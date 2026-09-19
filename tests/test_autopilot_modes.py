from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from click.testing import CliRunner
from sqlalchemy import select

from hagent import db as hagent_db
from hagent.cli import cli
from hagent.models import Agent, Autopilot, Issue, IssueStatus, Run, RunStatus, Runtime, RuntimeType, TimelineEvent, Workspace
from hagent.scheduler import sync_scheduler_jobs


def run_cli(*args):
    return CliRunner().invoke(cli, list(args))


def created_id(result):
    assert result.exit_code == 0, result.output
    return result.output.splitlines()[0].split()[-1]


@pytest.fixture()
def env(cli_db):
    project = created_id(run_cli("project", "create", "--name", "Ops"))
    with hagent_db.SessionLocal() as s:
        ws = s.scalar(select(Workspace))
        runtime = Runtime(workspace_id=ws.id, name="r", type=RuntimeType.OLLAMA, model="m", config_json="{}")
        s.add(runtime)
        s.flush()
        agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="Reporter", instructions="x")
        s.add(agent)
        s.commit()
        return {"project": project, "agent": agent.id}


def make_autopilot(env, *extra):
    return created_id(run_cli(
        "autopilot", "create", "--name", "Nightly", "--agent", "reporter", "--project", env["project"],
        "--mode", "create_issue", "--description", "Summarise yesterday's changes", "--issue-title-template", "Report {{date}}", *extra,
    ))


def issues():
    with hagent_db.SessionLocal() as s:
        return list(s.scalars(select(Issue).order_by(Issue.number)))


# --- create_issue mode ------------------------------------------------------

def test_each_firing_creates_an_issue_for_the_agent_and_queues_its_run(env):
    autopilot = make_autopilot(env)

    fired = run_cli("autopilot", "trigger", autopilot)
    run_cli("autopilot", "trigger", autopilot)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    first, second = issues()
    assert "completed" in fired.output and "DEF-1" in fired.output and "queued a run" in fired.output
    assert (first.title, second.title) == (f"Report {today}", f"Report {today}")
    assert first.description == "Summarise yesterday's changes" and first.assignee_agent_id == env["agent"]
    assert (first.number, second.number) == (1, 2)  # a fresh issue every time
    with hagent_db.SessionLocal() as s:
        runs = list(s.scalars(select(Run)))
        assert len(runs) == 2 and all(r.status == RunStatus.PENDING for r in runs)
        assert "Summarise yesterday's changes" in runs[0].prompt
        assert s.scalar(select(TimelineEvent.detail).where(TimelineEvent.event_type == "created_by_autopilot", TimelineEvent.issue_id == first.id)) == "Nightly"


def test_default_title_is_the_autopilot_name(env):
    autopilot = created_id(run_cli("autopilot", "create", "--name", "Weekly sweep", "--agent", "Reporter", "--project", env["project"], "--mode", "create_issue", "--description", "do it"))

    run_cli("autopilot", "trigger", autopilot)

    assert issues()[0].title == "Weekly sweep"


def test_runs_list_records_what_was_created(env):
    autopilot = make_autopilot(env)
    run_cli("autopilot", "trigger", autopilot)

    assert "Created DEF-1" in run_cli("autopilot", "runs", autopilot).output


def test_bad_configuration_is_rejected_up_front(env):
    common = ["autopilot", "create", "--name", "x", "--agent", "Reporter", "--mode", "create_issue"]

    no_project = run_cli(*common, "--description", "d")
    no_description = run_cli(*common, "--project", env["project"])
    bad_token = run_cli(*common, "--project", env["project"], "--description", "d", "--issue-title-template", "Hi {{name}}")
    missing_project = run_cli(*common, "--project", "nope", "--description", "d")
    unknown_agent = run_cli("autopilot", "create", "--name", "x", "--agent", "Nobody", "--mode", "filter")

    assert "needs --project" in no_project.output and "needs --description" in no_description.output
    assert "Unsupported template token" in bad_token.output and "{{name}}" in bad_token.output
    assert "not found" in missing_project.output and "not found" in unknown_agent.output
    assert run_cli("autopilot", "list").output.strip() == ""  # nothing half-created


def test_a_project_removed_after_creation_fails_the_run_visibly_instead_of_crashing(env):
    autopilot = make_autopilot(env)
    with hagent_db.SessionLocal() as s:
        s.get(Autopilot, autopilot).project_id = None
        s.commit()

    result = run_cli("autopilot", "trigger", autopilot)

    assert "failed" in result.output and "has no project" in result.output
    assert issues() == []


def test_update_can_switch_modes_and_revalidates(env):
    autopilot = created_id(run_cli("autopilot", "create", "--name", "A", "--agent", "Reporter"))

    bad = run_cli("autopilot", "update", autopilot, "--mode", "create_issue")
    good = run_cli("autopilot", "update", autopilot, "--mode", "create_issue", "--project", env["project"], "--description", "go")

    assert bad.exit_code != 0 and "needs --project" in bad.output
    assert good.exit_code == 0 and "mode: create_issue" in run_cli("autopilot", "get", autopilot).output


def test_existing_filter_mode_is_the_default_and_still_reports_get(env):
    autopilot = created_id(run_cli("autopilot", "create", "--name", "Legacy", "--agent", "Reporter"))

    assert "mode: filter" in run_cli("autopilot", "get", autopilot).output


# --- trigger timezones ------------------------------------------------------

def test_cron_triggers_accept_a_timezone_and_fire_in_it(env):
    autopilot = make_autopilot(env)

    added = run_cli("autopilot", "trigger-add", autopilot, "--cron", "0 9 * * *", "--timezone", "Asia/Kolkata", "--label", "morning")
    assert added.exit_code == 0, added.output
    assert "[Asia/Kolkata]  morning" in run_cli("autopilot", "get", autopilot).output

    scheduler = BackgroundScheduler()
    assert sync_scheduler_jobs(scheduler) == 1
    job = next(j for j in scheduler.get_jobs() if j.id.startswith("autopilot-trigger-"))
    assert str(job.trigger.timezone) == "Asia/Kolkata"


def test_unknown_timezones_and_bad_cron_are_rejected(env):
    autopilot = make_autopilot(env)

    unknown = run_cli("autopilot", "trigger-add", autopilot, "--cron", "0 9 * * *", "--timezone", "Mars/Olympus")
    bad_cron = run_cli("autopilot", "trigger-add", autopilot, "--cron", "not a cron", "--timezone", "Asia/Kolkata")

    assert unknown.exit_code != 0 and "Unknown timezone" in unknown.output
    assert bad_cron.exit_code != 0 and "Invalid cron" in bad_cron.output
    assert run_cli("autopilot", "trigger-list", autopilot).output.strip() == ""


def test_trigger_timezone_can_be_changed_and_cleared(env):
    autopilot = make_autopilot(env)
    added = run_cli("autopilot", "trigger-add", autopilot, "--cron", "0 9 * * *")
    trigger = added.output.split()[2]  # "Added trigger <id> (...) to autopilot ..."

    assert run_cli("autopilot", "trigger-update", trigger, "--timezone", "Europe/London").exit_code == 0
    assert "[Europe/London]" in run_cli("autopilot", "get", autopilot).output
    assert run_cli("autopilot", "trigger-update", trigger, "--timezone", "").exit_code == 0
    assert "[" not in run_cli("autopilot", "get", autopilot).output.split("trigger:")[1]


def test_the_run_dispatcher_is_registered_alongside_autopilot_jobs(env):
    scheduler = BackgroundScheduler()

    sync_scheduler_jobs(scheduler)

    assert "hagent-run-dispatcher" in {job.id for job in scheduler.get_jobs()}
