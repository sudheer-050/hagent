from contextlib import contextmanager

import pytest

import hagent.memory_mcp as memory_mcp
from hagent.models import Project, Workspace


EXPECTED_TOOLS = {
    "memory_start_session",
    "memory_get_context",
    "memory_search",
    "memory_remember",
    "memory_update",
    "memory_forget",
    "memory_record_event",
    "memory_finish_session",
    "memory_list",
}


def tool(name):
    return memory_mcp.SERVER._tool_manager._tools[name]


def test_mcp_tool_names_and_bounded_typed_schemas():
    registered = memory_mcp.SERVER._tool_manager._tools
    assert set(registered) == EXPECTED_TOOLS
    remember_schema = registered["memory_remember"].parameters
    assert remember_schema["type"] == "object"
    assert remember_schema["required"] == ["content"]
    assert remember_schema["properties"]["ttl_days"]["anyOf"][0]["type"] == "integer"
    context_schema = registered["memory_get_context"].parameters
    assert {"query", "limit", "token_budget"} <= set(context_schema["properties"])


def test_mcp_rejects_missing_fixed_owner_scope(monkeypatch):
    monkeypatch.delenv("HAGENT_MEMORY_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HAGENT_MEMORY_USER_ID", raising=False)
    with pytest.raises(RuntimeError, match="HAGENT_MEMORY_WORKSPACE_ID"):
        tool("memory_search").fn(query="anything")


def test_claude_label_write_is_visible_to_codex_label_via_same_mcp_store(
    session, monkeypatch
):
    workspace = Workspace(name="Shared")
    session.add(workspace)
    session.flush()
    project = Project(workspace_id=workspace.id, name="Project")
    session.add(project)
    session.commit()

    @contextmanager
    def test_session(workspace_id=None):
        assert workspace_id == workspace.id
        yield session

    monkeypatch.setattr(memory_mcp, "get_session", test_session)
    monkeypatch.setenv("HAGENT_MEMORY_WORKSPACE_ID", workspace.id)
    monkeypatch.setenv("HAGENT_MEMORY_USER_ID", "owner")
    monkeypatch.setenv("HAGENT_MEMORY_PROJECT_ID", project.id)
    monkeypatch.setenv("HAGENT_MEMORY_PROVIDER", "claude_code")
    saved = tool("memory_remember").fn(
        content="The approved cache backend is Redis.",
        category="decision",
        origin_type="confirmed_decision",
        verification_status="verified",
        source_agent="claude",
    )
    assert saved["source"]["provider"] == "claude_code"

    monkeypatch.setenv("HAGENT_MEMORY_PROVIDER", "codex_cli")
    found = tool("memory_search").fn(query="approved cache backend")
    assert [item["id"] for item in found["memories"]] == [saved["id"]]
    assert found["memories"][0]["source"]["provider"] == "claude_code"
