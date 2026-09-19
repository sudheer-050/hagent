"""Optional Claude Code lifecycle hook for Hagent memory sessions.

The hook records only lifecycle state. It deliberately does not read Claude's
transcript or persist prompt/output content; durable memories remain explicit
agent-mediated MCP calls unless raw retention is separately enabled.
"""
from __future__ import annotations

import json
import os
import sys

from sqlalchemy import select

from hagent.db import get_session, init_db
from hagent.memory import MemoryScope, MemoryService
from hagent.models import MemorySession


def scope_from_environment():
    workspace_id = os.environ.get("HAGENT_MEMORY_WORKSPACE_ID", "")
    user_id = os.environ.get("HAGENT_MEMORY_USER_ID", "")
    if not workspace_id or not user_id:
        raise RuntimeError("HAGENT_MEMORY_WORKSPACE_ID and HAGENT_MEMORY_USER_ID are required")
    return MemoryScope(
        workspace_id=workspace_id,
        user_id=user_id,
        project_id=os.environ.get("HAGENT_MEMORY_PROJECT_ID") or None,
        provider="claude_code",
    )


def handle(action, payload):
    scope = scope_from_environment()
    external_id = str(payload.get("session_id") or "")
    if not external_id:
        raise ValueError("Claude hook payload has no session_id")
    init_db()
    with get_session(workspace_id=scope.workspace_id) as session:
        service = MemoryService(session, scope)
        if action == "start":
            existing = session.scalar(select(MemorySession).where(
                MemorySession.workspace_id == scope.workspace_id,
                MemorySession.user_id == scope.user_id,
                MemorySession.project_id == scope.project_id,
                MemorySession.external_session_id == external_id,
                MemorySession.status == "active",
            ))
            if existing:
                return {"session_id": existing.id, "status": "active"}
            return service.start_session(
                task="Claude Code session",
                client="claude_code_hook",
                external_session_id=external_id,
                metadata={"capture": "lifecycle_only"},
            )
        if action == "finish":
            existing = session.scalar(select(MemorySession).where(
                MemorySession.workspace_id == scope.workspace_id,
                MemorySession.user_id == scope.user_id,
                MemorySession.project_id == scope.project_id,
                MemorySession.external_session_id == external_id,
                MemorySession.status == "active",
            ))
            if not existing:
                return {"session_id": None, "status": "not_found"}
            return service.finish_session(existing.id)
        raise ValueError("Action must be start or finish")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.load(sys.stdin)
    result = handle(action, payload)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
