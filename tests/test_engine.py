import pytest

from hagent.adapters.base import RuntimeResult
from hagent.engine import DelegationLimitExceeded, execute_agent, queue_issue_run, run_issue
from hagent.models import Agent, Issue, IssueStatus, Project, Runtime, RunStatus, RuntimeType, Skill, Squad, SquadMember, TimelineEvent, Workspace


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


def test_successful_run_updates_last_used_for_assigned_skills(session, mocker):
    workspace, agent = _make_agent(session)
    skill = Skill(workspace_id=workspace.id, name='Research', content='Find evidence')
    agent.skills = [skill]
    session.commit()
    issue = _make_issue(session, workspace)
    mocker.patch('hagent.adapters.ollama.OllamaRuntime.run', return_value=RuntimeResult(output='done'))

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.COMPLETED
    assert skill.last_used_at is not None


def test_queue_issue_run_reuses_existing_active_run(session):
    workspace, agent = _make_agent(session)
    issue = _make_issue(session, workspace)

    first = queue_issue_run(session, issue, agent)
    second = queue_issue_run(session, issue, agent)

    assert first.id == second.id
    assert first.status == RunStatus.PENDING
    assert issue.status == IssueStatus.IN_PROGRESS


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


def test_direct_agent_execution_uses_its_configured_backup(session, mocker):
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
    run_adapter = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=[RuntimeError("session limit reached"), RuntimeResult(output="backup completed")],
    )

    result = execute_agent(agent, "Handle this outside the issue runner")

    assert result.output == "backup completed"
    assert run_adapter.call_count == 2
    assert agent.runtime_id == backup.id
    assert agent.backup_runtime_id != backup.id


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


def test_squad_agent_can_delegate_to_specialist_with_isolated_context(session, mocker):
    ws, lead = _make_agent(session)
    lead.name = "Holly"
    specialist = Agent(
        workspace_id=ws.id,
        runtime_id=lead.runtime_id,
        name="Researcher",
        description="Find and evaluate evidence",
        instructions="Be precise and return sourced findings.",
    )
    squad = Squad(workspace_id=ws.id, name="Product team")
    session.add_all([specialist, squad])
    session.flush()
    session.add_all([
        SquadMember(squad_id=squad.id, agent_id=lead.id, role="supervisor"),
        SquadMember(squad_id=squad.id, agent_id=specialist.id, role="researcher"),
    ])
    session.commit()
    tool_events = []
    seen = []

    def use_tools(_runtime, *, prompt, context, tools, tool_executor, **_kwargs):
        seen.append((prompt, context, tools))
        if prompt.startswith("Delegated by Holly"):
            # A child does not receive the supervisor as a callable colleague.
            assert all(
                tool["function"]["name"] != f"delegate_{lead.id.replace('-', '')}"
                for tool in tools or []
            )
            return RuntimeResult(output="Three useful findings")
        delegate = next(tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("delegate_"))
        findings = tool_executor(delegate, {"task": "Research the three most relevant facts."})
        return RuntimeResult(output=f"Final synthesis: {findings}")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=use_tools)
    result = execute_agent(lead, "Prepare a short evidence-based brief", record_tool=lambda *args: tool_events.append(args))

    assert result.output == "Final synthesis: Three useful findings"
    assert len(seen) == 2
    assert "Be precise" in seen[1][1]
    assert any(event[0] == "delegation_started:Researcher" for event in tool_events)
    assert any(event[0] == "delegation:Researcher" for event in tool_events)


def test_squad_delegation_cannot_bypass_teammates_approval_gate(session, mocker):
    ws, lead = _make_agent(session)
    specialist = Agent(
        workspace_id=ws.id,
        runtime_id=lead.runtime_id,
        name="Approvals required",
        require_run_approval=True,
    )
    squad = Squad(workspace_id=ws.id, name="Approval team")
    session.add_all([specialist, squad])
    session.flush()
    session.add_all([
        SquadMember(squad_id=squad.id, agent_id=lead.id),
        SquadMember(squad_id=squad.id, agent_id=specialist.id),
    ])
    session.commit()
    runtime_calls = []

    def try_delegate(_runtime, *, tools, tool_executor, **_kwargs):
        runtime_calls.append(tools)
        delegate = next(tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("delegate_"))
        message = tool_executor(delegate, {"task": "Perform the gated work."})
        assert "requires manual run approval" in message
        return RuntimeResult(output="I will request approval instead.")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=try_delegate)

    result = execute_agent(lead, "Delegate something", resolve_backup_runtime=lambda agent: None)

    assert result.output == "I will request approval instead."
    assert len(runtime_calls) == 1


def test_squad_delegation_budget_stops_additional_specialist_calls(session, mocker):
    ws, lead = _make_agent(session)
    lead.delegation_limit = 1
    researchers = [
        Agent(workspace_id=ws.id, runtime_id=lead.runtime_id, name="Researcher A"),
        Agent(workspace_id=ws.id, runtime_id=lead.runtime_id, name="Researcher B"),
    ]
    squad = Squad(workspace_id=ws.id, name="Budgeted team")
    session.add_all([*researchers, squad])
    session.flush()
    session.add_all([SquadMember(squad_id=squad.id, agent_id=member.id) for member in [lead, *researchers]])
    session.commit()
    calls = []
    tool_events = []

    def invoke(_runtime, *, prompt, tools, tool_executor, **_kwargs):
        calls.append(prompt)
        if prompt.startswith("Delegated by"):
            return RuntimeResult(output="First specialist completed")
        available = [tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("delegate_")]
        tool_executor(available[0], {"task": "First specialist task"})
        tool_executor(available[1], {"task": "Second specialist task"})
        return RuntimeResult(output="Should not get here")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=invoke)

    with pytest.raises(DelegationLimitExceeded, match=r"Delegation limit \(1\) reached"):
        execute_agent(lead, "Use both specialists", record_tool=lambda *args: tool_events.append(args))

    assert len(calls) == 2  # supervisor and first specialist only
    assert any(event[0].startswith("delegation_budget_exhausted:") for event in tool_events)
