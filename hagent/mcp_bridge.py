"""Ad hoc MCP server exposing hagent's own tools (delegation, terminal, passthrough
MCP tools) for the lifetime of a single CLI-based agent run.

CLI runtimes (Claude Code, Codex, etc.) run as a subprocess and can only call
tools through MCP - they have no way to call back into a Python closure. This
module runs a small loopback HTTP server *inside* the already-running hagent
process, exposing the tool list and a call endpoint that forwards straight
into the real `tool_executor` closure (the same one used by API-based
runtimes), so tool state (delegation budget, cancellation, working directory)
stays in one place instead of being re-derived in a second process.

The CLI subprocess reaches this HTTP server through `mcp_bridge_stub.py`, a
tiny stdio-MCP-to-HTTP proxy the CLI spawns itself per its `--mcp-config`.
"""

import contextlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(tools, tool_executor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence stdlib access logging
            pass

        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/tools":
                self._send_json(200, {"tools": tools})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/call":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = tool_executor(payload.get("name", ""), payload.get("arguments") or {})
                self._send_json(200, {"result": result})
            except Exception as exc:
                self._send_json(200, {"error": str(exc)})

    return Handler


@contextlib.contextmanager
def serve_tools(tools, tool_executor):
    """Yields a loopback base URL serving `tools` for the duration of the block."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(tools, tool_executor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
