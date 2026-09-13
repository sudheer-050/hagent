"""Task execution engine: Issue -> Agent -> Runtime -> Run result, persisted back to the DB."""

import json
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hagent.adapters import get_runtime_class
from hagent.models import Agent, Issue, IssueStatus, Run, RunStatus, Runtime
from hagent.models import TimelineEvent
from hagent.mcp_client import call_tool_sync, list_tools_sync
from hagent.terminal import run_agent_command
from hagent.rag import add_agent_knowledge


def run_issue(session: Session, issue: Issue, agent: Agent, prompt: str | None = None) -> Run:
    """Create and execute a Run for an issue against its assigned agent's runtime.

    On success the issue moves to IN_REVIEW; on failure it's left wherever it was
    (still IN_PROGRESS) so a human/autopilot can see it needs attention.
    """
    if issue.project.workspace_id != agent.workspace_id or agent.runtime.workspace_id != agent.workspace_id:
        raise ValueError("Issue, agent and runtime must share a workspace")
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

    def cancelled():
        session.refresh(run)
        return run.status == RunStatus.CANCELLED

    def record_tool(name, arguments, result):
        session.add(TimelineEvent(issue_id=issue.id, event_type="tool_call", detail=f"{name}({json.dumps(arguments)}) -> {result}"))
        session.commit()

    backup_runtime = session.get(Runtime, agent.backup_runtime_id) if agent.backup_runtime_id else None

    def record_failover(primary, backup, error):
        session.add(TimelineEvent(
            issue_id=issue.id,
            event_type="runtime_failover",
            detail=f"{primary.name} failed ({error}); handed over to {backup.name}",
        ))
        session.commit()

    try:
        result = execute_agent(agent, run.prompt, cancelled=cancelled, record_tool=record_tool, backup_runtime=backup_runtime, record_failover=record_failover)
    except Exception as exc:
        _finish(session, run, issue, error=str(exc))
        return run
    _finish(session, run, issue, result=result)
    return run


def _finish(session, run, issue, result=None, error=None):
    status = RunStatus.FAILED if error else RunStatus.COMPLETED
    output = result.output if result else None
    tokens = len(run.prompt.split()) + len((output or "").split())
    changed = session.execute(update(Run).where(Run.id == run.id, Run.status == RunStatus.RUNNING).values(
        status=status, output=output, error=error, token_estimate=tokens,
        transcript_json=json.dumps(result.raw or {}) if result else json.dumps({"error": error}),
        finished_at=datetime.now(timezone.utc)), execution_options={"synchronize_session": False}).rowcount
    if changed:
        if not error:
            session.execute(update(Issue).where(Issue.id == issue.id, Issue.status != IssueStatus.CANCELLED).values(status=IssueStatus.IN_REVIEW), execution_options={"synchronize_session": False})
        session.add(TimelineEvent(issue_id=issue.id, event_type="run_failed" if error else "run_completed", detail=error or f"Estimated {tokens} tokens"))
    session.commit()
    session.refresh(run)
    session.refresh(issue)


def cancel_issue(session, issue):
    count = session.execute(update(Run).where(Run.issue_id == issue.id, Run.status.in_([RunStatus.PENDING, RunStatus.RUNNING])).values(status=RunStatus.CANCELLED, error="cancelled by user", finished_at=datetime.now(timezone.utc))).rowcount
    issue.status = IssueStatus.CANCELLED
    session.add(TimelineEvent(issue_id=issue.id, event_type="cancelled", detail=f"Cancelled {count} task(s)"))
    session.commit()
    return count


def execute_agent(
    agent,
    prompt,
    *,
    cancelled=None,
    record_tool=None,
    backup_runtime=None,
    record_failover=None,
):
    """Execute with the agent's primary runtime, then its optional backup.

    Failover happens only when execution raises an exception. A normal model
    response, even an imperfect one, is returned without launching a second
    paid request. Cancellation never starts a backup run.
    """
    prompt = add_agent_knowledge(agent, prompt)
    primary = agent.runtime
    try:
        return _execute_with_runtime(agent, primary, prompt, cancelled, record_tool)
    except Exception as primary_error:
        if not backup_runtime or _is_cancellation(primary_error, cancelled):
            raise
        if backup_runtime.workspace_id != agent.workspace_id:
            raise ValueError("Backup runtime crosses workspaces") from primary_error
        if backup_runtime.archived:
            raise RuntimeError("Configured backup runtime is archived") from primary_error
        if record_failover:
            record_failover(primary, backup_runtime, primary_error)
        try:
            return _execute_with_runtime(agent, backup_runtime, prompt, cancelled, record_tool)
        except Exception as backup_error:
            raise RuntimeError(
                f"Primary runtime {primary.name} failed: {primary_error}; "
                f"backup runtime {backup_runtime.name} failed: {backup_error}"
            ) from backup_error


def _is_cancellation(error, cancelled):
    if "cancel" in str(error).lower():
        return True
    return bool(cancelled and cancelled())


def _execute_with_runtime(agent, runtime, prompt, cancelled, record_tool):
    runtime_cls = get_runtime_class(runtime.type)
    config = json.loads(runtime.config_json or "{}")
    if getattr(agent, "terminal_enabled", False):
        config["terminal_enabled"] = True
        if getattr(agent, "terminal_working_directory", None):
            config["working_directory"] = agent.terminal_working_directory
    adapter = runtime_cls(model=runtime.model, config=config)

    tools = []
    executors = {}
    if getattr(agent, "terminal_enabled", False):
        schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "PowerShell command to execute"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
            },
            "required": ["command"],
        }
        description = "Execute a PowerShell command in this agent's configured working directory. Returns exit code, stdout, and stderr."
        if str(runtime.type.value) == "claude":
            tools.append({"name": "terminal_execute", "description": description, "input_schema": schema})
        else:
            tools.append({"type": "function", "function": {"name": "terminal_execute", "description": description, "parameters": schema}})
        executors["terminal_execute"] = None

    for server in getattr(agent, "mcp_servers", []):
        if server.workspace_id != agent.workspace_id:
            raise ValueError("MCP server crosses workspaces")
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
            raise RuntimeError(f"MCP discovery failed for {server.name}: {exc}") from exc

    def execute_tool(name, arguments):
        if cancelled and cancelled():
            raise RuntimeError("Run cancelled")
        if name == "terminal_execute":
            result = run_agent_command(agent, arguments.get("command", ""), arguments.get("timeout", 120))
            if record_tool:
                record_tool(name, arguments, result)
            return result
        server, original_name = executors[name]
        result = call_tool_sync(server, original_name, arguments)
        if record_tool:
            record_tool(name, arguments, result)
        return result

    context = agent.instructions
    for skill in agent.skills:
        if skill.workspace_id != agent.workspace_id:
            raise ValueError("Skill crosses workspaces")
        context += f"\n\nSkill: {skill.name}\n{skill.content}"
        for file in skill.files:
            context += f"\n--- {file.filename} ---\n{file.content}"
    return adapter.run(
        prompt=prompt,
        context=context,
        tools=tools or None,
        tool_executor=execute_tool if tools else None,
    )
