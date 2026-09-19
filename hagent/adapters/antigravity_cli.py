"""Runtime adapter for an authenticated local Antigravity CLI (agy) installation."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult


def _find_agy(command: str) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(command)
    if found:
        return found
    # Only fall back to the default install location when the caller didn't
    # ask for a specific command - otherwise a deliberately-custom command
    # that fails to resolve would silently run a different agy install.
    if command == "agy":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            installed = Path(local) / "agy" / "bin" / "agy.exe"
            if installed.is_file():
                return str(installed)
    return None


class AntigravityCliRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        executable = _find_agy(self.config.get("command", "agy"))
        if not executable:
            raise RuntimeError("Antigravity CLI (agy) is not installed or is not on PATH")

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [executable, "--print", full_prompt, "--output-format", "json"]
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        if self.config.get("terminal_enabled"):
            args.append("--dangerously-skip-permissions")
        else:
            args.append("--sandbox")
        try:
            completed = subprocess.run(
                args,
                cwd=self.config.get("working_directory") or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.get("timeout", 600),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Antigravity CLI failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Antigravity CLI failed (exit {completed.returncode}): {detail}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Antigravity CLI returned invalid JSON") from exc
        if data.get("status") not in (None, "SUCCESS"):
            raise RuntimeError(f"Antigravity CLI failed: {data.get('status')}")
        usage = data.get("usage") or {}
        return RuntimeResult(
            output=data.get("response", ""),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw=data,
        )
