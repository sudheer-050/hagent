import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hagent import connections, db
from hagent.connection_secrets import protect, reveal
from hagent.mcp_client import call_tool_sync, list_tools_sync
from hagent.models import Agent, Base, McpServer, Runtime, RuntimeType, Workspace
from hagent.tenancy import WorkspaceSession
from hagent.web import app


@pytest.fixture
def connection_web(tmp_path, monkeypatch):
    monkeypatch.delenv("HAGENT_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("HAGENT_GOOGLE_CLIENT_SECRET", raising=False)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False))
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    yield TestClient(app, base_url="http://127.0.0.1")
    engine.dispose()


def add_google(client, service="gmail"):
    response = client.post("/connections", data={"kind": "google", "label": "Personal " + service,
                           "service_name": service, "client_id": "demo.apps.googleusercontent.com",
                           "client_secret": "private-client-secret"}, follow_redirects=False)
    assert response.status_code == 303
    with db.get_session() as session:
        return session.scalar(select(McpServer).order_by(McpServer.name))


def test_credentials_encrypted_and_not_rendered(connection_web):
    item = add_google(connection_web)
    assert "private-client-secret" not in item.config_json
    assert connections.credentials(item)["client_secret"] == "private-client-secret"
    page = connection_web.get("/connections")
    assert page.status_code == 200
    assert "Sign in with Google" in page.text
    assert "private-client-secret" not in page.text
    assert "dpapi:" not in page.text and "fernet:" not in page.text
    assert "/connections/google/settings" in page.text
    assert 'name="client_secret"' not in page.text
    assert 'name="client_id"' not in page.text
    with pytest.raises(ValueError, match="could not be unlocked"):
        reveal("invalid:do-not-echo-me")


def test_google_oauth_cookie_state_pkce_and_single_use(connection_web, monkeypatch):
    item = add_google(connection_web)
    start = connection_web.post(f"/connections/{item.id}/google/start", follow_redirects=False)
    assert start.status_code == 303
    params = parse_qs(urlsplit(start.headers["location"]).query)
    state = params["state"][0]
    assert params["access_type"] == ["offline"]
    assert params["scope"] == [connections.GOOGLE_SCOPES["gmail"]]
    assert params["code_challenge_method"] == ["S256"]
    assert "HttpOnly" in start.headers["set-cookie"]
    calls = []

    def exchange(method, url, **kwargs):
        calls.append(kwargs["data"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(kwargs["data"]["code_verifier"].encode()).digest()).decode().rstrip("=")
        assert params["code_challenge"] == [challenge]
        return {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600,
                "scope": connections.GOOGLE_SCOPES["gmail"]}

    monkeypatch.setattr(connections, "request", exchange)
    assert connection_web.get("/connections/google/callback", params={"state": state + "wrong", "code": "code"}).status_code == 400
    other_browser = TestClient(app, base_url="http://127.0.0.1")
    assert other_browser.get("/connections/google/callback", params={"state": state, "code": "code"}).status_code == 400
    assert not calls
    callback = connection_web.get("/connections/google/callback", params={"state": state, "code": "code"}, follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    assert calls[0]["client_secret"] == "private-client-secret"
    assert calls[0]["redirect_uri"] == params["redirect_uri"][0]
    assert connection_web.get("/connections/google/callback", params={"state": state, "code": "code"}).status_code == 400
    with db.get_session() as session:
        saved = session.get(McpServer, item.id)
        assert connections.config(saved)["connected"]
        assert connections.credentials(saved)["refresh_token"] == "private-refresh"
        assert "private-refresh" not in saved.config_json
    assert "private-access" not in connection_web.get("/connections").text


def test_expired_oauth_and_missing_service_scope_refused(connection_web, monkeypatch):
    item = add_google(connection_web, "drive")
    start = connection_web.post(f"/connections/{item.id}/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    with db.get_session() as session:
        saved = session.get(McpServer, item.id)
        cfg = connections.config(saved)
        cfg["oauth"]["expires"] = 0
        saved.config_json = json.dumps(cfg)
        session.commit()
    assert connection_web.get("/connections/google/callback", params={"state": state, "code": "code"}).status_code == 400
    start = connection_web.post(f"/connections/{item.id}/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr(connections, "request", lambda *a, **k: {"access_token": "a", "refresh_token": "r", "scope": "unrelated"})
    connection_web.get("/connections/google/callback", params={"state": state, "code": "code"})
    with db.get_session() as session:
        assert not connections.config(session.get(McpServer, item.id))["connected"]


def test_refresh_token_and_gmail_read_are_direct(connection_web, monkeypatch):
    item = add_google(connection_web)
    connections.save_credentials(item, {"client_secret": "s", "refresh_token": "r", "expires_at": 0})
    calls = []

    def google(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == connections.TOKEN_URL:
            assert kwargs["data"]["grant_type"] == "refresh_token"
            return {"access_token": "renewed", "expires_in": 3600}
        assert kwargs["headers"]["Authorization"] == "Bearer renewed"
        return {"id": "msg123", "payload": {"mimeType": "text/plain", "body": {
            "data": base64.urlsafe_b64encode(b"A real decoded message body").decode().rstrip("=")}}}

    monkeypatch.setattr(connections, "request", google)
    tools = list_tools_sync(item)
    assert {tool["name"] for tool in tools} == {"search_messages", "read_message"}
    assert "renewed" not in json.dumps(tools) and "client_secret" not in json.dumps(tools)
    result = call_tool_sync(item, "read_message", {"message_id": "msg123"})
    assert result["body"] == "A real decoded message body"
    assert len(calls) == 2
    assert connections.credentials(item)["refresh_token"] == "r"
    with pytest.raises(ValueError, match="valid message or file ID"):
        call_tool_sync(item, "read_message", {"message_id": "../profile"})
    assert len(calls) == 2


def test_drive_search_and_google_doc_export(connection_web, monkeypatch):
    item = add_google(connection_web, "drive")
    connections.save_credentials(item, {"access_token": "drive-token", "expires_at": time.time() + 3600})
    calls = []

    def google(method, url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/export"):
            assert kwargs["params"] == {"mimeType": "text/plain"}
            return "Document text from Google"
        if url.endswith("/doc123"):
            return {"id": "doc123", "name": "Notes", "mimeType": "application/vnd.google-apps.document"}
        assert "\\'" in kwargs["params"]["q"]
        return {"files": [{"id": "doc123", "name": "Notes"}], "nextPageToken": "next"}

    monkeypatch.setattr(connections, "request", google)
    assert call_tool_sync(item, "search_files", {"query": "owner's notes"})["nextPageToken"] == "next"
    assert call_tool_sync(item, "read_file", {"file_id": "doc123"})["content"] == "Document text from Google"
    assert len(calls) == 3


def test_direct_api_fixed_endpoint_and_secret_header(connection_web, monkeypatch):
    response = connection_web.post("/connections", data={"kind": "api", "label": "Example API",
                                   "url": "https://example.com/items", "auth": "header",
                                   "token": "private-api-key", "key_name": "X-Service-Key"})
    assert "private-api-key" not in response.text
    with db.get_session() as session:
        item = session.scalar(select(McpServer))
    seen = []
    monkeypatch.setattr(connections, "request", lambda method, url, **kw: seen.append((method, url, kw)) or {"ok": True})
    assert call_tool_sync(item, "get", {"query": {"status": "open"}}) == {"ok": True}
    assert seen == [("GET", "https://example.com/items", {"headers": {"X-Service-Key": "private-api-key"}, "params": {"status": "open"}})]
    with pytest.raises(ValueError, match="not available"):
        call_tool_sync(item, "delete", {})
    for url in ("http://remote.example/api", "https://user:password@example.com/api", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            connections.validate_url(url)


def test_workspace_access_and_disconnect(connection_web, monkeypatch):
    item = add_google(connection_web)
    with db.get_session(scoped=False) as session:
        foreign = Workspace(name="foreign")
        session.add(foreign)
        session.flush()
        runtime = Runtime(workspace_id=foreign.id, name="runtime", type=RuntimeType.OLLAMA, model="m")
        session.add(runtime)
        session.flush()
        agent = Agent(workspace_id=foreign.id, runtime_id=runtime.id, name="Foreign agent")
        session.add(agent)
        session.commit()
        foreign_id = agent.id
    assert connection_web.post(f"/connections/{item.id}/agents", data={"agent_ids": foreign_id}).status_code == 404
    with db.get_session() as session:
        connections.save_credentials(session.get(McpServer, item.id), {"client_secret": "s", "refresh_token": "r"})
    seen = []
    monkeypatch.setattr(connections, "request", lambda *args, **kwargs: seen.append((args, kwargs)) or "")
    assert connection_web.post(f"/connections/{item.id}/disconnect").status_code == 200
    assert seen[0][0] == ("POST", "https://oauth2.googleapis.com/revoke")
    with db.get_session() as session:
        saved = session.get(McpServer, item.id)
        assert saved is None


def test_browser_cannot_register_or_invoke_connections_cross_origin(connection_web):
    response = connection_web.post("/connections", headers={"Origin": "https://unrelated.example"},
                                   data={"kind": "api", "label": "Injected", "url": "https://example.com"})
    assert response.status_code == 403
    with db.get_session() as session:
        assert not session.scalars(select(McpServer)).all()


def test_google_sign_in_uses_installation_settings_without_user_credentials(connection_web):
    response = connection_web.post("/connections/google/settings", data={
        "client_id": "owner.apps.googleusercontent.com", "client_secret": "installation-secret",
    }, follow_redirects=False)
    assert response.status_code == 303
    page = connection_web.get("/connections")
    assert "Sign in to Gmail" in page.text and "Sign in to Google Drive" in page.text
    assert 'name="client_id"' not in page.text and 'name="client_secret"' not in page.text
    assert "installation-secret" not in page.text
    settings = connection_web.get("/connections/google/settings")
    assert "owner.apps.googleusercontent.com" in settings.text
    assert "installation-secret" not in settings.text
    assert "/connections/google/callback" in settings.text
    response = connection_web.post("/connections/google/connect", data={"service_name": "gmail"}, follow_redirects=False)
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert target.hostname == "accounts.google.com"
    assert parse_qs(target.query)["client_id"] == ["owner.apps.googleusercontent.com"]
    # Retrying an unfinished sign-in reuses the pending connection.
    connection_web.post("/connections/google/connect", data={"service_name": "gmail"}, follow_redirects=False)
    with db.get_session() as session:
        items = session.scalars(select(McpServer)).all()
        assert len(items) == 1
        assert connections.credentials(items[0])["client_secret"] == "installation-secret"


def test_unconfigured_google_sign_in_does_not_create_fake_connection(connection_web):
    response = connection_web.post("/connections/google/connect", data={"service_name": "gmail"}, follow_redirects=False)
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/connections"
    assert "enabled" in response.headers["location"]
    with db.get_session() as session:
        assert not session.scalars(select(McpServer)).all()


def test_installation_google_credentials_are_workspace_scoped(connection_web):
    connection_web.post("/connections/google/settings", data={
        "client_id": "owner.apps.googleusercontent.com", "client_secret": "workspace-secret"})
    with db.get_session(scoped=False) as session:
        foreign = Workspace(name="Other Google settings")
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    connection_web.cookies.set("workspace_id", foreign_id)
    assert "owner.apps.googleusercontent.com" not in connection_web.get("/connections/google/settings").text
    result = connection_web.post("/connections/google/connect", data={"service_name": "drive"}, follow_redirects=False)
    assert result.status_code == 303
    assert not result.headers["location"].startswith("https://accounts.google.com")


def test_engine_exposes_connection_tools_only_to_assigned_agent(session, mocker):
    from hagent.adapters.base import RuntimeResult
    from hagent.engine import execute_agent
    workspace = Workspace(name="connections")
    session.add(workspace)
    session.flush()
    runtime = Runtime(workspace_id=workspace.id, name="model", type=RuntimeType.OLLAMA, model="m")
    session.add(runtime)
    session.flush()
    agent = Agent(workspace_id=workspace.id, runtime_id=runtime.id, name="Connected agent")
    connection = McpServer(workspace_id=workspace.id, name="gmail", transport="google", config_json='{"service":"gmail"}')
    session.add_all([agent, connection])
    session.commit()
    observed = []

    def model(_runtime, *, tools, tool_executor, **kwargs):
        names = [tool["function"]["name"] for tool in tools]
        observed.append(names)
        if "gmail__read_message" in names:
            assert tool_executor("gmail__read_message", {"message_id": "m1"}) == {"body": "message"}
        return RuntimeResult(output="done")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=model)
    call = mocker.patch("hagent.connections.call_tool", return_value={"body": "message"})
    execute_agent(agent, "Read if authorized")
    call.assert_not_called()
    agent.mcp_servers = [connection]
    session.commit()
    execute_agent(agent, "Read authorized message")
    call.assert_called_once_with(connection, "read_message", {"message_id": "m1"})
    assert "gmail__read_message" not in observed[0]
    assert "gmail__read_message" in observed[1]


def test_real_stdio_mcp_discovery_and_execution(tmp_path):
    import sys
    stub = tmp_path / "connector.py"
    stub.write_text('''try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
import os
server = MCPServer("Connection test")
@server.tool()
def hello(name: str) -> str:
    return "Hello " + name + ("; credential available" if os.environ.get("DEMO_KEY") else "; missing credential")
server.run(transport="stdio")
''')
    server = McpServer(name="local_demo", transport="stdio", command=sys.executable,
                       args_json=json.dumps([str(stub)]), config_json=json.dumps({
                           "credentials": protect({"env": {"DEMO_KEY": "protected-test-value"}})}))
    try:
        tools = list_tools_sync(server)
    except PermissionError as exc:
        if getattr(exc, "winerror", None) == 5:
            pytest.skip("This Windows execution environment denies the named pipes required by stdio MCP")
        raise
    assert any(tool["name"] == "hello" for tool in tools)
    result = call_tool_sync(server, "hello", {"name": "Holly"})
    assert "Hello Holly; credential available" in json.dumps(result)
    assert "protected-test-value" not in json.dumps(result)


def test_http_requests_limit_responses_and_refuse_redirects(monkeypatch):
    import httpx
    client_class = httpx.Client
    seen = []

    def respond(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://other.example/steal"})

    monkeypatch.setattr(connections.httpx, "Client", lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError, match="HTTP 302"):
        connections.request("GET", "https://example.com/items", headers={"Authorization": "Bearer secret"})
    assert seen == ["https://example.com/items"]
    monkeypatch.setattr(connections, "MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(connections.httpx, "Client", lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'{"ok":true}'))))
    with pytest.raises(ValueError, match="exceeds"):
        connections.request("GET", "https://example.com/items")
