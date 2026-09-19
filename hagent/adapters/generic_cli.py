"""Config-driven adapter for any CLI that takes a prompt and returns single-shot JSON.

Adding a brand-new CLI-backed AI provider to Hagent previously meant writing a new
Python adapter file (see claude_code.py, codex_cli.py, antigravity_cli.py, gemini_cli.py -
each near-identical). This adapter covers the common shape - "run a binary, send the
prompt, get one JSON object back, pull the answer text out of it" - via runtime config
alone, no code:

    {
      "command": "somecli",
      "args": ["--print", "--output-format", "json"],
      "prompt_mode": "stdin",          # or "arg" to append the prompt as the last argument
      "output_path": "result",         # dotted path into the JSON for the answer text
      "usage_path": "usage",           # optional: dotted path to a {input_tokens,output_tokens}-shaped object
      "session_path": "session_id"     # optional: dotted path to a resumable session/thread id
    }

A CLI whose output is a JSONL event stream (Codex's shape) or that needs bespoke flag
logic (e.g. different flags for a fresh vs. resumed session) still needs its own adapter -
this covers the common case, not every case.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult


def _dig(data: dict, dotted_path: str | None):
    if not dotted_path:
        return None
    value = data
    for key in dotted_path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


class GenericCliRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        command = self.config.get("command")
        if not command:
            raise RuntimeError("generic_cli runtime requires 'command' in its config")
        candidate = Path(command).expanduser()
        executable = str(candidate) if candidate.is_file() else shutil.which(command)
        if not executable:
            raise RuntimeError(f"'{command}' is not installed or not on PATH")

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [executable, *self.config.get("args", [])]
        stdin_input = None
        if self.config.get("prompt_mode", "stdin") == "arg":
            args.append(full_prompt)
        else:
            stdin_input = full_prompt

        try:
            completed = subprocess.run(
                args,
                input=stdin_input,
                cwd=self.config.get("working_directory") or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.get("timeout", 600),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"'{command}' failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"'{command}' failed (exit {completed.returncode}): {detail}")

        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"'{command}' did not return valid JSON on stdout") from exc

        output = _dig(data, self.config.get("output_path", "result"))
        if not isinstance(output, str):
            raise RuntimeError(
                f"'{command}': configured output_path '{self.config.get('output_path', 'result')}' "
                f"did not resolve to a text field in the returned JSON"
            )
        usage_path = self.config.get("usage_path")
        usage = _dig(data, usage_path) if usage_path else None
        session_id = _dig(data, self.config.get("session_path")) if self.config.get("session_path") else None
        raw = dict(data) if isinstance(data, dict) else {"result": data}
        if usage is not None and usage_path != "usage":
            # Normalize onto the exact key name RuntimeResult's auto-extractor looks
            # for, so a custom usage_path still gets picked up automatically.
            raw["usage"] = usage
        return RuntimeResult(output=output, raw=raw, session_id=session_id)
