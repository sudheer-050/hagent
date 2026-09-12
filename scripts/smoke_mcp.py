"""Real MCP + real HTTP adapter + persisted agent-run demonstration.

The deterministic local HTTP responder stands in for the language model only.
MCP initialize, tools/list and tools/call use the official SDK and demo process.
Run from the repository root: python scripts/smoke_mcp.py
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from hagent.models import Base, Workspace, Runtime, RuntimeType, Agent, Project, Issue, McpServer, RunStatus
from hagent.engine import run_issue


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
        if tool_messages:
            message = {"role": "assistant", "content": "MCP time result: " + tool_messages[-1]["content"]}
        else:
            assert any(t["function"]["name"] == "demo__get_time" for t in payload["tools"])
            message = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "demo__get_time", "arguments": {}}}]}
        data = json.dumps({"message": message}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'smoke.db'}")
            Base.metadata.create_all(engine)
            with Session(engine, expire_on_commit=False) as session:
                ws = Workspace(name="mcp-smoke"); session.add(ws); session.flush()
                rt = Runtime(workspace_id=ws.id, name="local fixture", type=RuntimeType.OLLAMA, model="fixture", config_json=json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}"}))
                project = Project(workspace_id=ws.id, name="smoke")
                mcp = McpServer(workspace_id=ws.id, name="demo", transport="stdio", command=sys.executable, args_json=json.dumps([str(Path(__file__).with_name("demo_mcp_server.py").resolve())]))
                session.add_all([rt, project, mcp]); session.flush()
                agent = Agent(workspace_id=ws.id, runtime_id=rt.id, name="demo-agent")
                agent.mcp_servers.append(mcp)
                issue = Issue(project_id=project.id, title="Get UTC time through MCP")
                session.add_all([agent, issue]); session.commit()
                run = run_issue(session, issue, agent)
                assert run.status == RunStatus.COMPLETED, run.error
                assert "MCP time result:" in run.output and "isError" in run.output
                assert any(event.event_type == "tool_call" for event in issue.timeline)
                assert len(json.loads(run.transcript_json)["messages"]) == 2
                print(json.dumps({"status": run.status.value, "output": run.output, "transcript_rounds": 2, "protocol": "real MCP stdio", "model": "deterministic local HTTP fixture"}, indent=2))
            engine.dispose()
    finally:
        server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    main()
