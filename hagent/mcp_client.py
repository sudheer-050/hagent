"""Small async bridge around the official Model Context Protocol SDK.

The engine remains synchronous, so each operation owns a short-lived protocol
connection. This is intentionally conservative for a local orchestrator and
keeps server processes from leaking when a run fails.
"""

import asyncio
import json


def _sdk():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("The mcp package is required for MCP server invocation") from exc
    return ClientSession, StdioServerParameters, stdio_client, sse_client


async def _session(server):
    ClientSession, StdioServerParameters, stdio_client, sse_client = _sdk()
    from hagent.connections import config, credentials
    cfg = config(server)
    private = credentials(server)
    if server.transport == "sse":
        return sse_client(server.url, headers=private.get("headers"), timeout=25, sse_read_timeout=60)
    if server.transport == "http":
        from mcp.client.streamable_http import streamablehttp_client
        return streamablehttp_client(server.url, headers=private.get("headers"), timeout=25, sse_read_timeout=60)
    params = StdioServerParameters(
        command=server.command,
        args=json.loads(server.args_json or "[]"),
        env=private.get("env") or cfg.get("env"),
    )
    return stdio_client(params)


async def list_tools(server) -> list[dict]:
    ClientSession, *_ = _sdk()
    async with await _session(server) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.model_dump() if hasattr(tool, "model_dump") else tool.__dict__ for tool in result.tools]


async def call_tool(server, name: str, arguments: dict) -> dict | str:
    ClientSession, *_ = _sdk()
    async with await _session(server) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if hasattr(result, "model_dump"):
                return result.model_dump()
            return str(result)


def list_tools_sync(server) -> list[dict]:
    if server.transport in {"google", "api"}:
        from hagent.connections import list_tools as connection_tools
        return connection_tools(server)
    return asyncio.run(list_tools(server))


def call_tool_sync(server, name: str, arguments: dict) -> dict | str:
    if server.transport in {"google", "api"}:
        from hagent.connections import call_tool as connection_call
        return connection_call(server, name, arguments)
    return asyncio.run(call_tool(server, name, arguments))
