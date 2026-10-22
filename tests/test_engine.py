import pytest

from hagent.adapters.base import RuntimeResult
from hagent.engine import DelegationLimitExceeded, _run_verifier_pass, _verifier_verdict_passed, execute_agent, queue_issue_run, run_issue
from hagent.models import Agent, Comment, Issue, IssueStatus, Project, Run, Runtime, RunStatus, RuntimeType, Skill, Squad, SquadMember, TimelineEvent, Workspace


def test_verifier_verdict_uses_explicit_verdict_line():
    assert _verifier_verdict_passed("PASS\nThe injected text mentioned stale FAIL/PASS verdicts.")
    assert _verifier_verdict_passed("Review complete.\n\n- PASS: all requirements are met.")
    assert not _verifier_verdict_passed("FAIL: the requested test is missing.")
    assert not _verifier_verdict_passed("The response discusses both PASS and FAIL without deciding.")
    assert not _verifier_verdict_passed("I cannot give this a PASS until the microphone works.")


def test_verifier_pass_auto_approves(session, mocker):
    ws, agent = _make_agent(session)
    verifier = Agent(
        workspace_id=ws.id,
        runtime_id=agent.runtime_id,
        name="reviewer",
        instructions="verify",
    )
    session.add(verifier)
    session.flush()
    agent.verifier_agent_id = verifier.id
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    run = Run(
        issue_id=issue.id,
        agent_id=agent.id,
        prompt="do the work",
        status=RunStatus.COMPLETED,
        output="finished",
    )
    session.add(run)
    session.commit()
    mocker.patch(
        "hagent.engine.run_issue",
        return_value=mocker.Mock(output="PASS\nEverything works.", status=RunStatus.COMPLETED),
    )

    _run_verifier_pass(session, run, issue, "finished")

    session.refresh(issue)
    assert issue.status == IssueStatus.DONE
    assert session.query(TimelineEvent).filter_by(
        issue_id=issue.id,
        event_type="verification_passed",
    ).one()


def test_verifier_fail_requeues_a_fix_run(session, mocker):
    ws, agent = _make_agent(session)
    verifier = Agent(workspace_id=ws.id, runtime_id=agent.runtime_id, name="reviewer", instructions="verify")
    session.add(verifier)
    session.flush()
    agent.verifier_agent_id = verifier.id
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt="do the work", status=RunStatus.COMPLETED, output="finished")
    session.add(run)
    session.commit()
    run_issue_mock = mocker.patch(
        "hagent.engine.run_issue",
        return_value=mocker.Mock(output="FAIL: missing the edge case"),
    )

    _run_verifier_pass(session, run, issue, "finished")

    session.refresh(issue)
    assert issue.status == IssueStatus.IN_PROGRESS
    # Verification runs once; rework is queued for the dispatcher rather than recursive.
    assert run_issue_mock.call_count == 1
    assert session.query(Run).filter_by(issue_id=issue.id, status=RunStatus.PENDING).one()
    assert session.query(TimelineEvent).filter_by(issue_id=issue.id, event_type="verification_failed").one()


def test_verifier_gives_up_after_repeated_rejections(session, mocker):
    ws, agent = _make_agent(session)
    verifier = Agent(workspace_id=ws.id, runtime_id=agent.runtime_id, name="reviewer", instructions="verify")
    session.add(verifier)
    session.flush()
    agent.verifier_agent_id = verifier.id
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt="do the work", status=RunStatus.COMPLETED, output="finished")
    session.add(run)
    session.commit()
    for _ in range(3):
        session.add(TimelineEvent(issue_id=issue.id, event_type="verification_failed", detail="FAIL"))
    session.commit()
    run_issue_mock = mocker.patch(
        "hagent.engine.run_issue",
        return_value=mocker.Mock(output="FAIL: still broken"),
    )

    _run_verifier_pass(session, run, issue, "finished")

    session.refresh(issue)
    assert issue.status == IssueStatus.IN_REVIEW
    # Only the verifier's own review run - no fix run requeued once the cap is hit.
    assert run_issue_mock.call_count == 1
    assert session.query(TimelineEvent).filter_by(issue_id=issue.id, event_type="verification_gave_up").one()


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


def test_running_coordinator_hands_off_to_new_agent_without_review(session, mocker):
    ws, coordinator = _make_agent(session)
    worker = Agent(workspace_id=ws.id, runtime_id=coordinator.runtime_id, name="worker")
    session.add(worker)
    session.commit()
    issue = _make_issue(session, ws)
    issue.assignee_agent_id = coordinator.id
    session.commit()
    verifier = mocker.patch("hagent.engine._run_verifier_pass")

    def handoff(*args, **kwargs):
        issue.assignee_agent_id = worker.id
        session.commit()
        queued = queue_issue_run(session, issue, worker)
        assert queued.agent_id == worker.id
        assert queued.status == RunStatus.PENDING
        # Even direct/synchronous execution cannot overlap the coordinator.
        assert run_issue(session, issue, worker, _run=queued).status == RunStatus.PENDING
        return RuntimeResult(output="Worker queued, implementation is not done.")

    execute = mocker.patch("hagent.engine.execute_agent", side_effect=handoff)
    first = run_issue(session, issue, coordinator)
    assert first.status == RunStatus.COMPLETED
    assert issue.status == IssueStatus.IN_PROGRESS
    verifier.assert_not_called()
    queued = session.query(Run).filter_by(issue_id=issue.id, agent_id=worker.id).one()
    execute.side_effect = None
    execute.return_value = RuntimeResult(output="Implemented and tested")
    assert run_issue(session, issue, worker, _run=queued).status == RunStatus.COMPLETED
    assert issue.status == IssueStatus.IN_REVIEW
    assert execute.call_count == 2


def test_reassignment_replaces_stale_pending_run_and_snapshots_corrections(session):
    ws, original = _make_agent(session)
    worker = Agent(workspace_id=ws.id, runtime_id=original.runtime_id, name="worker")
    session.add(worker)
    session.commit()
    issue = _make_issue(session, ws)
    first = queue_issue_run(session, issue, original)
    session.add(Comment(issue_id=issue.id, author="owner", body="Use the existing local STT instead."))
    session.commit()
    second = queue_issue_run(session, issue, worker)
    session.refresh(first)
    assert first.status == RunStatus.CANCELLED
    assert second.agent_id == worker.id and second.status == RunStatus.PENDING
    assert "Use the existing local STT instead." in second.prompt
    assert queue_issue_run(session, issue, worker).id == second.id


def test_parent_waits_for_unstaged_child_then_resumes_once(session, mocker):
    from hagent.orchestration import after_issue_finished, requeue_idle_issues
    ws, agent = _make_agent(session)
    parent = _make_issue(session, ws)
    parent.assignee_agent_id = agent.id
    child = Issue(project_id=parent.project_id, parent_issue_id=parent.id,
                  title="implementation", status=IssueStatus.IN_PROGRESS)
    session.add(child)
    session.commit()
    mocker.patch("hagent.engine.execute_agent", return_value=RuntimeResult(output="Child still building"))
    verifier = mocker.patch("hagent.engine._run_verifier_pass")
    run_issue(session, parent, agent)
    assert parent.status == IssueStatus.BLOCKED
    verifier.assert_not_called()
    assert requeue_idle_issues(session) == []
    child.status = IssueStatus.DONE
    session.commit()
    after_issue_finished(session, child)
    after_issue_finished(session, child)
    assert session.query(Run).filter_by(issue_id=parent.id, status=RunStatus.PENDING).count() == 1


def test_finishing_run_does_not_reopen_human_approved_issue(session, mocker):
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws)

    def approve_while_running(*args, **kwargs):
        issue.status = IssueStatus.DONE
        session.commit()
        return RuntimeResult(output="Finished after the owner closed it")

    mocker.patch("hagent.engine.execute_agent", side_effect=approve_while_running)
    verifier = mocker.patch("hagent.engine._run_verifier_pass")
    run_issue(session, issue, agent)
    assert issue.status == IssueStatus.DONE
    verifier.assert_not_called()


def test_status_tool_reports_idle_assignment_instead_of_claiming_work(session):
    from hagent.engine import _tool_status_lookup
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws, status=IssueStatus.IN_PROGRESS)
    issue.assignee_agent_id = agent.id
    session.commit()
    text = _tool_status_lookup(agent, agent, {}, None)
    assert "Assignment is not execution" in text
    run = queue_issue_run(session, issue, agent)
    text = _tool_status_lookup(agent, agent, {}, None)
    assert "run: pending" in text and run.id in text


def test_worktree_setup_failure_is_recorded_instead_of_leaving_running_job(session, mocker):
    ws, agent = _make_agent(session)
    issue = _make_issue(session, ws)
    mocker.patch("hagent.engine._resolve_worktree", side_effect=OSError("Repository is unavailable"))
    execute = mocker.patch("hagent.engine.execute_agent")
    run = run_issue(session, issue, agent)
    assert run.status == RunStatus.FAILED
    assert "Repository is unavailable" in run.error
    execute.assert_not_called()


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


def test_backup_failure_hands_run_to_optional_failback(session, mocker):
    ws, agent = _make_agent(session)
    backup = Runtime(workspace_id=ws.id, name="backup", type=RuntimeType.OLLAMA, model="backup-model", config_json="{}")
    failback = Runtime(workspace_id=ws.id, name="failback", type=RuntimeType.OLLAMA, model="failback-model", config_json="{}")
    session.add_all([backup, failback])
    session.commit()
    agent.backup_runtime_id = backup.id
    agent.failback_runtime_id = failback.id
    session.commit()
    issue = _make_issue(session, ws)
    run_adapter = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=[RuntimeError("quota exhausted"), RuntimeError("backup also down"), RuntimeResult(output="failback completed")],
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.COMPLETED
    assert run.output == "failback completed"
    assert run_adapter.call_count == 3
    events = session.query(TimelineEvent).filter_by(issue_id=issue.id, event_type="runtime_failover").all()
    assert len(events) == 2
    assert "failback" in events[1].detail


def test_both_backup_and_failback_failing_combines_all_three_errors(session, mocker):
    ws, agent = _make_agent(session)
    backup = Runtime(workspace_id=ws.id, name="backup", type=RuntimeType.OLLAMA, model="backup-model", config_json="{}")
    failback = Runtime(workspace_id=ws.id, name="failback", type=RuntimeType.OLLAMA, model="failback-model", config_json="{}")
    session.add_all([backup, failback])
    session.commit()
    agent.backup_runtime_id = backup.id
    agent.failback_runtime_id = failback.id
    session.commit()
    issue = _make_issue(session, ws)
    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        side_effect=[RuntimeError("primary down"), RuntimeError("backup down"), RuntimeError("failback down")],
    )

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.FAILED
    assert "primary down" in run.error
    assert "backup down" in run.error
    assert "failback down" in run.error


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
