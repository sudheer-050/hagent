"""Provider availability monitoring and quota/session-limit failover."""

import json
import re
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import object_session

from hagent.adapters import get_runtime_class
from hagent.models import Agent, Issue, IssueStatus, Runtime, RuntimeType, TimelineEvent


PROVIDER_MONITOR_NAME = "Provider Monitor"
PROBE_PROMPT = "Provider availability check. Reply with exactly OK."
# Independent providers can be checked together; repeated checks of one cannot.
manual_probe_lock = threading.Lock()
_manual_probe_locks = {}


def manual_probe_lock_for(runtime_id):
    with manual_probe_lock:
        return _manual_probe_locks.setdefault(runtime_id, threading.Lock())

_CAPACITY_ERROR = re.compile(
    r"(session|usage|spending|token|context|rate)[ _-]?(?:limit|quota)"
    r"|(?:limit|quota).*(?:reached|exceeded|exhausted)"
    r"|credits?.*(?:exhausted|insufficient|depleted)|insufficient.*credits?"
    r"|too many requests|resource[_ -]?exhausted|\b429\b",
    re.IGNORECASE,
)


def is_capacity_error(error) -> bool:
    """Return whether a provider failure means its current allowance is exhausted."""
    return bool(_CAPACITY_ERROR.search(str(error)))


def usage_limit_notice(error, now=None):
    """Plain user-facing quota errors; never invent a provider reset time."""
    if not is_capacity_error(error):
        return None
    text = str(error)
    if re.search(r'context.{0,30}(?:length|window|limit)', text, re.I):
        return 'This conversation is too long. Start a new conversation to continue.'
    prefix = 'Your tokens are used up.'
    if re.search(r'credits?.*(?:exhausted|insufficient|depleted)|insufficient.*credits?', text, re.I):
        return 'Your provider credits are used up. Add credits to continue.'
    if re.search(r'too many requests|rate[ _-]?limit|\b429\b', text, re.I) and not re.search(r'usage|tokens?|quota', text, re.I):
        prefix = 'The provider has reached its request limit.'
    match = _RETRY_AT.search(text)
    if match:
        hour, minute = int(match[1]), int(match[2] or 0)
        if 1 <= hour <= 12 and 0 <= minute < 60:
            period = match[3].replace('.', '').upper()
            zone = f' ({match[4]})' if match[4] else ''
            return f'{prefix} Please wait until {hour}:{minute:02d} {period}{zone}.'
    epoch = re.search(r"resets?_at['\"\\]*\s*:\s*['\"]?(\d{9,13})\b", text, re.I)
    relative = re.search(r"resets_in_seconds['\"\\]*\s*:\s*(\d+)\b", text, re.I)
    try:
        reset = None
        if epoch:
            stamp = int(epoch[1])
            reset = datetime.fromtimestamp(stamp / 1000 if stamp > 10**11 else stamp, timezone.utc)
        elif relative:
            reset = (now or datetime.now(timezone.utc)) + timedelta(seconds=int(relative[1]))
        if reset:
            when = reset.astimezone().strftime('%I:%M %p %Z on %b %d').lstrip('0')
            return f'{prefix} Please wait until {when}.'
    except (ValueError, OverflowError, OSError):
        pass
    return f'{prefix} Please try again later. No reset time was provided.'


# Failures that mean the provider itself is unreachable or broken, not that this task went
# wrong. A timeout is deliberately excluded: a long task timing out says nothing about the
# provider's health.
_INFRASTRUCTURE_ERROR = re.compile(
    r"ECONNREFUSED|connection refused|failed to start|not installed|not on PATH"
    r"|ENOTFOUND|getaddrinfo|name or service not known|could not resolve host",
    re.IGNORECASE,
)
# Codex: "... or try again at 5:23 PM." / Claude Code: "You've hit your session limit - resets
# 5:20pm (America/New_York)". The time is the provider's local clock (or the named zone).
_RETRY_AT = re.compile(
    r"(?:try again at|resets(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)(?:\s*\(([A-Za-z_]+/[A-Za-z_/]+)\))?",
    re.IGNORECASE,
)
LIMITED_COOLDOWN = timedelta(minutes=30)
MAX_DETAIL_CHARS = 1000
ERROR_COOLDOWN = timedelta(minutes=10)


def is_infrastructure_error(error) -> bool:
    if "timed out" in str(error).lower() and "failed to start" in str(error).lower():
        return False  # adapters report a TimeoutExpired as "failed to start: ... timed out"
    return bool(_INFRASTRUCTURE_ERROR.search(str(error)))


def _as_utc(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def retry_after(runtime: Runtime):
    """When a runtime marked limited or error may be tried again (UTC), or None if it is usable now.

    A usage-limit message that names a reset time ("try again at 8:35 PM", local time) is
    honored exactly - this is how Paperclip classifies provider_quota failures. Otherwise a
    fixed cooldown applies, so a broken provider costs one failed attempt per cooldown window
    instead of one per run.
    """
    if runtime.health_status not in {"limited", "error"} or runtime.last_health_check_at is None:
        return None
    checked = _as_utc(runtime.last_health_check_at)
    if runtime.health_status == "error":
        return checked + ERROR_COOLDOWN
    reset = parse_reset_time(runtime.health_detail or "", checked)
    return reset or checked + LIMITED_COOLDOWN


def parse_reset_time(text: str, after: datetime):
    """The first reset time a usage-limit message names, as the next such moment after `after` (UTC)."""
    match = _RETRY_AT.search(text)
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower().startswith("p") else 0)
    minute = int(match.group(2) or 0)
    zone = None
    if match.group(4):
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(match.group(4))
        except Exception:  # noqa: BLE001 - unknown zone or no tz database: fall back to local time
            zone = None
    local_after = _as_utc(after).astimezone(zone)
    reset = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset <= local_after:
        reset += timedelta(days=1)
    return reset.astimezone(timezone.utc)


def limit_detail(error) -> str:
    """A health_detail that keeps the reset time even when the raw error is a long JSON event stream.

    CLI errors carry the whole event log, and the "try again at ..." sentence is often past the
    first 1000 characters, so a plain truncation silently threw the reset time away.
    """
    text = str(error).strip()
    match = _RETRY_AT.search(text)
    if not match or match.end() <= MAX_DETAIL_CHARS:
        return text[:MAX_DETAIL_CHARS]
    reset_clause = match.group(0)
    return (reset_clause + " | " + text)[:MAX_DETAIL_CHARS]


def is_cooling_down(runtime, now=None) -> bool:
    if runtime is None:
        return False
    until = retry_after(runtime)
    return until is not None and (now or datetime.now(timezone.utc)) < until


def record_infrastructure_failure(runtime: Runtime, error) -> bool:
    """Mark a runtime unhealthy after an unreachable/broken-provider failure. Returns whether it did."""
    if not is_infrastructure_error(error) or is_capacity_error(error):
        return False
    session = object_session(runtime)
    if session is None:
        return False
    runtime.health_status = "error"
    runtime.health_detail = str(error).strip()[:1000]
    runtime.last_health_check_at = datetime.now(timezone.utc)
    session.commit()
    return True


def record_runtime_success(runtime: Runtime) -> None:
    """A real run succeeded, so the runtime is healthy whatever the last check said."""
    session = object_session(runtime)
    if session is None:
        return
    runtime.health_status = "healthy"
    runtime.health_detail = ""
    runtime.last_health_check_at = datetime.now(timezone.utc)
    session.commit()


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
    detail = limit_detail(error)
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
        if backup and is_cooling_down(backup, now):
            # Both providers are limited. Moving now would only bounce the agent between two
            # exhausted runtimes and lose track of its home runtime; stay put and wait.
            _notify(session, agent, (
                f"{failed_runtime.name} reached its current session or usage limit, and {agent.name}'s "
                f"backup {backup.name} is limited too, so I left {agent.name} where it is."))
            continue
        if backup and not backup.archived and backup.workspace_id == agent.workspace_id and backup.id != failed_runtime.id:
            agent.runtime_id = backup.id
            agent.backup_runtime_id = failed_runtime.id
            # Remember the agent's home runtime from its FIRST failover only. Overwriting it on a
            # second failover is how a day with both providers limited stranded every agent on
            # whichever provider happened to recover first.
            if agent.failback_runtime_id is None:
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
    """Return automatically failed-over agents to their home provider once it recovers.

    The home provider is failback_runtime_id; the agent's current backup is irrelevant here (after
    two failovers it can point anywhere), so it is not part of the match.
    """
    affected = session.scalars(
        select(Agent).where(
            Agent.workspace_id == recovered_runtime.workspace_id,
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
    config["timeout"] = min(20, int(config.get("timeout", 20)))
    config.update(terminal_enabled=False, max_tokens=16, max_tool_rounds=1, max_retries=0)
    config.pop("resume_session_id", None)
    if config.get("worker"):
        raise RuntimeError("This runtime uses a remote worker. Test its worker connection on the Workers page.")
    from hagent import cli_sessions
    if runtime.type.value in cli_sessions.SUPPORTED and cli_sessions.enabled({'_persistent_key': runtime.id}):
        result = cli_sessions.probe(runtime.type.value, runtime.model, runtime.id, config, PROBE_PROMPT)
        if not (result.output or '').strip():
            raise RuntimeError('The provider returned an empty response.')
        return
    with tempfile.TemporaryDirectory(prefix="hagent-connection-test-") as directory:
        config["working_directory"] = directory
        adapter = get_runtime_class(runtime.type)(model=runtime.model, config=config)
        result = adapter.run(
            PROBE_PROMPT,
            context="This is an automated availability probe. Do not use tools. Reply only OK.",
        )
        if not (result.output or "").strip():
            raise RuntimeError("The provider returned an empty response. Check the selected model and account.")


def connection_error(runtime, error):
    """Keep saved credentials out of user-visible and persisted diagnostics."""
    from hagent.memory import redact_sensitive
    config = json.loads(runtime.config_json or "{}")
    detail = str(error)
    secrets = [config.get(key) for key in ("api_key", "token", "password")]
    secrets += [os.environ.get(name) for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", config.get("api_key_env", ""))]
    for secret in secrets:
        if secret:
            detail = detail.replace(str(secret), "[redacted]")
    return redact_sensitive(detail)[0][:800]


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
            probe_lock = manual_probe_lock_for(runtime.id)
            if not probe_lock.acquire(blocking=False):
                continue
            try:
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
            finally:
                probe_lock.release()
    return {
        "checked": checked,
        "healthy": healthy,
        "limited": limited,
        "errors": errors,
        "switched": switched,
        "restored": restored,
    }
