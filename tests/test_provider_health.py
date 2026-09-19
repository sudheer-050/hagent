from contextlib import nullcontext

from hagent.models import Agent, Issue, IssueStatus, Project, Runtime, RuntimeType, TimelineEvent, Workspace
from hagent.provider_health import (
    PROVIDER_MONITOR_NAME,
    ensure_provider_monitor_agents,
    is_capacity_error,
    promote_backups_for_limited_runtime,
    restore_recovered_runtime,
    run_provider_health_checks,
)


def _seed(session):
    workspace = Workspace(name="provider-health")
    session.add(workspace)
    session.flush()
    claude = Runtime(
        workspace_id=workspace.id,
        name="Claude",
        type=RuntimeType.CLAUDE_CODE,
        model="default",
    )
    codex = Runtime(
        workspace_id=workspace.id,
        name="Codex",
        type=RuntimeType.CODEX_CLI,
        model="default",
    )
    session.add_all([claude, codex])
    session.flush()
    agent = Agent(
        workspace_id=workspace.id,
        runtime_id=claude.id,
        backup_runtime_id=codex.id,
        name="Holly",
    )
    session.add(agent)
    session.commit()
    return workspace, claude, codex, agent


def test_capacity_error_recognizes_session_and_quota_limits():
    assert is_capacity_error("Claude session limit reached")
    assert is_capacity_error("429 RESOURCE_EXHAUSTED")
    assert is_capacity_error("insufficient credits")
    assert not is_capacity_error("connection refused")


def test_limited_runtime_promotes_backups_and_notifies_agents(session):
    workspace, claude, codex, agent = _seed(session)
    project = Project(workspace_id=workspace.id, name="p")
    session.add(project)
    session.flush()
    working = Issue(project_id=project.id, title="in flight", status=IssueStatus.IN_PROGRESS, assignee_agent_id=agent.id)
    finished = Issue(project_id=project.id, title="finished", status=IssueStatus.DONE, assignee_agent_id=agent.id)
    session.add_all([working, finished])
    session.commit()

    switched = promote_backups_for_limited_runtime(session, claude, "session limit reached")

    assert switched == [agent]
    assert agent.runtime_id == codex.id
    assert agent.backup_runtime_id == claude.id
    assert agent.failback_runtime_id == claude.id
    assert claude.health_status == "limited"
    notice = session.query(TimelineEvent).filter_by(event_type="provider_failover").one()  # only the open issue
    assert notice.issue_id == working.id and "switched Holly to Codex" in notice.detail


def test_recovered_runtime_restores_only_agents_that_automatically_failed_over(session):
    workspace, claude, codex, agent = _seed(session)
    other = Agent(
        workspace_id=workspace.id,
        runtime_id=codex.id,
        backup_runtime_id=claude.id,
        name="Already on Codex",
    )
    session.add(other)
    session.commit()
    promote_backups_for_limited_runtime(session, claude, "session limit reached")

    restored = restore_recovered_runtime(session, claude)

    assert restored == [agent]
    assert agent.runtime_id == claude.id
    assert agent.backup_runtime_id == codex.id
    assert agent.failback_runtime_id is None
    assert other.runtime_id == codex.id


def test_periodic_health_check_restores_original_provider_after_recovery(session, monkeypatch):
    _, claude, codex, agent = _seed(session)
    monkeypatch.setattr("hagent.db.get_session", lambda **kwargs: nullcontext(session))
    state = {"claude_available": False}

    def probe(runtime):
        if runtime.id == claude.id and not state["claude_available"]:
            raise RuntimeError("usage limit exceeded")

    monkeypatch.setattr("hagent.provider_health._probe", probe)
    run_provider_health_checks()
    assert agent.runtime_id == codex.id

    state["claude_available"] = True
    summary = run_provider_health_checks()

    assert summary["restored"] == 1
    assert agent.runtime_id == claude.id
    assert agent.backup_runtime_id == codex.id


def test_recovering_provider_keeps_being_probed_after_a_different_error(session, monkeypatch):
    _, claude, _, agent = _seed(session)
    monkeypatch.setattr("hagent.db.get_session", lambda **kwargs: nullcontext(session))
    outcomes = iter((RuntimeError("usage limit exceeded"), RuntimeError("connection refused"), None))

    def probe(runtime):
        if runtime.id == claude.id:
            outcome = next(outcomes)
            if outcome:
                raise outcome

    monkeypatch.setattr("hagent.provider_health._probe", probe)
    run_provider_health_checks()
    run_provider_health_checks()
    summary = run_provider_health_checks()

    assert summary["restored"] == 1
    assert agent.runtime_id == claude.id


def test_periodic_health_check_creates_monitor_and_promotes_on_limit(session, monkeypatch):
    _, claude, codex, agent = _seed(session)
    monkeypatch.setattr("hagent.db.get_session", lambda **kwargs: nullcontext(session))

    def probe(runtime):
        if runtime.id == claude.id:
            raise RuntimeError("usage limit exceeded")

    monkeypatch.setattr("hagent.provider_health._probe", probe)

    summary = run_provider_health_checks()

    assert summary["limited"] == 1
    assert summary["switched"] == 1
    assert agent.runtime_id == codex.id
    monitor = session.query(Agent).filter_by(name=PROVIDER_MONITOR_NAME).one()
    assert monitor.runtime_id == codex.id


def test_ensure_provider_monitor_prefers_codex(session):
    _, _, codex, _ = _seed(session)

    created = ensure_provider_monitor_agents(session)

    assert len(created) == 1
    assert created[0].runtime_id == codex.id
