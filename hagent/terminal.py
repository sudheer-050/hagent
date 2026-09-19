"""Audited command execution for Hagent agents that have terminal access enabled."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


def resolve_working_directory(value: str | None) -> str:
    path = Path(value).expanduser() if value else Path.cwd()
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"Working directory does not exist: {path}")
    return str(path)


def _run_in_docker_sandbox(image: str, cwd: str, command: str, timeout: int) -> subprocess.CompletedProcess:
    """Run the command inside a disposable Docker container with ONLY cwd mounted,
    instead of directly on the host. The container can't see anything on this
    machine outside that one directory - verified in practice, not assumed."""
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError(f"sandbox_image is set to '{image}' but Docker is not installed or not on PATH")
    mount = f"{Path(cwd).resolve()}:/workspace"
    args = [docker, "run", "--rm", "-v", mount, "-w", "/workspace", image, "pwsh", "-NoLogo", "-NonInteractive", "-Command", command]
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
    )


def run_agent_command(agent: Any, command: str, timeout: int = 120, working_directory_override: str | None = None) -> dict[str, Any]:
    """Run one PowerShell command for an explicitly terminal-enabled agent.

    working_directory_override takes precedence over the agent's own configured
    directory - used to route a specific run into its own isolated git worktree
    (see worktrees.py) instead of the agent's shared default directory.

    If the agent has sandbox_image set AND a working_directory_override (a real
    git worktree, not its shared home-directory default) is in play, the command
    runs inside a disposable Docker container with only that worktree mounted,
    instead of directly on the host. There's nothing meaningful to sandbox against
    outside a worktree - mounting the whole home directory would defeat the point,
    and GUI/screen-control work fundamentally needs the real host - so sandboxing
    only ever activates for isolated repo work, never as a blanket agent setting.
    """
    if not getattr(agent, "terminal_enabled", False):
        raise RuntimeError("Terminal access is disabled for this agent")
    if not command or not command.strip():
        raise ValueError("command is required")
    cwd = resolve_working_directory(working_directory_override or getattr(agent, "terminal_working_directory", None))
    limit = max(1, min(int(timeout), 600))

    sandbox_image = getattr(agent, "sandbox_image", None)
    if sandbox_image and working_directory_override:
        completed = _run_in_docker_sandbox(sandbox_image, cwd, command, limit)
        output_limit = 100_000
        return {
            "command": command,
            "cwd": cwd,
            "sandboxed": True,
            "sandbox_image": sandbox_image,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-output_limit:],
            "stderr": completed.stderr[-output_limit:],
            "truncated": len(completed.stdout) > output_limit or len(completed.stderr) > output_limit,
        }

    try:
        agent_environment = json.loads(getattr(agent, "env_json", "{}") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Agent environment JSON is invalid") from exc
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in agent_environment.items()})
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
