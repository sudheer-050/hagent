"""Supported-flag launch helpers for independent Codex and Claude CLI sessions."""
from __future__ import annotations
import json, sys


def build_codex_args(command, route, scope, prompt=None):
    args = [command]
    if route.get("model") and route["model"] != "default":
        args += ["--model", route["model"]]
    if route.get("provider_effort"):
        args += ["-c", f"model_reasoning_effort='{route['provider_effort']}'"]
    args += [
        "-c", f"mcp_servers.hagent_memory.command='{sys.executable}'",
        "-c", "mcp_servers.hagent_memory.args=['-m','hagent.memory_mcp']",
    ]
    for key, value in scope.items():
        if value:
            args += ["-c", f"mcp_servers.hagent_memory.env.{key}='{value}'"]
    if prompt: args.append(prompt)
    return args


def build_claude_args(command, route, mcp_config_path, prompt=None):
    args = [command, "--mcp-config", mcp_config_path]
    if route.get("model") and route["model"] != "default":
        args += ["--model", route["model"]]
    if route.get("provider_effort"):
        args += ["--effort", route["provider_effort"]]
    if prompt: args.append(prompt)
    return args


def claude_mcp_config(scope):
    return {"mcpServers": {"hagent_memory": {
        "command": sys.executable, "args": ["-m", "hagent.memory_mcp"],
        "env": {key: value for key, value in scope.items() if value},
    }}}
