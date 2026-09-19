"""Stdio MCP server that proxies list_tools/call_tool to hagent's own loopback
HTTP bridge (see mcp_bridge.py). Spawned directly by a CLI agent runtime (its
`--mcp-config` points a stdio server entry at this script) - deliberately has
no other hagent imports so it stays cheap to launch as a child process.

Usage: python mcp_bridge_stub.py <base_url>
"""

import asyncio
import json
import sys
import urllib.request

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def _http_json(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


async def main() -> None:
    base_url = sys.argv[1].rstrip("/")

    async def on_list_tools(ctx, params):
        data = _http_json(f"{base_url}/tools")
        tools = [
            types.Tool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("input_schema") or {"type": "object", "properties": {}},
            )
            for t in data.get("tools", [])
        ]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params):
        try:
            data = _http_json(f"{base_url}/call", {"name": params.name, "arguments": params.arguments or {}})
            if "error" in data:
                return types.CallToolResult(content=[types.TextContent(type="text", text=str(data["error"]))], is_error=True)
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(data.get("result", "")))])
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], is_error=True)

    server = Server("hagent-bridge", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
