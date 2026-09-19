import json
import threading
import time

import pytest
import uvicorn

from hagent import auth, remote, warm, web
from hagent.models import User

PASSWORD = "correct horse battery"


@pytest.fixture()
def server(cli_db, auth_db, monkeypatch, tmp_path):
    """A real Hagent server on a free local port, backed by the throwaway database."""
    monkeypatch.setattr(auth, "_session", lambda: auth.hagent_db.SessionLocal())
    with auth.hagent_db.SessionLocal() as s:
        user = auth.create_user(s, "sudheer", PASSWORD)
        token, _ = auth.issue_token(s, s.get(User, user.id), name="test")
    config = uvicorn.Config(web.app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    port = instance.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setattr(remote, "CONFIG_PATH", tmp_path / "remote.json")
    for name in ("HAGENT_REMOTE_URL", "HAGENT_REMOTE_TOKEN", "HAGENT_LOCAL"):
        monkeypatch.delenv(name, raising=False)
    yield {"url": f"http://127.0.0.1:{port}", "token": token}
    instance.should_exit = True
    thread.join(timeout=5)


def login(server, capsys):
    assert remote.remote_command(["login", "--url", server["url"], "--token", server["token"]]) == 0
    capsys.readouterr()


def test_login_validates_the_token_and_saves_it(server, capsys):
    assert remote.remote_command(["login", "--url", server["url"], "--token", server["token"]]) == 0

    assert "as sudheer (owner)" in capsys.readouterr().out
    assert json.loads(remote.CONFIG_PATH.read_text()) == {"url": server["url"], "token": server["token"]}
    assert remote.remote_command(["status"]) == 0
    assert "Logged in to" in capsys.readouterr().out


def test_login_with_a_bad_token_saves_nothing(server, capsys):
    assert remote.remote_command(["login", "--url", server["url"], "--token", "hag_wrong"]) == 1

    assert "rejected this token" in capsys.readouterr().err
    assert not remote.CONFIG_PATH.exists()


def test_login_refuses_plain_http_to_other_hosts_unless_allowed(server, capsys):
    code = remote.remote_command(["login", "--url", "http://203.0.113.7:8000", "--token", "hag_x"])

    assert code == 2 and "plain http" in capsys.readouterr().err


def test_commands_run_on_the_server_and_output_and_exit_codes_come_back(server, capsys):
    login(server, capsys)

    assert remote.run_remote(["project", "create", "--name", "from-afar"]) == 0
    capsys.readouterr()
    assert remote.run_remote(["project", "list"]) == 0
    assert "from-afar" in capsys.readouterr().out

    assert remote.run_remote(["no-such-command"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_host_management_commands_are_refused_remotely(server, capsys):
    login(server, capsys)

    for argv in (["auth", "status"], ["serve"], ["daemon", "start"], ["worker", "start"]):
        assert remote.run_remote(argv) == 1
        assert "only be run on the machine that hosts Hagent" in capsys.readouterr().err


def test_a_revoked_token_stops_working(server, capsys):
    login(server, capsys)
    with auth.hagent_db.SessionLocal() as s:
        auth.revoke_token(s, s.query(auth.AuthToken).filter_by(kind="api").one().id)
    auth.invalidate_caches()

    assert remote.run_remote(["project", "list"]) == 1
    assert "rejected this token" in capsys.readouterr().err


def test_an_unreachable_server_is_reported_not_silently_run_locally(server, capsys, monkeypatch):
    monkeypatch.setenv("HAGENT_REMOTE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("HAGENT_REMOTE_TOKEN", "hag_x")

    assert remote.run_remote(["project", "list"]) == 1
    assert "Could not reach" in capsys.readouterr().err


def test_remote_execution_needs_an_identity_even_from_loopback(cli_db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(auth, "_session", lambda: auth.hagent_db.SessionLocal())
    client = TestClient(web.app)  # no accounts exist

    assert client.post("/api/cli", json={"argv": ["project", "list"]}).status_code == 403


# --- launcher routing -------------------------------------------------------

def test_launcher_sends_commands_to_the_remote_server_when_logged_in(server, capsys, mocker):
    login(server, capsys)
    run_remote = mocker.patch("hagent.remote.run_remote", return_value=0)

    with pytest.raises(SystemExit) as exit_info:
        warm.main(["issue", "list"])

    assert exit_info.value.code == 0
    run_remote.assert_called_once_with(["issue", "list"])


@pytest.mark.parametrize("argv", [["auth", "status"], ["serve"], ["worker", "start"]])
def test_launcher_keeps_host_commands_local(server, capsys, mocker, argv):
    login(server, capsys)
    run_remote = mocker.patch("hagent.remote.run_remote")
    local_cli = mocker.patch("hagent.cli.cli")

    warm.main(argv)

    run_remote.assert_not_called()
    local_cli.assert_called_once()


def test_hagent_local_forces_this_machine(server, capsys, mocker, monkeypatch):
    login(server, capsys)
    monkeypatch.setenv("HAGENT_LOCAL", "1")
    monkeypatch.setattr(warm, "try_warm", lambda argv: None)
    run_remote = mocker.patch("hagent.remote.run_remote")
    local_cli = mocker.patch("hagent.cli.cli")

    warm.main(["issue", "list"])

    run_remote.assert_not_called()
    local_cli.assert_called_once()
