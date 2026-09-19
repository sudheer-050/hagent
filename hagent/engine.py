"""Task execution engine: Issue -> Agent -> Runtime -> Run result, persisted back to the DB."""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session

from hagent.adapters import get_runtime_class
from hagent.adapters.base import ResumableError
from hagent.models import Agent, ChatMessage, ChatThread, Comment, Issue, IssueStatus, Project, ProjectStatus, Repo, RoutingDecision, Run, RunStatus, Runtime, Skill, SkillLesson, UserProfile
from hagent.models import TimelineEvent
from hagent.mcp_client import call_tool_sync, list_tools_sync
from hagent.provider_health import handle_capacity_failure, is_capacity_error
from hagent.terminal import run_agent_command
from hagent.live_activity import start_delegation, end_delegation
from hagent.worktrees import ensure_worktree
from hagent.memory import MemoryScope, MemoryService, context_as_prompt, redact_sensitive
from hagent.router import ModelRouter

_queue_lock = threading.Lock()
log = logging.getLogger(__name__)

# Runtimes whose adapter hands tools to the model via the Anthropic Messages
# API shape ({"name", "description", "input_schema"}) - either directly
# (claude) or, for the CLI adapters, because they bridge tools through MCP
# (see mcp_bridge.py), whose wire format is that same {name, description,
# input_schema} shape. Every other runtime type's adapter expects OpenAI-style
# function-calling tools ({"type": "function", "function": {...}}) instead.
_CLAUDE_SHAPED_RUNTIMES = {"claude", "claude_code", "codex_cli"}


class DelegationLimitExceeded(RuntimeError):
    """The supervisor exhausted its configured subagent-call budget."""


def queue_issue_run(session: Session, issue: Issue, agent: Agent, prompt: str | None = None) -> Run:
    """Persist a pending run so a web request can hand execution to a worker."""
    if issue.project.workspace_id != agent.workspace_id or agent.runtime.workspace_id != agent.workspace_id:
        raise ValueError("Issue, agent and runtime must share a workspace")
    with _queue_lock:
        active_run = session.scalar(
            select(Run)
            .where(Run.issue_id == issue.id, Run.status.in_([RunStatus.PENDING, RunStatus.WAITING_APPROVAL, RunStatus.RUNNING]))
            .order_by(Run.created_at.desc())
        )
        if active_run:
            return active_run
        status = RunStatus.WAITING_APPROVAL if getattr(agent, "require_run_approval", False) else RunStatus.PENDING
        run = Run(issue_id=issue.id, agent_id=agent.id, prompt=prompt or issue.description or issue.title, status=status)
        session.add(run)
        if status == RunStatus.PENDING:
            issue.status = IssueStatus.IN_PROGRESS
        session.flush()
        event_type = "run_approval_requested" if status == RunStatus.WAITING_APPROVAL else "run_queued"
        detail = f"Run for agent {agent.name} is waiting for approval" if status == RunStatus.WAITING_APPROVAL else f"Run queued for agent {agent.name}"
        session.add(TimelineEvent(issue_id=issue.id, event_type=event_type, detail=detail))
        session.commit()
        session.refresh(run)
        return run


def _resolve_worktree(session: Session, issue: Issue, agent: Agent) -> str | None:
    """If the issue's project is linked to a real local git repo and this agent has
    terminal access, give this issue its own git worktree + branch instead of sharing
    the agent's default working directory - keeps concurrent/parallel agent work on
    the same repo from clobbering each other's files. Returns None (no override,
    falls back to the agent's normal terminal_working_directory) whenever a repo
    isn't configured, isn't a real git checkout, or the agent has no terminal access.
    """
    if not getattr(agent, "terminal_enabled", False):
        return None
    project = issue.project
    if not project or not project.repo_id:
        return None
    repo = session.get(Repo, project.repo_id)
    if not repo or not repo.local_path:
        return None
    return ensure_worktree(repo.local_path, issue.id)


def run_issue(
    session: Session,
    issue: Issue,
    agent: Agent,
    prompt: str | None = None,
    *,
    _run: Run | None = None,
    resume_session_id: str | None = None,
) -> Run:
    """Create and synchronously execute a run (used by the CLI and scheduler)."""
    if issue.project.workspace_id != agent.workspace_id or agent.runtime.workspace_id != agent.workspace_id:
        raise ValueError("Issue, agent and runtime must share a workspace")
    run = _run or queue_issue_run(session, issue, agent, prompt)
    if run.status != RunStatus.PENDING:
        return run
    from hagent.recovery import current_owner

    started_at = datetime.now(timezone.utc)
    owner_pid, owner_started = current_owner()
    claimed = session.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == RunStatus.PENDING)
        .values(status=RunStatus.RUNNING, started_at=started_at, owner_pid=owner_pid, owner_started=owner_started),
        execution_options={"synchronize_session": False},
    ).rowcount
    if not claimed:
        session.refresh(run)
        return run
    session.refresh(run)
    issue.status = IssueStatus.IN_PROGRESS
    session.add(TimelineEvent(issue_id=issue.id, event_type="run_started", detail=f"Agent {agent.name} started a run"))
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

    def record_skill_use(member):
        mark_agent_skills_used(session, member)

    working_directory_override = _resolve_worktree(session, issue, agent)

    try:
        result = execute_agent(
            agent,
            run.prompt,
            cancelled=cancelled,
            record_tool=record_tool,
            backup_runtime=backup_runtime,
            record_failover=record_failover,
            record_skill_use=record_skill_use,
            resolve_backup_runtime=lambda member: session.get(Runtime, member.backup_runtime_id) if member.backup_runtime_id else None,
            working_directory_override=working_directory_override,
            resume_session_id=resume_session_id,
            memory_project_id=issue.project_id,
            memory_client="hagent-issue",
            memory_task_metadata={"issue_id": issue.id, "run_id": run.id},
            routing_run_id=run.id,
        )
    except Exception as exc:
        checkpoint_session_id = getattr(exc, "session_id", None)
        _finish(session, run, issue, error=str(exc), checkpoint_session_id=checkpoint_session_id)
        if checkpoint_session_id:
            session.add(TimelineEvent(
                issue_id=issue.id,
                event_type="checkpoint_saved",
                detail=f"Failed, but a resumable session was recovered - 'issue continue {issue.id}' can pick up from here instead of starting over.",
            ))
            session.commit()
        return run
    _finish(session, run, issue, result=result)
    return run


def process_queued_run_by_id(run_id: str) -> Run | None:
    """Worker entry point used after the HTTP response has been sent."""
    from hagent.db import get_session

    with get_session(scoped=False) as session:
        run = session.get(Run, run_id)
        if run is None or run.status != RunStatus.PENDING:
            return run
        issue = session.get(Issue, run.issue_id)
        if issue is None:
            return None
        session.info["workspace_id"] = issue.project.workspace_id
        agent = session.get(Agent, run.agent_id)
        if agent is None:
            return None
        return run_issue(session, issue, agent, _run=run)


def _finish(session, run, issue, result=None, error=None, checkpoint_session_id=None):
    status = RunStatus.FAILED if error else RunStatus.COMPLETED
    output = result.output if result else None
    tokens = len(run.prompt.split()) + len((output or "").split())
    changed = session.execute(update(Run).where(Run.id == run.id, Run.status == RunStatus.RUNNING).values(
        status=status, output=output, error=error, token_estimate=tokens,
        input_tokens=result.input_tokens if result else None,
        output_tokens=result.output_tokens if result else None,
        session_id=result.session_id if result else checkpoint_session_id,
        transcript_json=json.dumps(result.raw or {}) if result else json.dumps({"error": error}),
        finished_at=datetime.now(timezone.utc)), execution_options={"synchronize_session": False}).rowcount
    if changed:
        if not error:
            session.execute(update(Issue).where(Issue.id == issue.id, Issue.status != IssueStatus.CANCELLED).values(status=IssueStatus.IN_REVIEW), execution_options={"synchronize_session": False})
        session.add(TimelineEvent(issue_id=issue.id, event_type="run_failed" if error else "run_completed", detail=error or f"Estimated {tokens} tokens"))
    session.commit()
    session.refresh(run)
    session.refresh(issue)
    if changed and error:
        agent = session.get(Agent, run.agent_id)
        if agent and agent.skills:
            _learn_from_mistake([s.id for s in agent.skills], f'On "{issue.title}": {error}', issue_id=issue.id, source="run_failure")
    if changed and not error:
        from hagent.orchestration import after_issue_finished

        after_issue_finished(session, issue)
    if changed and not error and output:
        _run_verifier_pass(session, run, issue, output)


VERIFICATION_PROJECT_NAME = "Verification Reviews"


def _get_or_create_verification_project(session: Session, workspace_id: str) -> Project:
    project = session.scalar(
        select(Project).where(Project.workspace_id == workspace_id, Project.name == VERIFICATION_PROJECT_NAME)
    )
    if project is None:
        project = Project(
            workspace_id=workspace_id,
            name=VERIFICATION_PROJECT_NAME,
            description="Auto-generated second-pass reviews of completed work. Not a place to assign new work directly.",
            status=ProjectStatus.ACTIVE,
        )
        session.add(project)
        session.flush()
    return project


def _run_verifier_pass(session: Session, run: Run, issue: Issue, output: str) -> None:
    """After a genuinely successful run, if the executing agent has a verifier configured,
    have the verifier independently check the work and comment PASS/FAIL on the original issue.
    Never nested - a review issue lives in the Verification Reviews project, and review issues
    are never themselves auto-verified.
    """
    agent = session.get(Agent, run.agent_id)
    if not agent or not agent.verifier_agent_id or issue.project.name == VERIFICATION_PROJECT_NAME:
        return
    verifier = session.get(Agent, agent.verifier_agent_id)
    if not verifier or verifier.archived or verifier.id == agent.id:
        return
    review_project = _get_or_create_verification_project(session, agent.workspace_id)
    review_issue = Issue(
        project_id=review_project.id,
        title=f"Verify: {issue.title}",
        description=(
            f"Independently review the following completed work for correctness, quality, and "
            f"safety. You did not do this work - {agent.name} did.\n\n"
            f"Original request:\n{run.prompt}\n\n"
            f"Delivered output:\n{output}\n\n"
            f"Respond starting with exactly the word PASS if the work is correct and safe, or "
            f"FAIL: <specific reason> if it has a real problem. Be concrete about what's wrong "
            f"if failing - 'looks fine' or 'seems risky' without a specific defect is not a valid "
            f"FAIL."
        ),
        assignee_agent_id=verifier.id,
    )
    session.add(review_issue)
    session.flush()
    session.commit()
    verify_run = run_issue(session, review_issue, verifier)
    verdict = (verify_run.output or "").strip()
    # Don't require PASS/FAIL as literally the first word - a verifier that adds a
    # sentence of preamble before the verdict (seen in practice, despite being asked
    # not to) still has a real, findable answer. FAIL takes priority if both appear,
    # since the whole point is not to let a hedge slip past a real problem; no
    # recognizable verdict at all is treated as a failure to surface for review
    # rather than silently passing unclear output.
    has_fail = re.search(r"\bFAIL\b", verdict, re.IGNORECASE) is not None
    has_pass = re.search(r"\bPASS\b", verdict, re.IGNORECASE) is not None
    passed = has_pass and not has_fail
    session.add(Comment(issue_id=issue.id, author=f"{verifier.name} (verifier)", body=verdict or "(verifier produced no output)"))
    session.add(TimelineEvent(
        issue_id=issue.id,
        event_type="verification_passed" if passed else "verification_failed",
        detail=verdict[:500] if verdict else "Verifier produced no output",
    ))
    lesson_skill_ids = []
    lesson_text = ""
    if not passed:
        session.execute(
            update(Issue).where(Issue.id == issue.id).values(status=IssueStatus.IN_PROGRESS),
            execution_options={"synchronize_session": False},
        )
        if agent.skills:
            lesson_skill_ids = [s.id for s in agent.skills]
            lesson_text = f'On "{issue.title}", a reviewer found: {verdict}'
    session.commit()
    if lesson_skill_ids:
        _learn_from_mistake(lesson_skill_ids, lesson_text, issue_id=issue.id, source="verifier_reject")


def cancel_issue(session, issue):
    count = session.execute(update(Run).where(Run.issue_id == issue.id, Run.status.in_([RunStatus.PENDING, RunStatus.WAITING_APPROVAL, RunStatus.RUNNING])).values(status=RunStatus.CANCELLED, error="cancelled by user", finished_at=datetime.now(timezone.utc))).rowcount
    issue.status = IssueStatus.CANCELLED
    session.add(TimelineEvent(issue_id=issue.id, event_type="cancelled", detail=f"Cancelled {count} task(s)"))
    session.commit()
    return count


def mark_agent_skills_used(session: Session, agent: Agent) -> None:
    """Record successful agent invocation for every skill included in its context."""
    if not agent.skills:
        return
    used_at = datetime.now(timezone.utc)
    for skill in agent.skills:
        if skill.workspace_id != agent.workspace_id:
            raise ValueError("Skill crosses workspaces")
        skill.last_used_at = used_at
    session.commit()


# How many of a skill's most recent lessons a recall_lessons() tool call returns.
# This bounds a single tool result, not storage. Lessons remain attached to the
# skill until that skill is deleted and can be exported in full at any time.
MAX_LESSONS_PER_SKILL = 30

# Lessons are also mirrored to one plain-text file per skill, named after the
# skill in a workspace-specific directory - a durable, human-readable copy
# outside the database. The agent never has this stuffed into its prompt;
# every skill's context only carries a one-line pointer ("N lessons on file"), and
# the model reads the file itself via the recall_lessons tool only when it decides
# a task looks like something that's failed before. That keeps normal runs exactly
# as cheap as a skill with no history at all. Lessons are retained with the skill.
SKILL_NOTES_DIR = Path(os.environ.get("HAGENT_SKILL_NOTES_DIR", "skill-notes"))


def _skill_notes_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "skill"


def _skill_notes_path(skill: Skill) -> Path:
    # Include workspace and immutable id so equal/renamed skill names never mix
    # histories or leak notes across tenants.
    return SKILL_NOTES_DIR / skill.workspace_id / f"{_skill_notes_slug(skill.name)}-{skill.id[:8]}.md"


def _append_skill_note_file(skill: Skill, source: str, text: str, now: datetime) -> None:
    path = _skill_notes_path(skill)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                f"# {skill.name} — lessons learned\n\n"
                "Distilled notes from real run failures and reviewer rejections. Not injected into "
                "every prompt - the agent reads this on demand (recall_lessons tool) only when it "
                "judges a task is similar to something that's gone wrong before.\n\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as f:
            f.write(f"## {now.strftime('%Y-%m-%d %H:%M UTC')} ({source})\n{text}\n\n")
    except OSError:
        log.warning("Could not write skill notes file %s", path, exc_info=True)


_NON_LEARNABLE_FAILURE = re.compile(
    r"cancel|timed? ?out|timeout|network|connection|offline|unavailable|"
    r"unauthori[sz]ed|forbidden|authentication|credential|not installed|"
    r"missing executable|context (?:window )?(?:overflow|exceeded)", re.I
)


def _learn_from_mistake(skill_ids: list[str], lesson_text: str, *, issue_id: str | None = None,
                        source: str = "run_failure") -> int:
    """Synchronously record a short, redacted, deduplicated skill lesson.

    Infrastructure, cancellation, authentication, capacity and timeout failures
    are not skill lessons. Synchronous persistence prevents a short-lived CLI
    process from exiting while a daemon thread still owns the only copy.
    """
    raw = lesson_text.strip()
    if source == "run_failure" and (is_capacity_error(raw) or _NON_LEARNABLE_FAILURE.search(raw)):
        return 0
    text, _redactions = redact_sensitive(raw)
    if not skill_ids or not text:
        return 0
    text = text[:400]
    from hagent.db import get_session

    notes, recorded = [], 0
    try:
        with get_session(scoped=False) as session:
            now = datetime.now(timezone.utc)
            for skill_id in dict.fromkeys(skill_ids):
                skill = session.get(Skill, skill_id)
                if not skill:
                    continue
                already = session.scalar(
                    select(SkillLesson).where(SkillLesson.skill_id == skill_id, SkillLesson.text == text)
                )
                if already:
                    continue
                session.add(SkillLesson(skill_id=skill_id, issue_id=issue_id, source=source,
                                        text=text, created_at=now))
                session.execute(
                    update(Skill).where(Skill.id == skill_id)
                    .values(improvement_count=Skill.improvement_count + 1, improved_at=now),
                    execution_options={"synchronize_session": False},
                )
                notes.append((skill, source, text, now))
                recorded += 1
            session.commit()
        # The database is canonical. Mirror files are written only after commit,
        # so a failed transaction can never leave a phantom lesson on disk.
        for skill, note_source, note_text, created_at in notes:
            _append_skill_note_file(skill, note_source, note_text, created_at)
        return recorded
    except Exception:
        log.warning("Skill lesson recording failed", exc_info=True)
        return 0


def execute_agent(
    agent,
    prompt,
    *,
    cancelled=None,
    record_tool=None,
    backup_runtime=None,
    record_failover=None,
    resolve_backup_runtime=None,
    record_skill_use=None,
    delegation_path=(),
    delegation_budget=None,
    working_directory_override=None,
    resume_session_id=None,
    memory_project_id=None,
    memory_user_id=None,
    memory_client="hagent",
    memory_task_metadata=None,
    routing_override=None,
    routing_run_id=None,
):
    """Execute with the agent's primary runtime, then its optional backup.

    Failover happens only when execution raises an exception. A normal model
    response, even an imperfect one, is returned without launching a second
    paid request. Cancellation never starts a backup run. resume_session_id only
    ever applies to the primary runtime - a session id from one provider means
    nothing to a different provider's backup runtime. The configured backup is
    resolved here so every execution entry point gets the same failover behavior,
    including chat and direct engine callers.
    """
    original_prompt = prompt
    session = object_session(agent)
    primary = agent.runtime
    memory_service = None
    memory_session_id = None
    memory_strict = False
    routing = None
    route_started = time.monotonic()
    if session is not None:
        try:
            router = ModelRouter(session, agent.workspace_id, memory_project_id)
            routing = router.route(original_prompt, default_runtime=agent.runtime,
                override=routing_override, run_id=routing_run_id,
                tool_depth=len(delegation_path or ()), persist=True)
            primary = session.get(Runtime, routing["runtime_id"])
            if primary is None:
                raise RuntimeError("Selected routing runtime is unavailable")
        except Exception as exc:
            policy = ModelRouter(session, agent.workspace_id, memory_project_id).policy()
            if policy is not None:
                raise
            log.warning("Model routing unavailable; using the agent runtime: %s", type(exc).__name__)
            primary = agent.runtime
        try:
            owner = memory_user_id
            if not owner:
                profile = session.scalar(select(UserProfile))
                owner = profile.id if profile else "local-user"
            memory_service = MemoryService(session, MemoryScope(
                workspace_id=agent.workspace_id, user_id=owner,
                project_id=memory_project_id, agent_id=agent.id,
                provider=primary.type.value))
            memory_strict = memory_service.settings().strict_mode
            started = memory_service.start_session(task=original_prompt, client=memory_client,
                metadata=memory_task_metadata or {})
            memory_session_id = started.get("session_id")
            if routing and memory_session_id:
                decision_row = session.get(RoutingDecision, routing["id"])
                if decision_row:
                    decision_row.memory_session_id = memory_session_id
                    session.commit()
            if memory_session_id:
                memory_service.record_event(memory_session_id, role="user", content=original_prompt)
            prompt = original_prompt + context_as_prompt(started.get("context") or {})
        except Exception as exc:
            log.warning("Memory start/context unavailable; continuing without memory: %s", type(exc).__name__)
            if memory_strict: raise
            memory_service = None
            memory_session_id = None
            prompt = original_prompt
    delegation_path = delegation_path or (agent.id,)
    if delegation_budget is None:
        delegation_budget = {"limit": max(0, min(50, int(getattr(agent, "delegation_limit", 8)))), "used": 0}
    if backup_runtime is None and getattr(agent, "backup_runtime_id", None):
        if session is not None:
            backup_runtime = session.get(Runtime, agent.backup_runtime_id)
    if routing and session is not None:
        policy = ModelRouter(session, agent.workspace_id, memory_project_id).policy()
        if routing.get("fallback"):
            fallback_runtime = session.get(Runtime, routing["fallback"][0]["runtime_id"])
            if fallback_runtime is not None:
                backup_runtime = fallback_runtime
        elif policy is not None and policy.max_cost_usd is not None:
            # A configured spend cap forbids an unpriced or more-expensive silent fallback.
            backup_runtime = None
    route_config = {
        "model": routing.get("model") if routing else None,
        "effort": routing.get("provider_effort") if routing else None,
        "output_limit": routing.get("output_limit") if routing else None,
        "timeout": routing.get("timeout_seconds") if routing else None,
    }

    def finish_observers(result=None, error=None, outcome=None):
        if routing and session is not None:
            try:
                ModelRouter(session, agent.workspace_id, memory_project_id).finalize(
                    routing["id"], input_tokens=getattr(result, "input_tokens", None),
                    output_tokens=getattr(result, "output_tokens", None),
                    latency_ms=int((time.monotonic() - route_started) * 1000),
                    outcome=outcome or ("failed" if error else "completed"))
            except Exception as exc:
                log.warning("Routing audit finalization failed: %s", type(exc).__name__)
        if memory_service and memory_session_id:
            try:
                if result is not None:
                    memory_service.record_event(memory_session_id, role="assistant",
                        content=result.output or "")
                summary = (result.output or "")[:2000] if result is not None else ""
                unresolved = str(error)[:1000] if error else ""
                memory_service.finish_session(memory_session_id, summary=summary,
                    unresolved_state=unresolved)
            except Exception as exc:
                log.warning("Memory session finalization failed: %s", type(exc).__name__)
                if memory_strict: raise
    try:
        result = _execute_with_runtime(agent, primary, prompt, cancelled, record_tool,
            delegation_path, resolve_backup_runtime, delegation_budget, record_skill_use,
            working_directory_override, resume_session_id, route_config)
    except DelegationLimitExceeded:
        # A configured cost guard is a deliberate stop, not a provider error:
        # do not silently spend on the backup runtime after the budget is used.
        finish_observers(error="delegation limit exceeded")
        raise
    except Exception as primary_error:
        handle_capacity_failure(agent, primary, primary_error)
        if not backup_runtime or _is_cancellation(primary_error, cancelled):
            finish_observers(error=primary_error)
            raise
        if backup_runtime.workspace_id != agent.workspace_id:
            raise ValueError("Backup runtime crosses workspaces") from primary_error
        if backup_runtime.archived:
            raise RuntimeError("Configured backup runtime is archived") from primary_error
        if record_failover:
            record_failover(primary, backup_runtime, primary_error)
        try:
            fallback_config = None
            if routing and routing.get("fallback") and backup_runtime.id == routing["fallback"][0]["runtime_id"]:
                fallback_route = routing["fallback"][0]
                fallback_config = {
                    "model": fallback_route.get("model"),
                    "effort": fallback_route.get("provider_effort"),
                    "output_limit": routing.get("output_limit"),
                    "timeout": routing.get("timeout_seconds"),
                }
            result = _execute_with_runtime(agent, backup_runtime, prompt, cancelled, record_tool,
                delegation_path, resolve_backup_runtime, delegation_budget, record_skill_use,
                working_directory_override, route_config=fallback_config)
            if record_skill_use:
                record_skill_use(agent)
            finish_observers(result=result, outcome="fallback_completed")
            return result
        except Exception as backup_error:
            # If the primary recovered a resumable session id before failing, don't
            # lose it just because the backup also failed - a future retry on the
            # primary runtime can still pick up from that checkpoint.
            recovered_session_id = getattr(primary_error, "session_id", None)
            combined = ResumableError(
                f"Primary runtime {primary.name} failed: {primary_error}; "
                f"backup runtime {backup_runtime.name} failed: {backup_error}",
                session_id=recovered_session_id,
            )
            finish_observers(error=combined)
            raise combined from backup_error
    if record_skill_use:
        record_skill_use(agent)
    finish_observers(result=result)
    return result


def _is_cancellation(error, cancelled):
    if "cancel" in str(error).lower():
        return True
    return bool(cancelled and cancelled())


def _execute_with_runtime(agent, runtime, prompt, cancelled, record_tool, delegation_path=(), resolve_backup_runtime=None, delegation_budget=None, record_skill_use=None, working_directory_override=None, resume_session_id=None, route_config=None):
    runtime_cls = get_runtime_class(runtime.type)
    config = json.loads(runtime.config_json or "{}")
    effective_working_directory = working_directory_override or getattr(agent, "terminal_working_directory", None)
    if getattr(agent, "terminal_enabled", False):
        config["terminal_enabled"] = True
        if effective_working_directory:
            config["working_directory"] = effective_working_directory
    if resume_session_id:
        config["resume_session_id"] = resume_session_id
    route_config = route_config or {}
    if route_config.get("effort"):
        config["reasoning_effort"] = route_config["effort"]
    if route_config.get("output_limit"):
        config["max_tokens"] = route_config["output_limit"]
    if route_config.get("timeout"):
        config["timeout"] = route_config["timeout"]
    if config.get("worker"):
        # This runtime lives on another device: hand the call to that worker's queue.
        from hagent.adapters.remote_worker import RemoteWorkerRuntime

        adapter = RemoteWorkerRuntime(model=route_config.get("model") or runtime.model, config=config, runtime_type=runtime.type.value)
    else:
        adapter = runtime_cls(model=route_config.get("model") or runtime.model, config=config)

    tools = []
    executors = {}
    delegates = {}
    status_lookups = {}
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
        if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
            tools.append({"name": "terminal_execute", "description": description, "input_schema": schema})
        else:
            tools.append({"type": "function", "function": {"name": "terminal_execute", "description": description, "parameters": schema}})
        executors["terminal_execute"] = None

    # Every agent can text the workspace owner directly - for a question, a
    # recommendation, or an issue worth flagging that doesn't need a formal
    # approval gate (require_run_approval already covers that separately).
    # This lands in the owner's Chat inbox, not buried in a run's tool log.
    message_user_schema = {
        "type": "object",
        "properties": {"message": {"type": "string", "description": "The question, recommendation, or update to send the workspace owner. Be direct and specific."}},
        "required": ["message"],
    }
    message_user_description = (
        "Send a direct message to the workspace owner - a question you need answered, a recommendation, "
        "or something worth flagging - outside this run's normal output. Use this instead of guessing when "
        "you're blocked on a decision only the owner can make. Do not use this for run-approval gates; those "
        "are handled separately and automatically."
    )
    if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
        tools.append({"name": "message_user", "description": message_user_description, "input_schema": message_user_schema})
    else:
        tools.append({"type": "function", "function": {"name": "message_user", "description": message_user_description, "parameters": message_user_schema}})
    executors["message_user"] = None

    # Full lesson history per skill lives on disk (see _skill_notes_path), not in
    # this prompt - each skill's context above only carries a one-line pointer when
    # it has any. Only expose the tool, and only list skills that actually have a
    # file, so an agent whose skills have no history yet pays nothing extra at all.
    skills_with_lessons = [s for s in agent.skills if s.improvement_count]
    if skills_with_lessons:
        recall_schema = {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "enum": [s.name for s in skills_with_lessons], "description": "Which of your skills to check past mistakes for"},
            },
            "required": ["skill_name"],
        }
        recall_description = (
            "Read the full history of lessons learned from real past mistakes for one of your skills. Call this "
            "before relying on a skill for a task that looks similar to something that has failed before - not on "
            "every task."
        )
        if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
            tools.append({"name": "recall_lessons", "description": recall_description, "input_schema": recall_schema})
        else:
            tools.append({"type": "function", "function": {"name": "recall_lessons", "description": recall_description, "parameters": recall_schema}})
        executors["recall_lessons"] = None

    # A squad is an executable team: expose each eligible colleague as a
    # focused delegation tool to tool-calling runtimes. Keep a clean context
    # boundary by passing only the delegated task, not the supervisor's trace.
    if len(delegation_path) < 4 and delegation_budget and delegation_budget["limit"] > 0:
        colleagues = {}
        for membership in getattr(agent, "squad_memberships", []):
            squad = membership.squad
            if squad.archived:
                continue
            for teammate_membership in squad.members:
                teammate = teammate_membership.agent
                if (
                    teammate.id == agent.id
                    or teammate.id in delegation_path
                    or teammate.archived
                    or teammate.workspace_id != agent.workspace_id
                ):
                    continue
                colleagues[teammate.id] = (teammate, teammate_membership.role)
        for teammate, role in sorted(colleagues.values(), key=lambda item: item[0].name.casefold()):
            tool_name = f"delegate_{teammate.id.replace('-', '')}"
            description = (
                f"Ask squad teammate {teammate.name} ({role}) to complete a focused subtask. "
                f"Expertise: {teammate.description or teammate.instructions or 'specialist agent'}. "
                "Provide all facts and constraints needed; this teammate receives the task in a separate context."
            )
            schema = {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "The complete, focused task for the teammate"}},
                "required": ["task"],
            }
            if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
                tools.append({"name": tool_name, "description": description, "input_schema": schema})
            else:
                tools.append({"type": "function", "function": {"name": tool_name, "description": description, "parameters": schema}})
            delegates[tool_name] = teammate

            # A live read, not a delegation: answers "what is X doing right now" from
            # this agent's actual current issue/run instead of asking them (slow, and
            # tempts the model to reuse a stale answer from earlier in the
            # conversation instead of checking again). Free - doesn't touch the
            # delegation budget above.
            status_tool_name = f"status_{teammate.id.replace('-', '')}"
            status_description = (
                f"Check what {teammate.name} ({role}) is actually doing right now - their live "
                "current issue/run, not a delegated task and not a memory of an earlier answer in "
                "this conversation. Always call this fresh for a status question; a prior answer "
                "in this same chat may be stale or from before this tool existed."
            )
            status_schema = {"type": "object", "properties": {}}
            if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
                tools.append({"name": status_tool_name, "description": status_description, "input_schema": status_schema})
            else:
                tools.append({"type": "function", "function": {"name": status_tool_name, "description": status_description, "parameters": status_schema}})
            status_lookups[status_tool_name] = teammate

    for server in getattr(agent, "mcp_servers", []):
        if server.workspace_id != agent.workspace_id:
            raise ValueError("MCP server crosses workspaces")
        try:
            for tool in list_tools_sync(server):
                original_name = tool.get("name", "")
                exposed_name = f"{server.name}__{original_name}"
                schema = tool.get("inputSchema") or tool.get("input_schema") or {"type": "object", "properties": {}}
                if str(runtime.type.value) in _CLAUDE_SHAPED_RUNTIMES:
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
            result = run_agent_command(
                agent,
                arguments.get("command", ""),
                arguments.get("timeout", 120),
                working_directory_override=effective_working_directory,
            )
            if record_tool:
                record_tool(name, arguments, result)
            return result
        if name == "message_user":
            text = str(arguments.get("message", "")).strip()
            if not text:
                raise ValueError("Message to the workspace owner cannot be empty")
            sess = object_session(agent)
            result = "Message sent to the workspace owner."
            if sess is not None:
                thread = sess.scalars(select(ChatThread).where(ChatThread.agent_id == agent.id)).first()
                if thread is None:
                    thread = ChatThread(workspace_id=agent.workspace_id, agent_id=agent.id)
                    sess.add(thread)
                    sess.flush()
                sess.add(ChatMessage(thread_id=thread.id, role="agent", content=text))
                thread.unread = True
                sess.commit()
            else:
                result = "Could not deliver the message - no active database session."
            if record_tool:
                record_tool(name, arguments, result)
            return result
        if name == "recall_lessons":
            skill_name = str(arguments.get("skill_name", "")).strip()
            match = next((s for s in agent.skills if s.name == skill_name), None)
            if not match:
                result = f'No skill named "{skill_name}" on this agent.'
            else:
                # The full history is retained with the skill (DB row + mirror file for
                # export/browsing), but a single tool call only returns the most recent
                # MAX_LESSONS_PER_SKILL - bounded token cost even for a skill with a
                # long history, same guarantee as the old always-on injection had.
                sess = object_session(agent)
                recent = sess.scalars(
                    select(SkillLesson).where(SkillLesson.skill_id == match.id)
                    .order_by(SkillLesson.created_at.desc()).limit(MAX_LESSONS_PER_SKILL)
                ).all() if sess else []
                if not recent:
                    result = "No lessons on file for this skill yet."
                else:
                    lines = [f"- ({lesson.created_at.strftime('%Y-%m-%d')}) {lesson.text}" for lesson in reversed(recent)]
                    total = match.improvement_count
                    header = f"Most recent {len(recent)} of {total} lesson(s) on file:\n" if total > len(recent) else ""
                    result = header + "\n".join(lines)
            if record_tool:
                record_tool(name, arguments, result[:2000])
            return result
        if name in delegates:
            teammate = delegates[name]
            task = str(arguments.get("task", "")).strip()
            if not task:
                raise ValueError("Delegated task cannot be empty")
            if teammate.require_run_approval:
                message = f"{teammate.name} requires manual run approval; create an issue assigned to that agent instead of delegating around the approval gate."
                if record_tool:
                    record_tool(f"delegation_blocked:{teammate.name}", {"task": task}, message)
                return message
            if delegation_budget["used"] >= delegation_budget["limit"]:
                message = f"Delegation limit ({delegation_budget['limit']}) reached; no additional specialist call was started."
                if record_tool:
                    record_tool(f"delegation_budget_exhausted:{teammate.name}", {"task": task}, message)
                raise DelegationLimitExceeded(message)
            delegation_budget["used"] += 1
            if cancelled and cancelled():
                raise RuntimeError("Run cancelled")
            if teammate.runtime.workspace_id != agent.workspace_id:
                raise ValueError("Squad teammate runtime crosses workspaces")
            delegated_prompt = f"Delegated by {agent.name}. Complete this focused task and return concise findings or results:\n\n{task}"
            delegated_backup = resolve_backup_runtime(teammate) if resolve_backup_runtime else None
            if record_tool:
                record_tool(f"delegation_started:{teammate.name}", {"task": task}, "")
            start_delegation(agent.id, teammate.id)
            try:
                result = execute_agent(
                    teammate,
                    delegated_prompt,
                    cancelled=cancelled,
                    record_tool=record_tool,
                    backup_runtime=delegated_backup,
                    resolve_backup_runtime=resolve_backup_runtime,
                    delegation_path=(*delegation_path, teammate.id),
                    delegation_budget=delegation_budget,
                    record_skill_use=record_skill_use,
                    working_directory_override=effective_working_directory,
                )
            except Exception as exc:
                if record_tool:
                    record_tool(f"delegation_failed:{teammate.name}", {"task": task}, str(exc))
                raise
            finally:
                end_delegation(agent.id)
            output = result.output or "The delegated agent returned an empty response."
            if record_tool:
                record_tool(f"delegation:{teammate.name}", {"task": task}, output)
            return output
        if name in status_lookups:
            teammate = status_lookups[name]
            sess = object_session(agent)
            active_issue = sess.scalars(
                select(Issue)
                .where(Issue.assignee_agent_id == teammate.id, Issue.status.in_([IssueStatus.IN_PROGRESS, IssueStatus.IN_REVIEW]))
                .order_by(Issue.updated_at.desc())
            ).first()
            if active_issue:
                result = f'{teammate.name} is currently on "{active_issue.title}" (status: {active_issue.status.value}).'
            else:
                latest_run = sess.scalars(
                    select(Run).where(Run.agent_id == teammate.id).order_by(Run.created_at.desc())
                ).first()
                if latest_run:
                    when = latest_run.finished_at or latest_run.started_at or latest_run.created_at
                    result = (
                        f"{teammate.name} has no active issue right now. Most recent run: "
                        f'"{latest_run.issue.title}" ({latest_run.status.value}), {when}.'
                    )
                else:
                    result = f"{teammate.name} has no recorded work yet."
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
        if skill.improvement_count:
            context += (
                f"\n{skill.improvement_count} lesson(s) learned from real past mistakes with this skill are on "
                f'file - if this task looks like something that has gone wrong before, call recall_lessons("{skill.name}") '
                "before proceeding."
            )
        for file in skill.files:
            context += f"\n--- {file.filename} ---\n{file.content}"
    return adapter.run(
        prompt=prompt,
        context=context,
        tools=tools or None,
        tool_executor=execute_tool if tools else None,
    )
