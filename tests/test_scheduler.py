from hagent.adapters.base import RuntimeResult
from hagent.models import Agent, Autopilot, AutopilotRun, Issue, IssueStatus, Project, Run, RunStatus, Runtime, RuntimeType, Workspace
from hagent.scheduler import find_matching_issues, resume_pending_runs, run_autopilot_once


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

    monkeypatch.setattr("hagent.scheduler.get_session", lambda **kwargs: _SessionCtx(session))

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

    monkeypatch.setattr("hagent.scheduler.get_session", lambda **kwargs: _SessionCtx(session))

    assert run_autopilot_once(autopilot.id) is None


def test_autopilot_records_partial_failure_truthfully(session, mocker, monkeypatch):
    ws, agent, project = _setup(session)
    session.add_all([
        Issue(project_id=project.id, title='succeeds', status=IssueStatus.TODO),
        Issue(project_id=project.id, title='fails', status=IssueStatus.TODO),
    ])
    session.flush()
    autopilot = Autopilot(workspace_id=ws.id, name='ap', agent_id=agent.id, project_id=project.id, filter_status=IssueStatus.TODO)
    session.add(autopilot)
    session.commit()
    mocker.patch(
        'hagent.adapters.ollama.OllamaRuntime.run',
        side_effect=[RuntimeResult(output='done'), RuntimeError('provider failed')],
    )
    monkeypatch.setattr('hagent.scheduler.get_session', lambda **kwargs: _SessionCtx(session))

    result = run_autopilot_once(autopilot.id)

    assert result.status == 'partial'
    assert '1 succeeded, 1 failed' in result.summary


def test_autopilot_records_runs_waiting_for_manual_approval(session, mocker, monkeypatch):
    ws, agent, project = _setup(session)
    agent.require_run_approval = True
    session.add(Issue(project_id=project.id, title="Review first", status=IssueStatus.TODO))
    autopilot = Autopilot(workspace_id=ws.id, name="approval autopilot", agent_id=agent.id, project_id=project.id, filter_status=IssueStatus.TODO)
    session.add(autopilot)
    session.commit()
    runtime_call = mocker.patch("hagent.adapters.ollama.OllamaRuntime.run")
    monkeypatch.setattr("hagent.scheduler.get_session", lambda **kwargs: _SessionCtx(session))

    result = run_autopilot_once(autopilot.id)

    runtime_call.assert_not_called()
    assert result.status == "partial"
    assert "1 awaiting approval" in result.summary
    assert session.query(Run).filter_by(status=RunStatus.WAITING_APPROVAL).count() == 1


def test_resume_pending_runs_schedules_only_not_started_runs(session, monkeypatch):
    ws, agent, project = _setup(session)
    issue = Issue(project_id=project.id, title="queued")
    session.add(issue)
    session.flush()
    pending = Run(issue_id=issue.id, agent_id=agent.id, prompt="do work", status=RunStatus.PENDING)
    running = Run(issue_id=issue.id, agent_id=agent.id, prompt="already started", status=RunStatus.RUNNING)
    session.add_all([pending, running])
    session.commit()

    class FakeScheduler:
        running = True

        def __init__(self):
            self.jobs = []

        def add_job(self, func, **kwargs):
            self.jobs.append((func, kwargs))

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("hagent.scheduler.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr("hagent.scheduler.get_session", lambda **kwargs: _SessionCtx(session))

    assert resume_pending_runs() == 1
    assert len(fake_scheduler.jobs) == 1
    func, job = fake_scheduler.jobs[0]
    assert func.__name__ == "process_queued_run_by_id"
    assert job["args"] == [pending.id]
    assert job["id"] == f"hagent-pending-run-{pending.id}"
    assert job["trigger"] == "date"


class _SessionCtx:
    """Wraps an existing test session as a context manager, mimicking get_session()."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False
