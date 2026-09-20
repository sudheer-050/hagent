"""Runtime adapter for an authenticated local Codex CLI installation."""

import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from hagent.adapters.base import BaseRuntime, ResumableError, RuntimeResult
from hagent.mcp_bridge import serve_tools

_STUB_PATH = str(Path(__file__).resolve().parent.parent / "mcp_bridge_stub.py")


@contextlib.contextmanager
def _mcp_server_args(tools, tool_executor):
    """Codex CLI only reaches tools through MCP, same as Claude Code - bridge
    whatever tools/tool_executor we were handed (delegation, terminal_execute,
    passthrough MCP tools) through mcp_bridge.py for this one invocation, and
    hand back the `-c mcp_servers....` overrides that register it."""
    if not tools:
        yield []
        return
    with serve_tools(tools, tool_executor) as base_url:
        yield [
            "-c", f"mcp_servers.hagent.command='{sys.executable}'",
            "-c", f"mcp_servers.hagent.args=['{_STUB_PATH}', '{base_url}']",
        ]


def _parse_jsonl_events(stdout: str, fallback_thread_id: str | None = None) -> tuple[list[str], dict | None, str | None]:
    """Shared between a completed run and a timed-out one (subprocess.TimeoutExpired
    still hands back whatever the process had already written to stdout)."""
    messages: list[str] = []
    usage: dict | None = None
    thread_id = fallback_thread_id
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        elif event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                messages.append(item["text"])
        elif event.get("type") == "turn.completed":
            usage = event.get("usage")
    return messages, usage, thread_id


def _find_codex(command: str) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(command)
    if found:
        return found
    # Only fall back to the default install location when the caller didn't
    # ask for a specific command - otherwise a deliberately-custom command
    # that fails to resolve would silently run a different Codex install.
    if command == "codex":
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
        resume_session_id = self.config.get("resume_session_id")
        # 'exec' mode is already non-interactive by design (that's what it's for),
        # so it doesn't need a bypass just to run without a TTY. The bypass flag
        # additionally lifts the *sandbox* itself, giving unrestricted filesystem/
        # command access - that must stay reserved for agents actually granted
        # terminal access. An agent that only has some other MCP tool (delegation,
        # message_user, etc.) still gets it: that tool is a separate registered MCP
        # server (see _mcp_server_args below), not one of Codex's own sandboxed
        # built-in tools, so it isn't affected by --sandbox read-only. Bypassing for
        # every tool-bearing agent used to mean any agent, terminal-enabled or not,
        # got danger-full-access to whatever directory this process happened to be
        # running in.
        needs_bypass = bool(self.config.get("terminal_enabled"))
        if resume_session_id:
            # Resuming a prior session: not --ephemeral (that would refuse to persist/
            # find session files in the first place). 'exec resume' also does NOT accept
            # --sandbox or --color at all (unlike plain 'exec') - passing them fails the
            # whole invocation outright, so they're deliberately omitted below.
            args = [executable, "exec", "resume", resume_session_id, "--json"]
            if needs_bypass:
                args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            # Not --ephemeral: session files need to persist to disk for a later
            # 'issue continue' to be able to resume this exact conversation.
            args = [executable, "exec", "--json"]
            if needs_bypass:
                args.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                args.extend(["--sandbox", "read-only"])
            args.extend(["--color", "never"])
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        if self.config.get("reasoning_effort"):
            args.extend(["-c", f"model_reasoning_effort='{self.config['reasoning_effort']}'"])
        # Prompt goes over stdin ('-'), not as a CLI argument - a long prompt as an
        # argument can exceed the Windows command-line length limit.
        timeout = self.config.get("timeout", 600)
        with _mcp_server_args(tools, tool_executor) as mcp_args:
            args.extend(mcp_args)
            args.append("-")
            try:
                completed = subprocess.run(
                    args,
                    input=full_prompt,
                    cwd=self.config.get("working_directory") or None,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # The process is dead, but whatever it had already streamed to stdout
                # before the timeout is still in exc.stdout - including, often, the
                # thread.started event with the real session id. That id is worth
                # recovering: it means 'issue continue' can actually resume this exact
                # conversation instead of the work being silently lost.
                partial_stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                _, _, recovered_thread_id = _parse_jsonl_events(partial_stdout, resume_session_id)
                raise ResumableError(
                    f"Codex CLI timed out after {timeout}s", session_id=recovered_thread_id
                ) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"Codex CLI failed to start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Codex CLI failed (exit {completed.returncode}): {detail}")

        # --json prints one JSON event per line: agent_message items carry the actual
        # response text, and the final turn.completed event carries real token usage -
        # both were previously lost when stdout was treated as one plain-text blob.
        messages, usage, thread_id = _parse_jsonl_events(completed.stdout, resume_session_id)
        output = "\n".join(messages).strip()
        if not output:
            # Fall back to raw stdout rather than silently returning nothing if the
            # event shape ever changes underneath this parser.
            output = completed.stdout.strip()
        return RuntimeResult(output=output, raw={"usage": usage, "stderr": completed.stderr}, session_id=thread_id)
