"""Minimal MCP fixture used for local end-to-end tool invocation checks."""

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("hagent-demo")


@mcp.tool()
def get_time() -> str:
    """Return the current UTC time."""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    mcp.run()
