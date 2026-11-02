"""Stdio MCP server that proxies list_tools/call_tool to hagent's own loopback
HTTP bridge (see mcp_bridge.py). Spawned directly by a CLI agent runtime (its
`--mcp-config` points a stdio server entry at this script) - deliberately has
no other hagent imports so it stays cheap to launch as a child process.

Usage: python mcp_bridge_stub.py <base_url>
"""

import asyncio
import json
import inspect
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
                **{schema_field: t.get("input_schema") or {"type": "object", "properties": {}}},
            )
            for t in data.get("tools", [])
        ]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params):
        try:
            data = _http_json(f"{base_url}/call", {"name": params.name, "arguments": params.arguments or {}})
            if "error" in data:
                return types.CallToolResult(content=[types.TextContent(type="text", text=str(data["error"]))], **{error_field: True})
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(data.get("result", "")))])
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], **{error_field: True})

    # SDK 1 uses decorators/camelCase; SDK 2 uses callbacks/snake_case.
    schema_field = "inputSchema" if "inputSchema" in types.Tool.model_fields else "input_schema"
    error_field = "isError" if "isError" in types.CallToolResult.model_fields else "is_error"
    if "on_list_tools" in inspect.signature(Server).parameters:
        server = Server("hagent-bridge", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    else:
        server = Server("hagent-bridge")

        @server.list_tools()
        async def list_tools():
            return (await on_list_tools(None, None)).tools

        @server.call_tool()
        async def call_tool(name, arguments):
            return await on_call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
