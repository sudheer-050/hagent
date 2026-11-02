import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hagent import web
from hagent.adapters.base import RuntimeResult
from hagent.engine import _run_verifier_pass, run_issue
from hagent.models import Agent, IssueStatus, Run, RunStatus, TimelineEvent
from hagent.orchestration import completion_blocker, stage_child_finished
from tests.test_engine import _make_agent, _make_issue


def test_local_cross_origin_mutation_and_websocket_are_refused():
    client = TestClient(web.app)
    assert client.post('/api/nothing', headers={'Origin': 'https://evil.example'}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/terminal', headers={'Origin': 'https://evil.example'}):
            pass
    assert client.get('/api/nothing', headers={'Host': 'evil.example'},
                      ).status_code == 404  # fake TestClient peer; real peer tested below
    assert TestClient(web.app, client=('127.0.0.1', 10)).get(
        '/api/nothing', headers={'Host': 'evil.example'}).status_code == 403


def test_completion_requires_current_verification_and_no_unfinished_children(session):
    ws, agent = _make_agent(session)
    agent.verifier_agent_id = 'reviewer'
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    issue.assignee_agent_id = agent.id
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt='work', status=RunStatus.COMPLETED)
    session.add(run); session.commit()
    assert completion_blocker(session, issue)
    assert not stage_child_finished(issue)
    session.add(TimelineEvent(issue_id=issue.id, event_type='verification_passed', detail='PASS'))
    session.commit()
    assert completion_blocker(session, issue) is None
    child = _make_issue(session, ws)
    child.parent_issue_id = issue.id
    session.commit()
    assert 'Child' in completion_blocker(session, issue)


def test_approval_gated_verifier_resumes_and_applies_exactly_once(session, mocker, tmp_path):
    ws, agent = _make_agent(session)
    verifier = Agent(workspace_id=ws.id, runtime_id=agent.runtime_id, name='Reviewer',
                     require_run_approval=True, terminal_enabled=True)
    session.add(verifier); session.flush()
    agent.verifier_agent_id = verifier.id
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt='work', status=RunStatus.COMPLETED,
              output='ready', working_directory=str(tmp_path))
    session.add(run); session.commit()
    _run_verifier_pass(session, run, issue, 'ready')
    review = session.query(Run).filter_by(review_of_run_id=run.id).one()
    assert review.status == RunStatus.WAITING_APPROVAL
    assert issue.status == IssueStatus.IN_REVIEW
    assert review.working_directory == str(tmp_path)
    assert session.query(TimelineEvent).filter_by(event_type='verification_failed').count() == 0
    review.status = RunStatus.PENDING
    session.commit()
    execute = mocker.patch('hagent.engine.execute_agent', return_value=RuntimeResult(output='PASS: checked files'))
    run_issue(session, review.issue, verifier, _run=review)
    assert execute.call_args.kwargs['working_directory_override'] == str(tmp_path)
    assert issue.status == IssueStatus.DONE
    _run_verifier_pass(session, run, issue, 'ready')
    assert session.query(TimelineEvent).filter_by(event_type='verification_passed').count() == 1


def test_stale_verification_cannot_complete_reassigned_work(session, mocker):
    ws, agent = _make_agent(session)
    verifier = Agent(workspace_id=ws.id, runtime_id=agent.runtime_id, name='Reviewer', require_run_approval=True)
    session.add(verifier); session.flush(); agent.verifier_agent_id = verifier.id
    issue = _make_issue(session, ws, status=IssueStatus.IN_REVIEW)
    original = Run(issue_id=issue.id, agent_id=agent.id, prompt='old', status=RunStatus.COMPLETED)
    session.add(original); session.commit()
    _run_verifier_pass(session, original, issue, 'old work')
    review = session.query(Run).filter_by(review_of_run_id=original.id).one()
    session.add(Run(issue_id=issue.id, agent_id=agent.id, prompt='new', status=RunStatus.PENDING))
    review.status = RunStatus.PENDING; session.commit()
    mocker.patch('hagent.engine.execute_agent', return_value=RuntimeResult(output='PASS'))
    run_issue(session, review.issue, verifier, _run=review)
    assert issue.status != IssueStatus.DONE
    assert session.query(TimelineEvent).filter_by(event_type='verification_superseded').count() == 1


def test_issue_delegation_has_child_run_and_independent_review(session, mocker):
    from hagent.models import Squad, SquadMember
    ws, holly = _make_agent(session)
    holly.name = 'Holly'
    worker = Agent(workspace_id=ws.id, runtime_id=holly.runtime_id, name='Jack', instructions='Implement the task.')
    reviewer = Agent(workspace_id=ws.id, runtime_id=holly.runtime_id, name='Ruth', instructions='Reviewer rules')
    squad = Squad(workspace_id=ws.id, name='Acceptance team')
    session.add_all([worker, reviewer, squad]); session.flush()
    worker.verifier_agent_id = reviewer.id
    session.add_all([SquadMember(squad_id=squad.id, agent_id=holly.id, role='supervisor'),
                     SquadMember(squad_id=squad.id, agent_id=worker.id, role='developer')])
    issue = _make_issue(session, ws)
    issue.assignee_agent_id = holly.id
    session.commit()

    def model(_runtime, *, prompt, context, tools, tool_executor, **kwargs):
        if 'Reviewer rules' in context:
            return RuntimeResult(output='PASS: acceptance checks passed')
        if prompt.startswith('Delegated by Holly'):
            return RuntimeResult(output='Artifact and passing checks')
        tool = next(t['function']['name'] for t in tools if t['function']['name'].startswith('delegate_'))
        result = tool_executor(tool, {'task': 'Build the tiny acceptance artifact'})
        assert 'run ' in result and 'issue status: done' in result
        return RuntimeResult(output='Delegation finished: ' + result)

    mocker.patch('hagent.adapters.ollama.OllamaRuntime.run', autospec=True, side_effect=model)
    parent_run = run_issue(session, issue, holly)
    assert parent_run.status == RunStatus.COMPLETED
    child = session.query(type(issue)).filter_by(parent_issue_id=issue.id).one()
    assert child.assignee_agent_id == worker.id
    assert child.status == IssueStatus.DONE
    child_run = session.query(Run).filter_by(issue_id=child.id).one()
    assert child_run.status == RunStatus.COMPLETED
    review_run = session.query(Run).filter_by(review_of_run_id=child_run.id).one()
    assert review_run.agent_id == reviewer.id
    assert review_run.review_applied_at is not None
