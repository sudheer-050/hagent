"""Task execution engine: Issue -> Agent -> Runtime -> Run result, persisted back to the DB."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from hagent.adapters import get_runtime_class
from hagent.models import Agent, Issue, IssueStatus, Run, RunStatus
from hagent.models import TimelineEvent
from hagent.mcp_client import call_tool_sync, list_tools_sync


def run_issue(session: Session, issue: Issue, agent: Agent, prompt: str | None = None) -> Run:
    """Create and execute a Run for an issue against its assigned agent's runtime.

    On success the issue moves to IN_REVIEW; on failure it's left wherever it was
    (still IN_PROGRESS) so a human/autopilot can see it needs attention.
    """
    runtime = agent.runtime
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt=prompt or issue.description or issue.title)
    session.add(run)
    session.commit()

    session.add(TimelineEvent(issue_id=issue.id, event_type="run_started", detail=f"Agent {agent.name} started a run"))
    session.commit()
    session.refresh(run)

    issue.status = IssueStatus.IN_PROGRESS
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    session.commit()

    runtime_cls = get_runtime_class(runtime.type)
    config = json.loads(runtime.config_json or "{}")
    adapter = runtime_cls(model=runtime.model, config=config)

    tools = []
    executors = {}
    for server in getattr(agent, "mcp_servers", []):
        try:
            for tool in list_tools_sync(server):
                original_name = tool.get("name", "")
                exposed_name = f"{server.name}__{original_name}"
                schema = tool.get("inputSchema") or tool.get("input_schema") or {"type": "object", "properties": {}}
                if str(runtime.type.value) == "claude":
                    tools.append({"name": exposed_name, "description": tool.get("description", ""), "input_schema": schema})
                else:
                    tools.append({"type": "function", "function": {"name": exposed_name, "description": tool.get("description", ""), "parameters": schema}})
                executors[exposed_name] = (server, original_name)
        except Exception as exc:
            session.add(TimelineEvent(issue_id=issue.id, event_type="mcp_error", detail=f"{server.name}: {exc}"))

    def execute_tool(name, arguments):
        server, original_name = executors[name]
        result = call_tool_sync(server, original_name, arguments)
        session.add(TimelineEvent(issue_id=issue.id, event_type="tool_call", detail=f"{name}({json.dumps(arguments)}) -> {result}"))
        session.commit()
        return result

    try:
        result = adapter.run(
            prompt=run.prompt,
            context=agent.instructions,
            tools=tools or None,
            tool_executor=execute_tool if tools else None,
        )
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.token_estimate = len((run.prompt or "").split())
        run.transcript_json = json.dumps({"error": str(exc)})
        run.finished_at = datetime.now(timezone.utc)
        session.add(TimelineEvent(issue_id=issue.id, event_type="run_failed", detail=str(exc)))
        session.commit()
        return run

    run.status = RunStatus.COMPLETED
    run.output = result.output
    run.token_estimate = len((run.prompt or "").split()) + len((run.output or "").split())
    run.transcript_json = json.dumps(result.raw or {})
    run.finished_at = datetime.now(timezone.utc)
    issue.status = IssueStatus.IN_REVIEW
    session.add(TimelineEvent(issue_id=issue.id, event_type="run_completed", detail=f"Estimated {run.token_estimate} tokens"))
    session.commit()
    return run
