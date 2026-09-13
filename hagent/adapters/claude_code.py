"""Runtime adapter for an authenticated local Claude Code CLI installation."""

import json
from pathlib import Path
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult


class ClaudeCodeRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        command = self.config.get("command", "claude")
        candidate = Path(command).expanduser()
        executable = str(candidate) if candidate.is_file() else shutil.which(command)
        if not executable:
            raise RuntimeError("Claude Code is not installed or is not on PATH")

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [executable, "--print", full_prompt, "--output-format", "json"]
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        if self.config.get("terminal_enabled"):
            args.append("--dangerously-skip-permissions")
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
            raise RuntimeError(f"Claude Code failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Claude Code failed (exit {completed.returncode}): {detail}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude Code returned invalid JSON") from exc
        return RuntimeResult(output=data.get("result", ""), raw=data)