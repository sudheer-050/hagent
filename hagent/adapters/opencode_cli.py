"""Runtime adapter for an authenticated local opencode CLI installation."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from hagent.adapters.base import BaseRuntime, RuntimeResult

# Without terminal access the agent may only read and answer; opencode's own
# permission system enforces this, not the prompt.
_READ_ONLY_PERMISSIONS = {"permission": {"bash": "deny", "edit": "deny", "webfetch": "deny"}}


def _find_opencode(command: str) -> str | None:
    candidate = Path(command).expanduser()
    found = str(candidate) if candidate.is_file() else shutil.which(command)
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # The npm shim would route the prompt through cmd.exe, which interprets
        # characters such as & | % and newlines. Use the native binary it wraps.
        native = path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        return str(native) if native.is_file() else None
    return found


def parse_events(stdout: str) -> tuple[str, dict]:
    """Collapse opencode's JSON event stream into (assistant text, metadata)."""
    texts: list[str] = []
    session_id = None
    input_tokens = output_tokens = 0
    saw_tokens = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue  # log lines and anything else that isn't an event
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = event.get("sessionID") or session_id
        part = event.get("part") or {}
        kind = event.get("type")
        if kind == "text" and part.get("text"):
            texts.append(part["text"])
        elif kind == "step_finish" and isinstance(part.get("tokens"), dict):
            saw_tokens = True
            input_tokens += int(part["tokens"].get("input") or 0)
            output_tokens += int(part["tokens"].get("output") or 0)
        elif kind == "error":
            error = event.get("error") or part.get("error") or {}
            detail = error.get("message") or (error.get("data") or {}).get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"opencode reported an error: {detail or line[:300]}")
    return "\n".join(texts).strip(), {
        "session_id": session_id,
        "input_tokens": input_tokens if saw_tokens else None,
        "output_tokens": output_tokens if saw_tokens else None,
    }


class OpencodeCliRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        executable = _find_opencode(self.config.get("command", "opencode"))
        if not executable:
            raise RuntimeError(
                "opencode is not installed, is not on PATH, or only its .cmd shim was found "
                "(set the runtime's command to the native opencode.exe)"
            )

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        args = [executable, "run", full_prompt, "--format", "json"]
        if self.model and self.model != "default":
            args.extend(["-m", self.model])
        resume_session_id = self.config.get("resume_session_id")
        if resume_session_id:
            args.extend(["-s", resume_session_id])

        env = os.environ.copy()
        if not self.config.get("terminal_enabled"):
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(_READ_ONLY_PERMISSIONS)
        try:
            completed = subprocess.run(
                args,
                cwd=self.config.get("working_directory") or None,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.get("timeout", 600),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"opencode failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"opencode failed (exit {completed.returncode}): {detail[:1000]}")
        output, meta = parse_events(completed.stdout)
        if not output:
            raise RuntimeError("opencode returned no text")
        return RuntimeResult(
            output=output,
            input_tokens=meta["input_tokens"],
            output_tokens=meta["output_tokens"],
            session_id=meta["session_id"],
        )
