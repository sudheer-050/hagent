import json
import threading

import pytest

from hagent import warm


@pytest.fixture()
def server(tmp_path, monkeypatch):
    import hagent.cli as cli_module

    monkeypatch.setattr(cli_module, "init_db", cli_module.init_db)  # restored after the server wraps it
    state_path = tmp_path / "warm.json"
    monkeypatch.setattr(warm, "STATE_PATH", state_path)
    instance = warm.WarmServer(state_path)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.close()
    thread.join(timeout=3)


def test_runs_a_command_in_the_warm_process_and_returns_its_output(server, capsys):
    code = warm.try_warm(["--help"])

    assert code == 0
    assert "Hagent: self-hosted multi-agent orchestration" in capsys.readouterr().out


def test_unknown_command_reports_click_error_and_exit_code(server, capsys):
    code = warm.try_warm(["no-such-command"])

    assert code == 2
    assert "No such command" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["daemon", "start"], ["warm", "status"]])
def test_long_running_or_management_commands_never_go_warm(server, argv):
    assert warm.try_warm(argv) is None


def test_falls_back_when_no_server_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(warm, "STATE_PATH", tmp_path / "missing.json")

    assert warm.try_warm(["--help"]) is None


def test_falls_back_when_command_would_use_a_different_database(server, monkeypatch):
    monkeypatch.setenv("HAGENT_DB_PATH", "some-other.db")

    assert warm.try_warm(["--help"]) is None


def test_wrong_token_is_rejected(server, tmp_path):
    state = json.loads(server.state_path.read_text(encoding="utf-8"))
    state["token"] = "0" * 64

    with pytest.raises((OSError, ValueError)):
        warm._request(state, {"op": "ping"}, timeout=2)


def test_stale_code_makes_the_server_decline_and_stop(server, monkeypatch):
    monkeypatch.setattr(warm, "_code_mtime", lambda: server.started_mtime + 10)

    assert warm.try_warm(["--help"]) is None
    assert server.stopping.wait(timeout=3)
