import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import select

from hagent import db as hagent_db
from hagent import dispatcher, recovery, web
from hagent.cli import cli
from hagent.models import Agent, AppSetting, Issue, IssueStatus, Run, RunStatus, Runtime, RuntimeType, Workspace


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
        agents = {name: Agent(workspace_id=ws.id, runtime_id=runtime.id, name=name, instructions="x") for name in ("Coder", "Reviewer")}
        s.add_all(agents.values())
        s.commit()
        return {"project": project, **{name.lower(): a.id for name, a in agents.items()}}


def queue_run(world, title, agent="coder", priority="none", waited_minutes=0.0):
    """An issue with a PENDING run that has been waiting for `waited_minutes`."""
    with hagent_db.SessionLocal() as s:
        issue = Issue(project_id=world["project"], title=title, priority=priority, status=IssueStatus.IN_PROGRESS, assignee_agent_id=world[agent])
        s.add(issue)
        s.flush()
        run = Run(issue_id=issue.id, agent_id=world[agent], prompt=title, status=RunStatus.PENDING, created_at=datetime.now(timezone.utc) - timedelta(minutes=waited_minutes))
        s.add(run)
        s.commit()
        return issue.id, run.id


@pytest.fixture()
def gate(monkeypatch):
    """Replace run execution with a stub that claims the run and waits to be released."""
    dispatcher.stop_dispatch_loop()
    dispatcher._inflight.clear()
    monkeypatch.setattr(dispatcher, "_last_heartbeat", 0.0)
    started, release = [], threading.Event()

    def fake_process(run_id):
        with hagent_db.SessionLocal() as s:
            run = s.get(Run, run_id)
            run.status, run.started_at, run.owner_pid, run.owner_started = RunStatus.RUNNING, datetime.now(timezone.utc), *recovery.current_owner()
            s.commit()
        started.append((run_id, time.monotonic()))
        release.wait(5)
        with hagent_db.SessionLocal() as s:
            s.get(Run, run_id).status = RunStatus.COMPLETED
            s.commit()

    monkeypatch.setattr(dispatcher, "process_queued_run_by_id", fake_process)
    monkeypatch.setattr(dispatcher, "_executor", None)
    yield started, release
    dispatcher.stop_dispatch_loop()
    release.set()
    if dispatcher._executor:
        dispatcher._executor.shutdown(wait=True)
    dispatcher._inflight.clear()


def wait_for(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- aging ------------------------------------------------------------------

def test_waiting_raises_priority_one_level_per_aging_period():
    now = datetime.now(timezone.utc)
    period = timedelta(seconds=dispatcher.AGING_SECONDS)

    assert dispatcher.effective_rank("none", now, now) == 4
    assert dispatcher.effective_rank("none", now - period, now) == 3
    assert dispatcher.effective_rank("none", now - 3 * period, now) == 1
    assert dispatcher.effective_rank("none", now - 99 * period, now) == 0  # never better than urgent
    assert dispatcher.effective_rank("urgent", now - 5 * period, now) == 0
    assert dispatcher.effective_rank("high", now.replace(tzinfo=None), now) == 1  # naive database timestamps are UTC


def test_old_low_priority_work_is_not_starved_by_newer_urgent_work(world, gate):
    started, _ = gate
    _, old_run = queue_run(world, "old chore", priority="none", waited_minutes=60)
    _, urgent_run = queue_run(world, "fresh urgent", priority="urgent", waited_minutes=0)

    assert dispatcher.dispatch_pending() == 1  # one slot for the Coder

    assert wait_for(lambda: len(started) == 1)
    assert started[0][0] == old_run  # it has waited long enough to rank alongside urgent, and it was there first
    assert urgent_run not in [run_id for run_id, _ in started]


def test_without_waiting_urgent_still_goes_first(world, gate):
    started, _ = gate
    queue_run(world, "chore", priority="none", waited_minutes=1)
    _, urgent_run = queue_run(world, "urgent", priority="urgent", waited_minutes=0)

    dispatcher.dispatch_pending()

    assert wait_for(lambda: len(started) == 1) and started[0][0] == urgent_run


# --- change-triggered wake-up ----------------------------------------------

def test_a_run_queued_by_another_connection_starts_within_a_fraction_of_a_second(world, gate):
    started, _ = gate
    dispatcher.start_dispatch_loop()
    time.sleep(dispatcher.POLL_SECONDS * 3)  # settle

    queued_at = time.monotonic()
    queue_run(world, "wake me")
    assert wait_for(lambda: len(started) == 1, seconds=2)

    assert started[0][1] - queued_at < 1.0  # the old 3 s timer could take up to 3 s


def test_the_wake_up_loop_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HAGENT_DISPATCH_LOOP", "0")

    assert dispatcher.start_dispatch_loop() is None


def test_the_loop_starts_once_and_stops(world, gate):
    first, second = dispatcher.start_dispatch_loop(), dispatcher.start_dispatch_loop()

    assert first is second and first.is_alive()
    dispatcher.stop_dispatch_loop()
    assert not first.is_alive()


# --- heartbeat and the queue explanation -----------------------------------

def test_the_dispatcher_reports_that_it_is_alive_without_flooding_the_database(world, gate):
    with hagent_db.SessionLocal() as s:
        assert dispatcher.heartbeat_age(s) is None

    dispatcher.dispatch_pending()
    with hagent_db.SessionLocal() as s:
        first = s.get(AppSetting, dispatcher.HEARTBEAT_KEY).value
        assert dispatcher.heartbeat_age(s) < 5
    dispatcher.dispatch_pending()  # within the throttle window: no second write
    with hagent_db.SessionLocal() as s:
        assert s.get(AppSetting, dispatcher.HEARTBEAT_KEY).value == first


def test_queue_command_says_when_no_dispatcher_is_running(world, gate):
    queue_run(world, "stuck")

    output = run_cli("queue").output

    assert "NOT RUNNING" in output and "hagent serve" in output


def test_queue_command_explains_each_wait(world, gate):
    started, release = gate
    with hagent_db.SessionLocal() as s:
        s.get(Agent, world["coder"]).max_concurrent_tasks = 1
        s.commit()
    first, _ = queue_run(world, "first job", agent="coder", priority="high")
    queue_run(world, "second job", agent="coder", priority="none")
    queue_run(world, "review job", agent="reviewer")
    dispatcher.dispatch_pending()
    assert wait_for(lambda: len(started) == 2)
    queue_run(world, "third job", agent="coder", priority="none", waited_minutes=25)
    queue_run(world, "review two", agent="reviewer")

    output = run_cli("queue").output

    assert "dispatcher: running" in output
    assert "running (2" in output
    assert "waiting for Coder (1/1 busy: DEF-1)" in output  # names the run holding the slot
    assert "waiting for Reviewer (1/1 busy: DEF-3)" in output
    assert "(aged from none)" in output  # the 25-minute wait is visible


def test_queue_command_flags_orphaned_runs(world, gate):
    with hagent_db.SessionLocal() as s:
        issue = Issue(project_id=world["project"], title="stuck", status=IssueStatus.IN_PROGRESS, assignee_agent_id=world["coder"])
        s.add(issue)
        s.flush()
        s.add(Run(issue_id=issue.id, agent_id=world["coder"], prompt="x", status=RunStatus.RUNNING, started_at=datetime.now(timezone.utc)))
        s.commit()

    assert "its process is gone" in run_cli("queue").output


def test_queue_command_lists_runs_awaiting_approval(world, gate):
    with hagent_db.SessionLocal() as s:
        issue = Issue(project_id=world["project"], title="needs ok", status=IssueStatus.TODO, assignee_agent_id=world["coder"])
        s.add(issue)
        s.flush()
        s.add(Run(issue_id=issue.id, agent_id=world["coder"], prompt="x", status=RunStatus.WAITING_APPROVAL))
        s.commit()

    assert "awaiting approval: DEF-1 (Coder)" in run_cli("queue").output


# --- compression ------------------------------------------------------------

def test_large_responses_are_compressed_for_clients_that_accept_it():
    client = TestClient(web.app)

    response = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200 and response.headers["content-encoding"] == "gzip"
    assert response.num_bytes_downloaded < len(response.content) * 0.5  # bytes on the wire vs the decoded page


def test_clients_that_do_not_accept_compression_get_plain_bytes():
    response = TestClient(web.app).get("/static/style.css", headers={"Accept-Encoding": "identity"})

    assert "content-encoding" not in response.headers
