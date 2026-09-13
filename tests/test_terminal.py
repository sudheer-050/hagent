from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.tenancy import WorkspaceSession
from hagent.terminal import run_agent_command
from hagent.web import app


@pytest.fixture(autouse=True)
def local_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'terminal-test.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False),
    )
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    db.init_db()
    yield
    engine.dispose()

def test_terminal_page_has_vscode_style_tabs_and_local_assets():
    response = TestClient(app).get("/terminal")
    assert response.status_code == 200
    assert 'id="terminal-tabs"' in response.text
    assert 'id="terminal-new"' in response.text
    assert 'id="terminal-dropdown"' in response.text
    assert 'id="terminal-custom-path-row" hidden' in response.text
    assert "/static/terminal.js?v=terminal-fit-2" in response.text
    assert 'id="terminal-dialog"' not in response.text
    assert 'value="home"' in response.text
    assert 'value="project"' in response.text
    assert str(Path.home()) in response.text
    assert 'data-project-cwd="' in response.text
    assert "/static/vendor/xterm.js" in response.text
    assert "/static/terminal.js" in response.text


def test_agent_terminal_command_runs_in_configured_directory(tmp_path):
    agent = SimpleNamespace(
        terminal_enabled=True,
        terminal_working_directory=str(tmp_path),
        env_json='{"HAGENT_TERMINAL_TEST": "available"}',
    )

    result = run_agent_command(
        agent,
        "Write-Output $env:HAGENT_TERMINAL_TEST; Write-Output (Get-Location).Path",
    )

    assert result["exit_code"] == 0
    assert "available" in result["stdout"]
    assert str(tmp_path) in result["stdout"]
    assert result["cwd"] == str(tmp_path.resolve())


def test_agent_terminal_command_is_opt_in(tmp_path):
    agent = SimpleNamespace(
        terminal_enabled=False,
        terminal_working_directory=str(tmp_path),
        env_json="{}",
    )

    try:
        run_agent_command(agent, "Write-Output should-not-run")
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled agent terminal unexpectedly ran")


def test_terminal_websocket_starts_real_powershell(tmp_path):
    with TestClient(app).websocket_connect(f"/ws/terminal?cwd={tmp_path}&rows=24&cols=80") as socket:
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        socket.send_json({"type": "input", "data": "Write-Output HAGENT_PTY_OK\r\n"})
        combined = ""
        for _ in range(200):
            message = socket.receive_json()
            if message["type"] == "output":
                combined += message["data"]
                if "HAGENT_PTY_OK" in combined:
                    break
        assert "HAGENT_PTY_OK" in combined