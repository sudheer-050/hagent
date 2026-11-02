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
import secrets
import threading
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(tools, tool_executor, token):
    call_lock = threading.Lock()
    context = copy_context()
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
            if self.path == f"/{token}/tools" and not self.headers.get("Origin"):
                self._send_json(200, {"tools": tools})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != f"/{token}/call" or self.headers.get("Origin"):
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    self._send_json(413, {"error": "invalid request size"})
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
                with call_lock:
                    result = context.run(tool_executor, payload.get("name", ""), payload.get("arguments") or {})
                self._send_json(200, {"result": result})
            except Exception as exc:
                self._send_json(200, {"error": str(exc)})

    return Handler


@contextlib.contextmanager
def serve_tools(tools, tool_executor):
    """Yields a loopback base URL serving `tools` for the duration of the block."""
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tools, tool_executor, token))
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/{token}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
