from hagent.adapters.base import RuntimeResult
from hagent.engine import run_issue
from hagent.models import Agent, Issue, IssueStatus, Project, Runtime, RunStatus, RuntimeType, Workspace


def _make_agent(session, runtime_type=RuntimeType.OLLAMA):
    ws = Workspace(name="ws")
    session.add(ws)
    session.commit()

    rt = Runtime(workspace_id=ws.id, name="rt", type=runtime_type, model="some-model", config_json="{}")
    session.add(rt)
    session.commit()

    agent = Agent(workspace_id=ws.id, runtime_id=rt.id, name="agent", instructions="be terse")
    session.add(agent)
    session.commit()
    return ws, agent


def _make_issue(session, workspace, status=IssueStatus.BACKLOG):
    project = Project(workspace_id=workspace.id, name="proj")
    session.add(project)
    session.commit()

    issue = Issue(project_id=project.id, title="hi", description="hi", status=status)
    session.add(issue)
    session.commit()
    return issue


def test_run_issue_success_persists_output_and_advances_status(session, mocker):
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws)

    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="pong"),
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.COMPLETED
    assert run.output == "pong"
    assert run.error is None
    assert run.started_at is not None
    assert run.finished_at is not None
    assert issue.status == IssueStatus.IN_REVIEW


def test_run_issue_failure_persists_error_and_leaves_issue_in_progress(session, mocker):
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws)

    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=RuntimeError("boom"),
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.FAILED
    assert run.output is None
    assert run.error == "boom"
    assert issue.status == IssueStatus.IN_PROGRESS


def test_run_issue_starts_pending_then_moves_through_states(session, mocker):
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws)

    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="ok"),
    )
    run = run_issue(session, issue, agent)
    assert run.status == RunStatus.COMPLETED
    assert issue.runs == [run]
