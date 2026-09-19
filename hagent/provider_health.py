"""Provider availability monitoring and quota/session-limit failover."""

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import object_session

from hagent.adapters import get_runtime_class
from hagent.models import Agent, Issue, IssueStatus, Runtime, RuntimeType, TimelineEvent


PROVIDER_MONITOR_NAME = "Provider Monitor"
PROBE_PROMPT = "Provider availability check. Reply with exactly OK."
_CAPACITY_ERROR = re.compile(
    r"(session|usage|spending|token|context|rate)[ -]?(?:limit|quota)"
    r"|(?:limit|quota).*(?:reached|exceeded|exhausted)"
    r"|credits?.*(?:exhausted|insufficient|depleted)|insufficient.*credits?"
    r"|too many requests|resource[_ -]?exhausted|\b429\b",
    re.IGNORECASE,
)


def is_capacity_error(error) -> bool:
    """Return whether a provider failure means its current allowance is exhausted."""
    return bool(_CAPACITY_ERROR.search(str(error)))


def _monitor_runtime(runtimes: list[Runtime]) -> Runtime:
    priorities = {
        RuntimeType.CODEX_CLI: 0,
        RuntimeType.OLLAMA: 1,
        RuntimeType.CLAUDE_CODE: 2,
    }
    return min(runtimes, key=lambda runtime: (priorities.get(runtime.type, 10), runtime.name.casefold()))


def ensure_provider_monitor_agents(session) -> list[Agent]:
    """Create one visible monitor agent per workspace once a runtime exists."""
    runtimes = session.scalars(
        select(Runtime).where(Runtime.archived.is_(False)).order_by(Runtime.workspace_id, Runtime.name)
    ).all()
    by_workspace = {}
    for runtime in runtimes:
        by_workspace.setdefault(runtime.workspace_id, []).append(runtime)

    created = []
    for workspace_id, choices in by_workspace.items():
        existing = session.scalar(
            select(Agent).where(
                Agent.workspace_id == workspace_id,
                Agent.name == PROVIDER_MONITOR_NAME,
                Agent.archived.is_(False),
            )
        )
        if existing:
            continue
        primary = _monitor_runtime(choices)
        backup = next((runtime for runtime in choices if runtime.id != primary.id), None)
        monitor = Agent(
            workspace_id=workspace_id,
            runtime_id=primary.id,
            backup_runtime_id=backup.id if backup else None,
            name=PROVIDER_MONITOR_NAME,
            description="Checks provider availability and coordinates automatic backup switching.",
            instructions=(
                "Monitor AI provider availability. When a provider reaches a session, token, "
                "rate, or quota limit, notify affected agents and promote their configured backup."
            ),
        )
        session.add(monitor)
        session.flush()
        created.append(monitor)
    if created:
        session.commit()
    return created


_OPEN_STATUSES = (IssueStatus.TODO, IssueStatus.IN_PROGRESS, IssueStatus.IN_REVIEW, IssueStatus.BLOCKED)


def _notify(session, agent: Agent, body: str) -> None:
    """Record a provider switch on the timeline of every open issue the agent is working on."""
    for issue_id in session.scalars(select(Issue.id).where(Issue.assignee_agent_id == agent.id, Issue.status.in_(_OPEN_STATUSES))).all():
        session.add(TimelineEvent(issue_id=issue_id, event_type="provider_failover", detail=body))


def promote_backups_for_limited_runtime(session, failed_runtime: Runtime, error) -> list[Agent]:
    """Swap every affected agent to its own valid backup and note it on the agent's open issues."""
    now = datetime.now(timezone.utc)
    detail = str(error).strip()[:1000]
    failed_runtime.health_status = "limited"
    failed_runtime.health_detail = detail
    failed_runtime.last_health_check_at = now
    affected = session.scalars(
        select(Agent).where(
            Agent.workspace_id == failed_runtime.workspace_id,
            Agent.runtime_id == failed_runtime.id,
            Agent.archived.is_(False),
        )
    ).all()
    switched = []
    for agent in affected:
        backup = session.get(Runtime, agent.backup_runtime_id) if agent.backup_runtime_id else None
        if backup and not backup.archived and backup.workspace_id == agent.workspace_id and backup.id != failed_runtime.id:
            agent.runtime_id = backup.id
            agent.backup_runtime_id = failed_runtime.id
            agent.failback_runtime_id = failed_runtime.id
            _notify(
                session,
                agent,
                (
                    f"{failed_runtime.name} reached its current session or usage limit. "
                    f"I switched {agent.name} to {backup.name}; {failed_runtime.name} is now the backup. "
                    f"The request that detected the limit will also retry on {backup.name}."
                ),
            )
            switched.append(agent)
        else:
            _notify(
                session,
                agent,
                (
                    f"{failed_runtime.name} reached its current session or usage limit, but "
                    f"{agent.name} has no available backup runtime configured."
                ),
            )
    session.commit()
    return switched


def restore_recovered_runtime(session, recovered_runtime: Runtime) -> list[Agent]:
    """Return automatically failed-over agents to their recovered provider."""
    affected = session.scalars(
        select(Agent).where(
            Agent.workspace_id == recovered_runtime.workspace_id,
            Agent.backup_runtime_id == recovered_runtime.id,
            Agent.failback_runtime_id == recovered_runtime.id,
            Agent.archived.is_(False),
        )
    ).all()
    restored = []
    for agent in affected:
        temporary_runtime = session.get(Runtime, agent.runtime_id)
        if temporary_runtime is None or temporary_runtime.id == recovered_runtime.id:
            agent.failback_runtime_id = None
            continue
        agent.runtime_id = recovered_runtime.id
        agent.backup_runtime_id = temporary_runtime.id
        agent.failback_runtime_id = None
        _notify(
            session,
            agent,
            (
                f"{recovered_runtime.name} is available again. I switched {agent.name} "
                f"back to its original provider; {temporary_runtime.name} is the backup again."
            ),
        )
        restored.append(agent)
    session.commit()
    return restored


def handle_capacity_failure(agent: Agent, failed_runtime: Runtime, error) -> list[Agent]:
    """React to a live request failure when the agent is attached to a DB session."""
    if not is_capacity_error(error):
        return []
    session = object_session(agent)
    if session is None:
        return []
    return promote_backups_for_limited_runtime(session, failed_runtime, error)


def _probe(runtime: Runtime) -> None:
    config = json.loads(runtime.config_json or "{}")
    config["timeout"] = min(45, int(config.get("timeout", 45)))
    adapter = get_runtime_class(runtime.type)(model=runtime.model, config=config)
    adapter.run(
        PROBE_PROMPT,
        context="This is an automated availability probe. Do not use tools. Reply only OK.",
    )


def run_provider_health_checks() -> dict:
    """Probe active and recovering providers, failing over or back as needed."""
    from hagent.db import get_session

    checked = healthy = limited = errors = switched = restored = 0
    with get_session(scoped=False) as session:
        ensure_provider_monitor_agents(session)
        primary_ids = set(
            session.scalars(select(Agent.runtime_id).where(Agent.archived.is_(False))).all()
        )
        recovering_ids = set(
            session.scalars(
                select(Agent.failback_runtime_id).where(
                    Agent.failback_runtime_id.is_not(None),
                    Agent.archived.is_(False),
                )
            ).all()
        )
        runtimes = session.scalars(
            select(Runtime).where(
                Runtime.archived.is_(False),
                (Runtime.id.in_(primary_ids)) | (Runtime.id.in_(recovering_ids)),
            )
        ).all()
        for runtime in runtimes:
            checked += 1
            awaiting_failback = runtime.id in recovering_ids
            try:
                _probe(runtime)
            except Exception as exc:
                if is_capacity_error(exc):
                    limited += 1
                    switched += len(promote_backups_for_limited_runtime(session, runtime, exc))
                else:
                    errors += 1
                    runtime.health_status = "error"
                    runtime.health_detail = str(exc).strip()[:1000]
                    runtime.last_health_check_at = datetime.now(timezone.utc)
                    session.commit()
            else:
                healthy += 1
                runtime.health_status = "healthy"
                runtime.health_detail = ""
                runtime.last_health_check_at = datetime.now(timezone.utc)
                session.commit()
                if awaiting_failback:
                    restored += len(restore_recovered_runtime(session, runtime))
    return {
        "checked": checked,
        "healthy": healthy,
        "limited": limited,
        "errors": errors,
        "switched": switched,
        "restored": restored,
    }
