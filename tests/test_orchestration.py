import threading
import time
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from hagent import db as hagent_db
from hagent import dispatcher, orchestration
from hagent.adapters.base import RuntimeResult
from hagent.cli import cli
from hagent.models import Agent, Base, Issue, IssueStatus, Project, Run, RunStatus, Runtime, RuntimeType, TimelineEvent, Workspace
from hagent.tenancy import WorkspaceSession


def run_cli(*args):
    return CliRunner().invoke(cli, list(args))


def created_id(result):
    assert result.exit_code == 0, result.output
    return result.output.splitlines()[0].split()[-1]


@pytest.fixture()
def world(cli_db):
    """A project, and two agents (Coder, Reviewer) on a stub runtime, in the CLI's database."""
    project_id = created_id(run_cli("project", "create", "--name", "P"))
    with hagent_db.SessionLocal() as s:
        ws = s.scalar(select(Workspace))
        runtime = Runtime(workspace_id=ws.id, name="r", type=RuntimeType.OLLAMA, model="m", config_json="{}")
        s.add(runtime)
        s.flush()
        coder = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="Coder", instructions="x")
        reviewer = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="Reviewer", instructions="x")
        s.add_all([coder, reviewer])
        s.commit()
        return {"project": project_id, "coder": coder.id, "reviewer": reviewer.id, "workspace": ws.id}


def issue(world, title="t", *extra):
    return created_id(run_cli("issue", "create", "--project", world["project"], "--title", title, *extra))


def runs_of(issue_id):
    with hagent_db.SessionLocal() as s:
        return list(s.scalars(select(Run).where(Run.issue_id == issue_id).order_by(Run.created_at)))


def status_of(issue_id):
    with hagent_db.SessionLocal() as s:
        return s.get(Issue, issue_id).status


# --- issue keys -------------------------------------------------------------

def test_issues_get_sequential_keys_and_can_be_addressed_by_them(world):
    first, second = issue(world, "one"), issue(world, "two")

    assert run_cli("issue", "get", "DEF-2").output.splitlines()[0] == f"id: {second}"
    assert run_cli("issue", "get", "def-1").output.splitlines()[0] == f"id: {first}"  # prefix is case-insensitive
    assert run_cli("issue", "get", "DEF-99").exit_code != 0
    assert "DEF-1" in run_cli("issue", "list").output and "DEF-2" in run_cli("issue", "list").output


def test_numbers_stay_unique_when_many_issues_are_inserted_in_one_flush(world):
    with hagent_db.SessionLocal() as s:
        s.add_all([Issue(project_id=world["project"], title=str(n)) for n in range(5)])
        s.commit()
        numbers = sorted(n for n in s.scalars(select(Issue.number)))

    assert numbers == [1, 2, 3, 4, 5]


def test_a_workspace_prefix_overrides_the_derived_one(world):
    with hagent_db.SessionLocal() as s:
        s.get(Workspace, world["workspace"]).issue_prefix = "ops"
        s.commit()
    new = issue(world)

    assert run_cli("issue", "get", "OPS-1").output.splitlines()[0] == f"id: {new}"


# --- auto-start -------------------------------------------------------------

def test_assigning_an_issue_starts_the_agent_unless_told_not_to(world):
    started, quiet = issue(world, "a"), issue(world, "b")

    result = run_cli("issue", "assign", started, "--agent", "Coder")
    assert "Queued run" in result.output
    run = runs_of(started)[0]
    assert (run.status, run.agent_id, status_of(started)) == (RunStatus.PENDING, world["coder"], IssueStatus.IN_PROGRESS)

    assert "Queued run" not in run_cli("issue", "assign", quiet, "--agent", "coder", "--no-start").output  # names are case-insensitive
    assert runs_of(quiet) == []


def test_reassigning_an_issue_with_an_active_run_does_not_double_queue(world):
    target = issue(world)
    run_cli("issue", "assign", target, "--agent", "Coder")

    run_cli("issue", "assign", target, "--agent", "Coder")

    assert len(runs_of(target)) == 1


def test_closed_issues_are_not_started_and_unassign_works(world):
    done = issue(world, "d", "--status", "done")

    assert "Queued" not in run_cli("issue", "assign", done, "--agent", "Coder").output
    assert runs_of(done) == []
    assert "unassigned" in run_cli("issue", "assign", done, "--unassign").output
    assert run_cli("issue", "assign", done).exit_code != 0  # needs --agent or --unassign


def test_moving_to_todo_starts_the_agent_and_other_moves_do_not(world):
    target = issue(world, "x", "--assignee", "Coder")  # backlog: created but not started
    assert runs_of(target) == []

    assert "Queued run" not in run_cli("issue", "status", target, "in_review").output
    assert "Queued run" in run_cli("issue", "status", target, "todo").output
    assert len(runs_of(target)) == 1


def test_status_no_start_and_unassigned_issues_start_nothing(world):
    assigned = issue(world, "a", "--assignee", "Coder")
    unassigned = issue(world, "b")

    run_cli("issue", "status", assigned, "todo", "--no-start")
    run_cli("issue", "status", unassigned, "todo")

    assert runs_of(assigned) == [] and runs_of(unassigned) == []


def test_create_with_assignee_and_todo_starts_it_immediately(world):
    target = issue(world, "go", "--assignee", "Reviewer", "--status", "todo")

    assert runs_of(target)[0].agent_id == world["reviewer"]
    assert runs_of(issue(world, "held", "--assignee", "Reviewer", "--status", "todo", "--no-start")) == []


def test_update_can_reassign_and_start_in_one_step(world):
    target = issue(world)

    result = run_cli("issue", "update", target, "--assignee", "Coder", "--priority", "high", "--due-date", "2030-01-31")

    assert "Queued run" in result.output
    assert "priority: high" in run_cli("issue", "get", target).output and "due_date: 2030-01-31" in run_cli("issue", "get", target).output


# --- fields, validation, listing -------------------------------------------

def test_bad_dates_stage_without_parent_and_unknown_agents_are_rejected(world):
    assert "YYYY-MM-DD" in run_cli("issue", "create", "--project", world["project"], "--title", "x", "--due-date", "31/01/2030").output
    assert "needs --parent" in run_cli("issue", "create", "--project", world["project"], "--title", "x", "--stage", "1").output
    assert "not found" in run_cli("issue", "create", "--project", world["project"], "--title", "x", "--assignee", "Nobody").output
    assert "not found" in run_cli("issue", "create", "--project", world["project"], "--title", "x", "--parent", "DEF-404").output


def test_list_filters_sorts_and_paginates(world):
    low = issue(world, "low", "--priority", "low")
    urgent = issue(world, "urgent", "--priority", "urgent", "--status", "todo")
    plain = issue(world, "plain")

    def ids(*args):
        return [line.split()[0] for line in run_cli("issue", "list", *args).output.splitlines()]

    assert ids("--status", "todo") == [urgent]
    assert ids("--priority", "low", "--priority", "urgent") == [low, urgent]
    assert ids("--sort", "priority") == [urgent, low, plain]
    assert ids("--sort", "created", "--direction", "desc", "--limit", "2") == [plain, urgent]
    assert ids("--limit", "1", "--offset", "1") == [urgent]
    assert ids("--assignee", "Coder") == []


# --- stage barriers ---------------------------------------------------------

def make_family(world):
    parent = issue(world, "Ship feature", "--assignee", "Coder", "--status", "in_review")
    a = issue(world, "design", "--parent", parent, "--stage", "1")
    b = issue(world, "schema", "--parent", parent, "--stage", "1")
    c = issue(world, "tests", "--parent", parent, "--stage", "2")
    loose = issue(world, "loose", "--parent", parent)
    return parent, a, b, c, loose


def test_children_are_listed_by_stage(world):
    parent, *_ = make_family(world)

    output = run_cli("issue", "children", parent).output

    assert output.index("stage 1:") < output.index("stage 2:") < output.index("unstaged:")


def test_parent_agent_wakes_only_when_the_whole_stage_has_finished(world):
    parent, a, b, c, loose = make_family(world)

    run_cli("issue", "status", a, "done")
    assert runs_of(parent) == []  # half of stage 1

    run_cli("issue", "status", b, "done")
    woken = runs_of(parent)
    assert len(woken) == 1 and woken[0].agent_id == world["coder"]
    assert "Stage 1" in woken[0].prompt and "design" in woken[0].prompt and "schema" in woken[0].prompt and "tests" not in woken[0].prompt


def test_unstaged_and_cancelled_sub_issues_behave_correctly(world):
    parent, a, b, c, loose = make_family(world)

    run_cli("issue", "status", loose, "done")
    assert runs_of(parent) == []  # unstaged children never wake the parent

    run_cli("issue", "status", c, "cancelled")  # cancelled counts as finished for its stage
    assert "Stage 2" in runs_of(parent)[0].prompt


def test_a_stage_that_finishes_while_the_parent_is_busy_fires_after_it_finishes(world):
    parent, a, b, c, loose = make_family(world)
    run_cli("issue", "status", a, "done")
    run_cli("issue", "status", b, "done")  # wakes parent for stage 1
    run_cli("issue", "status", c, "done")  # stage 2 done, but the parent's run is still active
    assert len(runs_of(parent)) == 1

    with hagent_db.SessionLocal() as s:
        first = s.scalar(select(Run).where(Run.issue_id == parent))
        first.status = RunStatus.COMPLETED
        s.commit()
        orchestration.after_issue_finished(s, s.get(Issue, parent))
        orchestration.after_issue_finished(s, s.get(Issue, parent))  # asking again must not re-fire

    prompts = [r.prompt for r in runs_of(parent)]
    assert len(prompts) == 2 and "Stage 2" in prompts[1]


def test_barrier_needs_an_assigned_agent_and_an_open_parent(world):
    unassigned = issue(world, "p")
    child = issue(world, "c", "--parent", unassigned, "--stage", "1")
    run_cli("issue", "status", child, "done")
    assert runs_of(unassigned) == []

    closed = issue(world, "closed", "--assignee", "Coder", "--status", "done")
    child = issue(world, "c2", "--parent", closed, "--stage", "1")
    run_cli("issue", "status", child, "done")
    assert runs_of(closed) == []


# --- dispatcher -------------------------------------------------------------

@pytest.fixture()
def gate(monkeypatch):
    """Replace run execution with a stub that claims the run and waits to be released."""
    dispatcher._inflight.clear()
    started, release = [], threading.Event()

    def fake_process(run_id):
        with hagent_db.SessionLocal() as s:
            run = s.get(Run, run_id)
            run.status, run.started_at = RunStatus.RUNNING, datetime.now(timezone.utc)
            s.commit()
        started.append(run_id)
        release.wait(5)
        with hagent_db.SessionLocal() as s:
            s.get(Run, run_id).status = RunStatus.COMPLETED
            s.commit()

    monkeypatch.setattr(dispatcher, "process_queued_run_by_id", fake_process)
    monkeypatch.setattr(dispatcher, "_executor", None)
    yield started, release
    release.set()
    if dispatcher._executor:
        dispatcher._executor.shutdown(wait=True)
    dispatcher._inflight.clear()


def wait_for(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_an_agent_runs_one_task_at_a_time_by_default(world, gate):
    started, release = gate
    first, second = issue(world, "a"), issue(world, "b")
    run_cli("issue", "assign", first, "--agent", "Coder")
    run_cli("issue", "assign", second, "--agent", "Coder")

    assert dispatcher.dispatch_pending() == 1
    assert wait_for(lambda: len(started) == 1)
    assert dispatcher.dispatch_pending() == 0  # the second waits for capacity

    release.set()
    assert wait_for(lambda: all(r.status == RunStatus.COMPLETED for r in runs_of(first)))
    assert dispatcher.dispatch_pending() == 1
    assert wait_for(lambda: len(started) == 2)


def test_max_concurrent_tasks_allows_parallel_runs(world, gate):
    started, _ = gate
    with hagent_db.SessionLocal() as s:
        s.get(Agent, world["coder"]).max_concurrent_tasks = 2
        s.commit()
    for name in "abc":
        run_cli("issue", "assign", issue(world, name), "--agent", "Coder")

    assert dispatcher.dispatch_pending() == 2
    assert wait_for(lambda: len(started) == 2)
    assert dispatcher.dispatch_pending() == 0


def test_different_agents_run_in_parallel_and_urgent_work_goes_first(world, gate):
    started, _ = gate
    relaxed = issue(world, "relaxed")
    urgent = issue(world, "urgent", "--priority", "urgent")
    other = issue(world, "other")
    run_cli("issue", "assign", relaxed, "--agent", "Coder")
    run_cli("issue", "assign", urgent, "--agent", "Coder")  # queued after `relaxed`, but more important
    run_cli("issue", "assign", other, "--agent", "Reviewer")

    assert dispatcher.dispatch_pending() == 2  # one per agent
    assert wait_for(lambda: len(started) == 2)

    coder_run = next(r for r in runs_of(urgent) if r.agent_id == world["coder"])
    assert coder_run.status == RunStatus.RUNNING and runs_of(relaxed)[0].status == RunStatus.PENDING


def test_the_global_parallel_limit_is_respected(world, gate, monkeypatch):
    started, _ = gate
    monkeypatch.setenv("HAGENT_MAX_PARALLEL_RUNS", "1")
    run_cli("issue", "assign", issue(world, "a"), "--agent", "Coder")
    run_cli("issue", "assign", issue(world, "b"), "--agent", "Reviewer")

    assert dispatcher.dispatch_pending() == 1


def test_dispatch_with_nothing_queued_is_a_no_op(world, gate):
    assert dispatcher.dispatch_pending() == 0


def test_a_queued_run_executes_end_to_end_through_the_real_engine(world, mocker):
    dispatcher._inflight.clear()
    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="all done"))
    target = issue(world, "real work", "--assignee", "Coder", "--status", "todo")

    assert dispatcher.dispatch_pending() == 1
    assert wait_for(lambda: runs_of(target)[0].status == RunStatus.COMPLETED, seconds=10)

    assert runs_of(target)[0].output == "all done" and status_of(target) == IssueStatus.IN_REVIEW
    dispatcher._executor.shutdown(wait=True)
    dispatcher._executor = None


def test_finishing_a_run_through_the_engine_wakes_the_parent_agent(world, mocker):
    dispatcher._inflight.clear()
    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="child result"))
    parent = issue(world, "Parent", "--assignee", "Reviewer", "--status", "in_review")
    child = issue(world, "Child", "--parent", parent, "--stage", "1", "--assignee", "Coder", "--status", "todo")

    dispatcher.dispatch_pending()
    assert wait_for(lambda: runs_of(child)[0].status == RunStatus.COMPLETED, seconds=10)

    assert wait_for(lambda: len(runs_of(parent)) == 1)
    assert "child result" in runs_of(parent)[0].prompt
    dispatcher._executor.shutdown(wait=True)
    dispatcher._executor = None


# --- migration of an existing database -------------------------------------

def test_existing_databases_gain_the_new_columns_numbers_and_indexes(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    new_columns = {
        "issues": ["priority", "start_date", "due_date", "stage", "number"], "agents": ["max_concurrent_tasks"],
        "workspaces": ["issue_prefix"], "comments": ["parent_comment_id", "resolved"],
        "autopilots": ["mode", "description", "issue_title_template"], "autopilot_triggers": ["timezone", "label"],
    }
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS ix_runs_status"))
        for table, columns in new_columns.items():
            for column in columns:
                connection.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))
        connection.execute(text("INSERT INTO workspaces (id, name, created_at) VALUES ('w1', 'Acme', '2026-01-01')"))
        connection.execute(text("INSERT INTO projects (id, workspace_id, name, description, status, created_at) VALUES ('p1', 'w1', 'P', '', 'ACTIVE', '2026-01-01')"))
        for n, day in enumerate(("2026-01-03", "2026-01-01", "2026-01-02")):
            connection.execute(text(f"INSERT INTO issues (id, project_id, title, description, status, position, created_at, updated_at) VALUES ('i{n}', 'p1', 't{n}', '', 'BACKLOG', 0, '{day}', '{day}')"))
    monkeypatch.setattr(hagent_db, "engine", engine)

    hagent_db._ensure_schema()

    inspector = inspect(engine)
    for table, columns in new_columns.items():
        assert set(columns) <= {c["name"] for c in inspector.get_columns(table)}, table
    assert "ix_runs_status" in {i["name"] for i in inspector.get_indexes("runs")}
    with engine.connect() as connection:
        numbers = dict(connection.execute(text("SELECT id, number FROM issues")).all())
    assert numbers == {"i1": 1, "i2": 2, "i0": 3}  # oldest first
    hagent_db._ensure_schema()  # idempotent
