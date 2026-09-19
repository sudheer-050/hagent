"""In-memory registry of "who is currently delegating to whom" - powers the
dashboard village's travel animation (an agent visibly walks to a teammate's
office while a delegation is in flight). Process-local and best-effort: this
app runs as a single uvicorn process, so a plain dict is sufficient and avoids
a schema migration for what is purely a live UI cue. Entries expire on their
own via max_age so a crashed run can never leave an agent stuck "traveling"."""

import time

_active: dict[str, dict] = {}


def start_delegation(from_agent_id: str, to_agent_id: str) -> None:
    _active[from_agent_id] = {"to": to_agent_id, "since": time.time()}


def end_delegation(from_agent_id: str) -> None:
    _active.pop(from_agent_id, None)


def current_delegations(max_age: float = 40.0) -> dict[str, str]:
    now = time.time()
    stale = [k for k, v in _active.items() if now - v["since"] > max_age]
    for k in stale:
        _active.pop(k, None)
    return {k: v["to"] for k, v in _active.items()}
