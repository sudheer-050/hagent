"""Runtime adapter for an authenticated local Claude Code CLI installation."""

import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from hagent.adapters.base import BaseRuntime, RuntimeResult
from hagent.mcp_bridge import serve_tools

_STUB_PATH = str(Path(__file__).resolve().parent.parent / "mcp_bridge_stub.py")


@contextlib.contextmanager
def _mcp_config_file(tools, tool_executor):
    """The CLI only ever calls tools through MCP, so any tools/tool_executor we
    were handed (delegation, terminal_execute, passthrough MCP tools) have to be
    exposed as an ad hoc MCP server for this one invocation - see mcp_bridge.py."""
    if not tools:
        yield None
        return
    with serve_tools(tools, tool_executor) as base_url:
        config = {"mcpServers": {"hagent": {"command": sys.executable, "args": [_STUB_PATH, base_url]}}}
        fd, path = tempfile.mkstemp(suffix=".json", prefix="hagent-mcp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(config, f)
            yield path
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)


class ClaudeCodeRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        command = self.config.get("command", "claude")
        candidate = Path(command).expanduser()
        executable = str(candidate) if candidate.is_file() else shutil.which(command)
        if not executable:
            raise RuntimeError("Claude Code is not installed or is not on PATH")

        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        # Prompt goes over stdin, not as a CLI argument - a long prompt as an argument
        # can exceed the Windows command-line length limit and fail before Claude Code
        # even starts (seen in practice: "The command line is too long").
        args = [executable, "--print", "--output-format", "json"]
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        if self.config.get("reasoning_effort"):
            args.extend(["--effort", self.config["reasoning_effort"]])
        resume_session_id = self.config.get("resume_session_id")
        if resume_session_id:
            args.extend(["--resume", resume_session_id])

        with _mcp_config_file(tools, tool_executor) as mcp_config_path:
            if mcp_config_path:
                args.extend(["--mcp-config", mcp_config_path, "--strict-mcp-config"])
                # Claude Code defers most tools behind its own ToolSearch step rather
                # than putting them all straight in context; left to guess, ToolSearch
                # reliably picks the wrong (built-in) tool over ours. Naming our tools
                # explicitly here is what makes ToolSearch actually find and load them.
                args.extend(["--allowedTools", *[f"mcp__hagent__{tool['name']}" for tool in tools]])
            # --print mode has no TTY to answer a permission prompt from. Terminal-
            # enabled agents need the full bypass (they're expected to run shell
            # commands). Agents WITHOUT terminal access must not get it just because
            # they were handed some other MCP tool (delegation, message_user, etc.) -
            # --allowedTools above already pre-approves exactly those named tools
            # without needing to also unlock Claude Code's own Read/Write/Edit/Bash
            # tools. Skipping permissions here for every tool-bearing agent used to
            # mean any agent, terminal-enabled or not, could write to whatever
            # directory this process happened to be running in.
            if self.config.get("terminal_enabled"):
                args.append("--dangerously-skip-permissions")
            try:
                completed = subprocess.run(
                    args,
                    input=full_prompt,
                    cwd=self.config.get("working_directory") or None,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
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
        return RuntimeResult(output=data.get("result", ""), raw=data, session_id=data.get("session_id"))
