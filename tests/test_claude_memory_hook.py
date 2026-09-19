from contextlib import contextmanager

from hagent.models import MemorySession, Project, Workspace
from scripts import claude_memory_hook


def test_claude_hook_records_lifecycle_only(session, monkeypatch):
    workspace = Workspace(name="Workspace")
    session.add(workspace)
    session.flush()
    project = Project(workspace_id=workspace.id, name="Project")
    session.add(project)
    session.commit()

    @contextmanager
    def test_session(workspace_id=None):
        assert workspace_id == workspace.id
        yield session

    monkeypatch.setattr(claude_memory_hook, "init_db", lambda: None)
    monkeypatch.setattr(claude_memory_hook, "get_session", test_session)
    monkeypatch.setenv("HAGENT_MEMORY_WORKSPACE_ID", workspace.id)
    monkeypatch.setenv("HAGENT_MEMORY_USER_ID", "owner")
    monkeypatch.setenv("HAGENT_MEMORY_PROJECT_ID", project.id)
    payload = {"session_id": "claude-session-1", "transcript_path": "must-not-be-read"}

    started = claude_memory_hook.handle("start", payload)
    duplicate = claude_memory_hook.handle("start", payload)
    finished = claude_memory_hook.handle("finish", payload)

    assert started["session_id"] == duplicate["session_id"]
    assert finished["status"] == "finished"
    row = session.query(MemorySession).one()
    assert row.external_session_id == "claude-session-1"
    assert row.status == "finished"
    assert row.events == []
