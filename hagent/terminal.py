"""Interactive Windows PTY sessions and audited commands for Hagent agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any


def resolve_working_directory(value: str | None) -> str:
    path = Path(value).expanduser() if value else Path.cwd()
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"Working directory does not exist: {path}")
    return str(path)


def spawn_terminal(cwd: str | None = None, rows: int = 30, cols: int = 120):
    """Spawn an interactive PowerShell through the native Windows ConPTY API."""
    try:
        from winpty import PtyProcess
    except ImportError as exc:
        raise RuntimeError("pywinpty is required for interactive terminals") from exc
    shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = str(powershell) if powershell.is_file() else shell
    return PtyProcess.spawn(
        [command, "-NoLogo", "-NoExit"],
        cwd=resolve_working_directory(cwd),
        dimensions=(max(2, rows), max(2, cols)),
        backend=0,  # winpty.Backend.ConPTY
    )


def run_agent_command(agent: Any, command: str, timeout: int = 120) -> dict[str, Any]:
    """Run one PowerShell command for an explicitly terminal-enabled agent."""
    if not getattr(agent, "terminal_enabled", False):
        raise RuntimeError("Terminal access is disabled for this agent")
    if not command or not command.strip():
        raise ValueError("command is required")
    cwd = resolve_working_directory(getattr(agent, "terminal_working_directory", None))
    try:
        agent_environment = json.loads(getattr(agent, "env_json", "{}") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Agent environment JSON is invalid") from exc
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in agent_environment.items()})
    limit = max(1, min(int(timeout), 600))
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=limit,
        check=False,
    )
    output_limit = 100_000
    return {
        "command": command,
        "cwd": cwd,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-output_limit:],
        "stderr": completed.stderr[-output_limit:],
        "truncated": len(completed.stdout) > output_limit or len(completed.stderr) > output_limit,
    }