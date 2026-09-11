from hagent.adapters.base import RuntimeResult
from hagent.models import Agent, Autopilot, AutopilotRun, Issue, IssueStatus, Project, Runtime, RuntimeType, Workspace
from hagent.scheduler import find_matching_issues, run_autopilot_once


def _setup(session):
    ws = Workspace(name="ws")
    session.add(ws)
    session.commit()

    rt = Runtime(workspace_id=ws.id, name="rt", type=RuntimeType.OLLAMA, model="m", config_json="{}")
    session.add(rt)
    session.commit()

    agent = Agent(workspace_id=ws.id, runtime_id=rt.id, name="agent")
    session.add(agent)
    session.commit()

    project = Project(workspace_id=ws.id, name="proj")
    session.add(project)
    session.commit()

    return ws, agent, project


def test_find_matching_issues_filters_by_project_and_status(session):
    ws, agent, project = _setup(session)
    other_project = Project(workspace_id=ws.id, name="other")
    session.add(other_project)
    session.commit()

    matching = Issue(project_id=project.id, title="a", status=IssueStatus.TODO)
    wrong_status = Issue(project_id=project.id, title="b", status=IssueStatus.DONE)
    wrong_project = Issue(project_id=other_project.id, title="c", status=IssueStatus.TODO)
    session.add_all([matching, wrong_status, wrong_project])
    session.commit()

    autopilot = Autopilot(workspace_id=ws.id, name="ap", agent_id=agent.id, project_id=project.id, filter_status=IssueStatus.TODO)
    session.add(autopilot)
    session.commit()

    results = find_matching_issues(session, autopilot)

    assert results == [matching]


def test_run_autopilot_once_runs_matching_issues_and_records_run(session, mocker, monkeypatch):
    ws, agent, project = _setup(session)
    issue = Issue(project_id=project.id, title="a", status=IssueStatus.TODO)
    session.add(issue)
    session.commit()

    autopilot = Autopilot(workspace_id=ws.id, name="ap", agent_id=agent.id, project_id=project.id, filter_status=IssueStatus.TODO)
    session.add(autopilot)
    session.commit()

    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="done"),
    )

    monkeypatch.setattr("hagent.scheduler.get_session", lambda: _SessionCtx(session))

    result = run_autopilot_once(autopilot.id)

    assert result is not None
    assert result.status == "completed"
    assert "1 matching issue" in result.summary
    assert session.query(AutopilotRun).count() == 1


def test_run_autopilot_once_returns_none_for_disabled_autopilot(session, monkeypatch):
    ws, agent, project = _setup(session)
    autopilot = Autopilot(workspace_id=ws.id, name="ap", agent_id=agent.id, enabled=False)
    session.add(autopilot)
    session.commit()

    monkeypatch.setattr("hagent.scheduler.get_session", lambda: _SessionCtx(session))

    assert run_autopilot_once(autopilot.id) is None


class _SessionCtx:
    """Wraps an existing test session as a context manager, mimicking get_session()."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False
