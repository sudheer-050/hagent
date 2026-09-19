"""Runtime adapter for Google's locally installed Gemini CLI."""

import json
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult


class GeminiCliRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        command = self.config.get("command", "gemini")
        executable = shutil.which(command)
        if not executable:
            raise RuntimeError(
                "Gemini CLI is not installed or is not on PATH "
                "(install from https://github.com/google-gemini/gemini-cli)"
            )

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [
            executable,
            "--model",
            self.model,
            "--output-format",
            "json",
            "--prompt",
            full_prompt,
        ]
        if self.config.get("terminal_enabled"):
            args.append("--yolo")
        try:
            completed = subprocess.run(
                args,
                cwd=self.config.get("working_directory") or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.get("timeout", 300),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Gemini CLI failed to start: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Gemini CLI failed (exit {completed.returncode}): {detail}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini CLI returned invalid JSON") from exc
        if data.get("error"):
            raise RuntimeError(f"Gemini CLI failed: {data['error']}")
        return RuntimeResult(output=data.get("response", ""), raw=data)
