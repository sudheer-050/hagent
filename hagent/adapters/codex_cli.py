"""Runtime adapter for an authenticated local Codex CLI installation."""

import os
from pathlib import Path
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult


def _find_codex(command: str) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(command)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        installed = Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        if installed.is_file():
            return str(installed)
    return None


class CodexCliRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        executable = _find_codex(self.config.get("command", "codex"))
        if not executable:
            raise RuntimeError("Codex CLI is not installed or is not on PATH")

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [executable, "exec", "--ephemeral"]
        if self.config.get("terminal_enabled"):
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args.extend(["--sandbox", "read-only"])
        args.extend(["--color", "never"])
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        args.append(full_prompt)
        try:
            completed = subprocess.run(
                args,
                cwd=self.config.get("working_directory") or None,
                capture_output=True,
                text=True,
                timeout=self.config.get("timeout", 600),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Codex CLI failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Codex CLI failed (exit {completed.returncode}): {detail}")
        return RuntimeResult(output=completed.stdout.strip(), raw={"stderr": completed.stderr})