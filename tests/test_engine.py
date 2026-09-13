from hagent.adapters.base import RuntimeResult
from hagent.engine import run_issue
from hagent.models import Agent, Issue, IssueStatus, Project, Runtime, RunStatus, RuntimeType, TimelineEvent, Workspace


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


def test_primary_failure_immediately_hands_run_to_optional_backup(session, mocker):
    ws, agent = _make_agent(session)
    backup = Runtime(
        workspace_id=ws.id,
        name="backup",
        type=RuntimeType.OLLAMA,
        model="backup-model",
        config_json="{}",
    )
    session.add(backup)
    session.commit()
    agent.backup_runtime_id = backup.id
    session.commit()
    issue = _make_issue(session, ws)
    run_adapter = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=[RuntimeError("quota exhausted"), RuntimeResult(output="backup completed")],
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.COMPLETED
    assert run.output == "backup completed"
    assert run_adapter.call_count == 2
    events = session.query(TimelineEvent).filter_by(issue_id=issue.id, event_type="runtime_failover").all()
    assert len(events) == 1
    assert "quota exhausted" in events[0].detail
    assert "backup" in events[0].detail


def test_cancellation_does_not_launch_backup(session, mocker):
    ws, agent = _make_agent(session)
    backup = Runtime(workspace_id=ws.id, name="backup", type=RuntimeType.OLLAMA, model="backup-model")
    session.add(backup)
    session.commit()
    agent.backup_runtime_id = backup.id
    session.commit()
    issue = _make_issue(session, ws)
    run_adapter = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=RuntimeError("Run cancelled"),
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.FAILED
    assert run_adapter.call_count == 1

def test_terminal_enabled_agent_is_given_audited_command_tool(session, mocker, tmp_path):
    ws, agent = _make_agent(session)
    agent.terminal_enabled = True
    agent.terminal_working_directory = str(tmp_path)
    session.commit()
    issue = _make_issue(session, ws)

    def use_terminal(_runtime, *, tools, tool_executor, **_kwargs):
        names = [item["function"]["name"] for item in tools]
        assert "terminal_execute" in names
        result = tool_executor("terminal_execute", {"command": "Write-Output AGENT_TERMINAL_OK"})
        assert result["exit_code"] == 0
        assert "AGENT_TERMINAL_OK" in result["stdout"]
        return RuntimeResult(output="command completed")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=use_terminal)

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.COMPLETED
    assert run.output == "command completed"
    tool_events = session.query(TimelineEvent).filter_by(issue_id=issue.id, event_type="tool_call").all()
    assert any("terminal_execute" in event.detail for event in tool_events)