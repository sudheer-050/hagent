"""Picks up queued runs and starts them, respecting each agent's max_concurrent_tasks.

Runs in the web server and in `hagent daemon start`. Starting a run is an atomic claim (see
engine.run_issue), so several dispatchers can coexist safely.

Latency: a small thread watches SQLite's `PRAGMA data_version`, which changes whenever *any*
process commits to the database. A run queued by a CLI command is therefore picked up within
about POLL_SECONDS instead of waiting for a timer. The scheduler's interval job stays as a safety net.

Fairness: waiting runs age. Every AGING_SECONDS a run has waited moves it one priority level up, so
old low-priority work is never starved by a steady stream of newer high-priority work.
"""

import logging
import os
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import func, select

from hagent import db as hagent_db
from hagent.db import get_session
from hagent.engine import process_queued_run_by_id
from hagent.models import Agent, AppSetting, Issue, Run, RunStatus
from hagent.orchestration import PRIORITY_RANK

log = logging.getLogger(__name__)

DISPATCH_INTERVAL_SECONDS = 3  # the scheduler's safety-net interval
POLL_SECONDS = 0.25  # how often the loop checks whether the database changed
BACKSTOP_SECONDS = 3.0  # dispatch at least this often even if nothing seems to have changed
AGING_SECONDS = 600  # each wait of this length raises a run's priority by one level
HEARTBEAT_KEY = "dispatcher_heartbeat"
HEARTBEAT_EVERY_SECONDS = 10.0

_lock = threading.Lock()
_inflight: dict[str, str] = {}  # run id -> agent id, for runs this process has started
_executor: ThreadPoolExecutor | None = None
_last_heartbeat = 0.0
_loop_thread: threading.Thread | None = None
_loop_stop = threading.Event()


def _max_parallel() -> int:
    try:
        return max(1, int(os.environ.get("HAGENT_MAX_PARALLEL_RUNS", "8")))
    except ValueError:
        return 8


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_max_parallel(), thread_name_prefix="hagent-run")
    return _executor


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def effective_rank(priority: str | None, created_at: datetime, now: datetime | None = None) -> int:
    """Priority rank (0 = most urgent) after aging."""
    waited = ((now or datetime.now(timezone.utc)) - _utc(created_at)).total_seconds()
    return max(0, PRIORITY_RANK.get(priority or "none", 4) - int(max(0.0, waited) // AGING_SECONDS))


def _execute(run_id: str) -> None:
    try:
        process_queued_run_by_id(run_id)
    except Exception:  # noqa: BLE001 - a failed run is recorded by the engine; never kill the pool thread
        log.exception("queued run %s crashed", run_id)
    finally:
        with _lock:
            _inflight.pop(run_id, None)


def _beat(session_factory=get_session) -> None:
    """Record that a dispatcher is alive, at most every HEARTBEAT_EVERY_SECONDS."""
    global _last_heartbeat
    if time.monotonic() - _last_heartbeat < HEARTBEAT_EVERY_SECONDS:
        return
    _last_heartbeat = time.monotonic()
    with session_factory(scoped=False) as session:
        row = session.get(AppSetting, HEARTBEAT_KEY)
        if row is None:
            row = AppSetting(key=HEARTBEAT_KEY)
            session.add(row)
        row.value = repr(time.time())
        session.commit()


def heartbeat_age(session) -> float | None:
    """Seconds since a dispatcher last reported in, or None if none ever has."""
    row = session.get(AppSetting, HEARTBEAT_KEY)
    try:
        return max(0.0, time.time() - float(row.value)) if row and row.value else None
    except ValueError:
        return None


def dispatch_pending() -> int:
    """Start every queued run whose agent has spare capacity. Returns how many were started."""
    _beat()
    with get_session(scoped=False) as session:
        pending = session.execute(
            select(Run.id, Run.agent_id, Issue.priority, Run.created_at, Agent.max_concurrent_tasks)
            .join(Issue, Issue.id == Run.issue_id)
            .join(Agent, Agent.id == Run.agent_id)
            .where(Run.status == RunStatus.PENDING)
        ).all()
        if not pending:
            return 0
        running = dict(session.execute(select(Run.agent_id, func.count()).where(Run.status == RunStatus.RUNNING).group_by(Run.agent_id)).all())

    now = datetime.now(timezone.utc)
    pending.sort(key=lambda row: (effective_rank(row.priority, row.created_at, now), _utc(row.created_at)))
    started = 0
    with _lock:
        per_agent = dict(running)  # already includes in-flight runs that have claimed RUNNING
        pending_ids = {row.id for row in pending}
        for run_id, agent_id in _inflight.items():
            if run_id in pending_ids:  # started here but not yet claimed in the database
                per_agent[agent_id] = per_agent.get(agent_id, 0) + 1
        for row in pending:
            if row.id in _inflight or len(_inflight) >= _max_parallel():
                continue
            if per_agent.get(row.agent_id, 0) >= max(1, row.max_concurrent_tasks or 1):
                continue
            _inflight[row.id] = row.agent_id
            per_agent[row.agent_id] = per_agent.get(row.agent_id, 0) + 1
            _pool().submit(_execute, row.id)
            started += 1
    return started


# --- change-triggered wake-up ---------------------------------------------------------------

def _watch_connection():
    path = hagent_db.engine.url.database
    if not path or path == ":memory:":
        return None
    return sqlite3.connect(path, check_same_thread=False, timeout=5)


def _loop(stop: threading.Event) -> None:
    watcher, last_version, last_dispatch = None, None, 0.0
    try:
        watcher = _watch_connection()
    except sqlite3.Error:
        log.exception("dispatcher cannot watch the database; falling back to timed dispatch")
    while not stop.wait(POLL_SECONDS):
        try:
            changed = False
            if watcher is not None:
                version = watcher.execute("PRAGMA data_version").fetchone()[0]
                changed, last_version = version != last_version, version
            if changed or time.monotonic() - last_dispatch >= BACKSTOP_SECONDS:
                last_dispatch = time.monotonic()
                dispatch_pending()
        except Exception:  # noqa: BLE001 - keep the loop alive whatever one pass hits
            log.exception("dispatch loop pass failed")
    if watcher is not None:
        watcher.close()


def start_dispatch_loop() -> threading.Thread | None:
    """Start the wake-up loop once per process. HAGENT_DISPATCH_LOOP=0 leaves only the timer."""
    global _loop_thread
    if os.environ.get("HAGENT_DISPATCH_LOOP", "1") == "0":
        return None
    if _loop_thread is not None and _loop_thread.is_alive():
        return _loop_thread
    _loop_stop.clear()
    _loop_thread = threading.Thread(target=_loop, args=(_loop_stop,), name="hagent-dispatch-loop", daemon=True)
    _loop_thread.start()
    return _loop_thread


def stop_dispatch_loop() -> None:
    global _loop_thread
    _loop_stop.set()
    if _loop_thread is not None:
        _loop_thread.join(timeout=5)
    _loop_thread = None


def shutdown_dispatcher(*, wait: bool = False) -> None:
    """Stop accepting queued work and release the process-owned executor."""
    global _executor
    stop_dispatch_loop()
    with _lock:
        executor = _executor
        _executor = None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)


# --- "why is my run waiting?" ----------------------------------------------------------------

def explain_queue(session, now: datetime | None = None) -> dict:
    """Running and queued runs, each queued run with the concrete reason it is (not yet) starting."""
    from hagent.keys import issue_key
    from hagent.recovery import owner_is_alive

    now = now or datetime.now(timezone.utc)
    rows = lambda status: session.execute(  # noqa: E731
        select(Run, Issue, Agent).join(Issue, Issue.id == Run.issue_id).join(Agent, Agent.id == Run.agent_id).where(Run.status == status)
    ).all()

    running = []
    holders: dict[str, list[str]] = {}
    for run, issue, agent in rows(RunStatus.RUNNING):
        key = issue_key(session, issue)
        holders.setdefault(agent.id, []).append(key)
        running.append({
            "key": key, "agent": agent.name, "title": issue.title, "seconds": (now - _utc(run.started_at)).total_seconds() if run.started_at else 0.0,
            "orphaned": not owner_is_alive(run.owner_pid, run.owner_started),
        })

    slots_used = Counter({agent_id: len(keys) for agent_id, keys in holders.items()})
    global_used, cap = len(running), _max_parallel()
    pending = sorted(rows(RunStatus.PENDING), key=lambda r: (effective_rank(r[1].priority, r[0].created_at, now), _utc(r[0].created_at)))
    queued = []
    for position, (run, issue, agent) in enumerate(pending, start=1):
        capacity = max(1, agent.max_concurrent_tasks or 1)
        key = issue_key(session, issue)
        if slots_used[agent.id] >= capacity:
            reason = f"waiting for {agent.name} ({slots_used[agent.id]}/{capacity} busy: {', '.join(holders.get(agent.id, []))})"
        elif global_used >= cap:
            reason = f"global limit reached ({cap} runs at once)"
        else:
            reason = "starts on the next dispatcher pass"
            slots_used[agent.id] += 1
            global_used += 1
            holders.setdefault(agent.id, []).append(f"{key}, queued ahead")
        queued.append({
            "position": position, "key": key, "agent": agent.name, "priority": issue.priority or "none",
            "effective_rank": effective_rank(issue.priority, run.created_at, now), "waited": (now - _utc(run.created_at)).total_seconds(), "reason": reason,
        })

    approvals = [{"key": issue_key(session, issue), "agent": agent.name} for _, issue, agent in rows(RunStatus.WAITING_APPROVAL)]
    return {"heartbeat_age": heartbeat_age(session), "running": running, "queued": queued, "approvals": approvals, "global_cap": cap}
