"""Agents with terminal access can run audited commands (the interactive terminal page was removed)."""

from types import SimpleNamespace

from hagent.terminal import run_agent_command


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
