import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from hagent import db as hagent_db
from hagent import dispatcher, recovery
from hagent.adapters.base import RuntimeResult
from hagent.cli import cli
from hagent.models import Agent, Issue, IssueStatus, Run, RunStatus, Runtime, RuntimeType, TimelineEvent, Workspace
from hagent.recovery import RESUME_MARKER, recover_interrupted_runs


def run_cli(*args):
    return CliRunner().invoke(cli, list(args))


@pytest.fixture()
def world(cli_db):
    project = run_cli("project", "create", "--name", "P").output.split()[-1]
    with hagent_db.SessionLocal() as s:
        ws = s.scalar(select(Workspace))
        runtime = Runtime(workspace_id=ws.id, name="r", type=RuntimeType.OLLAMA, model="m", config_json="{}")
        s.add(runtime)
        s.flush()
        agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="Coder", instructions="x")
        s.add(agent)
        s.commit()
        return {"project": project, "agent": agent.id}


def interrupted_run(world, *, owner_pid=None, owner_started=None, prompt="build the login page", status=IssueStatus.IN_PROGRESS, resumed_from=None):
    """An issue with a run left RUNNING, as if the application had closed mid-way."""
    with hagent_db.SessionLocal() as s:
        issue = Issue(project_id=world["project"], title="Login", description=prompt, status=status, assignee_agent_id=world["agent"])
        s.add(issue)
        s.flush()
        run = Run(issue_id=issue.id, agent_id=world["agent"], prompt=prompt, status=RunStatus.RUNNING, started_at=datetime.now(timezone.utc),
                  owner_pid=owner_pid, owner_started=owner_started, resumed_from=resumed_from)
        s.add(run)
        s.commit()
        return issue.id, run.id


def runs_of(issue_id):
    with hagent_db.SessionLocal() as s:
        return list(s.scalars(select(Run).where(Run.issue_id == issue_id).order_by(Run.created_at, Run.id)))


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# --- deciding whether a run was interrupted --------------------------------

def test_an_orphaned_run_is_failed_and_resumed_as_a_new_pending_run(world):
    issue_id, old_id = interrupted_run(world)

    queued = recover_interrupted_runs()

    old, new = runs_of(issue_id)
    assert queued == [new.id]
    assert old.status == RunStatus.FAILED and "Interrupted" in old.error and old.finished_at is not None
    assert (new.status, new.resumed_from, new.agent_id) == (RunStatus.PENDING, old.id, old.agent_id)
    assert new.prompt.startswith("build the login page") and RESUME_MARKER in new.prompt
    with hagent_db.SessionLocal() as s:
        assert s.get(Issue, issue_id).status == IssueStatus.IN_PROGRESS
        assert s.scalar(select(TimelineEvent.detail).where(TimelineEvent.issue_id == issue_id, TimelineEvent.event_type == "run_resumed"))


def test_runs_owned_by_a_live_process_are_left_alone(world):
    pid, started = recovery.current_owner()
    issue_id, _ = interrupted_run(world, owner_pid=pid, owner_started=started)

    assert recover_interrupted_runs() == []
    assert [r.status for r in runs_of(issue_id)] == [RunStatus.RUNNING]


def test_a_dead_owner_is_recovered(world):
    issue_id, _ = interrupted_run(world, owner_pid=dead_pid(), owner_started=time.time())

    assert len(recover_interrupted_runs()) == 1


def test_a_reused_pid_does_not_protect_a_dead_run(world):
    pid, started = recovery.current_owner()
    issue_id, _ = interrupted_run(world, owner_pid=pid, owner_started=started - 3600)  # same pid, different process

    assert len(recover_interrupted_runs()) == 1


def test_recovery_is_idempotent(world):
    interrupted_run(world)

    assert len(recover_interrupted_runs()) == 1
    assert recover_interrupted_runs() == []


def test_closed_issues_are_not_resumed(world):
    issue_id, _ = interrupted_run(world, status=IssueStatus.CANCELLED)

    assert recover_interrupted_runs() == []
    assert [r.status for r in runs_of(issue_id)] == [RunStatus.FAILED]  # still cleaned up, so it stops blocking


def test_an_issue_that_keeps_being_interrupted_is_blocked_not_retried_forever(world):
    issue_id, run_id = interrupted_run(world)
    for _ in range(recovery.MAX_RESUMES):
        new_id = recover_interrupted_runs()[0]
        with hagent_db.SessionLocal() as s:  # the resumed run starts, then the app closes again
            s.get(Run, new_id).status = RunStatus.RUNNING
            s.commit()

    assert recover_interrupted_runs() == []
    with hagent_db.SessionLocal() as s:
        assert s.get(Issue, issue_id).status == IssueStatus.BLOCKED
        assert s.scalar(select(TimelineEvent.id).where(TimelineEvent.issue_id == issue_id, TimelineEvent.event_type == "run_abandoned"))
    assert len(runs_of(issue_id)) == recovery.MAX_RESUMES + 1


def test_the_resume_note_does_not_stack_up(world):
    issue_id, _ = interrupted_run(world)
    new_id = recover_interrupted_runs()[0]
    with hagent_db.SessionLocal() as s:
        s.get(Run, new_id).status = RunStatus.RUNNING
        s.commit()

    recover_interrupted_runs()

    assert runs_of(issue_id)[-1].prompt.count(RESUME_MARKER) == 1


# --- the engine records ownership; the dispatcher is unblocked -------------

def test_the_engine_stamps_the_run_with_its_owner_process(world, mocker):
    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="ok"))
    issue_id = run_cli("issue", "create", "--project", world["project"], "--title", "t", "--assignee", "Coder", "--status", "todo").output.split()[2]

    dispatcher._inflight.clear()
    dispatcher.dispatch_pending()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and runs_of(issue_id)[0].status != RunStatus.COMPLETED:
        time.sleep(0.05)
    dispatcher._executor.shutdown(wait=True)
    dispatcher._executor = None

    run = runs_of(issue_id)[0]
    assert (run.owner_pid, run.owner_started) == recovery.current_owner()


def test_closing_and_reopening_continues_the_work_through_the_real_engine(world, mocker):
    """The user's scenario: an agent is mid-task, the app closes, and on the next start it carries on."""
    seen_prompts = []

    def fake_run(self, prompt, context="", tools=None, tool_executor=None):
        seen_prompts.append(prompt)
        return RuntimeResult(output="finished the login page")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", fake_run)
    issue_id, _ = interrupted_run(world)  # ... the application closed here ...

    dispatcher._inflight.clear()
    recover_interrupted_runs()  # ... and this is what start-up does
    assert dispatcher.dispatch_pending() == 1
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and runs_of(issue_id)[-1].status != RunStatus.COMPLETED:
        time.sleep(0.05)
    dispatcher._executor.shutdown(wait=True)
    dispatcher._executor = None

    final = runs_of(issue_id)[-1]
    assert final.status == RunStatus.COMPLETED and final.output == "finished the login page"
    assert RESUME_MARKER in seen_prompts[0] and "build the login page" in seen_prompts[0]
    with hagent_db.SessionLocal() as s:
        assert s.get(Issue, issue_id).status == IssueStatus.IN_REVIEW


def test_a_stuck_running_run_no_longer_blocks_its_agent_forever(world, mocker):
    """The real bug on this machine: stale RUNNING rows from a closed app kept agents from ever being dispatched."""
    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="ok"))
    interrupted_run(world)  # occupies the agent's single slot
    waiting = run_cli("issue", "create", "--project", world["project"], "--title", "next", "--assignee", "Coder", "--status", "todo").output.split()[2]

    dispatcher._inflight.clear()
    assert dispatcher.dispatch_pending() == 0  # blocked by the stale row

    recover_interrupted_runs()
    assert dispatcher.dispatch_pending() >= 1
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(r.status == RunStatus.COMPLETED for r in runs_of(waiting)):
        time.sleep(0.05)
    dispatcher._executor.shutdown(wait=True)
    dispatcher._executor = None
    assert any(r.status in (RunStatus.RUNNING, RunStatus.COMPLETED) for r in runs_of(waiting))


def test_recovery_runs_at_startup_and_every_minute(world):
    from apscheduler.schedulers.background import BackgroundScheduler

    from hagent.scheduler import sync_scheduler_jobs

    scheduler = BackgroundScheduler()
    sync_scheduler_jobs(scheduler)

    assert "hagent-run-recovery" in {job.id for job in scheduler.get_jobs()}
