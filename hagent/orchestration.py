"""Project-level orchestration: starting agents when work is handed to them, and waking a parent
issue's agent when a stage of its sub-issues has finished.

Stages work like this: sub-issues that share a parent and a `stage` number form a barrier group.
Once every issue in a group is finished (in review, done or cancelled) the parent's assignee gets a
new run whose prompt summarises the group, and decides what happens next (usually: review the
results and create the next stage). Sub-issues without a stage never wake the parent.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from hagent.models import Issue, IssueStatus, Run, RunStatus, TimelineEvent

log = logging.getLogger(__name__)

FINISHED_STATUSES = {IssueStatus.IN_REVIEW, IssueStatus.DONE, IssueStatus.CANCELLED}
CLOSED_STATUSES = {IssueStatus.DONE, IssueStatus.CANCELLED}
# Moving an issue into one of these while it has an agent assignee starts that agent.
AUTO_START_STATUSES = {IssueStatus.TODO, IssueStatus.IN_PROGRESS}
ACTIVE_RUN_STATUSES = [RunStatus.PENDING, RunStatus.WAITING_APPROVAL, RunStatus.RUNNING]
PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
SUMMARY_OUTPUT_CHARS = 600

IDLE_SWEEP_INTERVAL_SECONDS = 120
# Give a run that just finished (or a rate-limited provider) room to clear before trying again -
# otherwise a persistent failure gets re-fired every sweep instead of once per cooldown window.
IDLE_COOLDOWN_SECONDS = 300
# After this many runs in a row fail without the issue ever going idle-free in between, stop
# auto-requeueing and block it instead of retrying the same broken thing forever.
MAX_IDLE_RETRIES = 3
# Where an orphaned issue can be found sitting - i.e. anything that isn't already a finished
# outcome someone has to actively reopen.
NON_TERMINAL_STATUSES = {IssueStatus.BACKLOG, IssueStatus.TODO, IssueStatus.IN_PROGRESS, IssueStatus.BLOCKED}


def start_agent_run(session, issue: Issue, prompt: str | None = None) -> Run | None:
    """Queue a run for the issue's assigned agent. Returns None if there is nobody to run it."""
    from hagent.engine import queue_issue_run

    agent = issue.assignee
    if agent is None or getattr(agent, "archived", False) or issue.status in CLOSED_STATUSES:
        return None
    return queue_issue_run(session, issue, agent, prompt)


def _has_active_run(session, issue: Issue) -> bool:
    return session.scalar(select(Run.id).where(Run.issue_id == issue.id, Run.status.in_(ACTIVE_RUN_STATUSES)).limit(1)) is not None


def _stage_summary(session, parent: Issue, stage: int, children: list[Issue]) -> str:
    from hagent.keys import issue_key

    lines = [f"Stage {stage} of your sub-issues has finished. Results:", ""]
    for child in children:
        latest = session.scalar(select(Run).where(Run.issue_id == child.id, Run.status == RunStatus.COMPLETED).order_by(Run.created_at.desc()).limit(1))
        output = (latest.output or "").strip()[:SUMMARY_OUTPUT_CHARS] if latest else ""
        lines.append(f"- {issue_key(session, child)} [{child.status.value}] {child.title}")
        if output:
            lines.append("  " + output.replace("\n", "\n  "))
    lines += ["", f"You own '{parent.title}'. Review these results and decide the next step: create or start the next stage, fix problems, or finish the issue."]
    return "\n".join(lines)


def fire_ready_stages(session, parent: Issue) -> Run | None:
    """Wake the parent's agent for the first finished stage it has not been told about yet."""
    if parent.status in CLOSED_STATUSES or parent.assignee_agent_id is None or _has_active_run(session, parent):
        return None
    children = session.scalars(select(Issue).where(Issue.parent_issue_id == parent.id, Issue.stage.is_not(None)).order_by(Issue.stage, Issue.created_at)).all()
    stages: dict[int, list[Issue]] = {}
    for child in children:
        stages.setdefault(child.stage, []).append(child)
    for stage in sorted(stages):
        group = stages[stage]
        if any(child.status not in FINISHED_STATUSES for child in group):
            continue
        # The signature changes if a new sub-issue joins the stage, so it can fire again.
        signature = f"stage {stage}: " + ",".join(sorted(child.id for child in group))
        if session.scalar(select(TimelineEvent.id).where(TimelineEvent.issue_id == parent.id, TimelineEvent.event_type == "stage_completed", TimelineEvent.detail == signature).limit(1)):
            continue
        run = start_agent_run(session, parent, prompt=_stage_summary(session, parent, stage, group))
        if run is None:
            return None
        session.add(TimelineEvent(issue_id=parent.id, event_type="stage_completed", detail=signature))
        session.commit()
        return run
    return None


def after_issue_finished(session, issue: Issue) -> None:
    """Call when an issue reaches a finished state. Never raises: orchestration must not fail a run."""
    try:
        if issue.parent_issue_id:
            parent = session.get(Issue, issue.parent_issue_id)
            if parent is not None:
                fire_ready_stages(session, parent)
        # The issue may itself be a parent whose earlier stages completed while it was busy.
        fire_ready_stages(session, issue)
    except Exception:  # noqa: BLE001
        log.exception("stage barrier check failed for issue %s", issue.id)
        session.rollback()


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _consecutive_failures(session, issue_id: str) -> int:
    """How many of the issue's most recent runs failed in a row, most recent first."""
    statuses = session.scalars(
        select(Run.status).where(Run.issue_id == issue_id).order_by(Run.created_at.desc()).limit(MAX_IDLE_RETRIES + 1)
    ).all()
    count = 0
    for status in statuses:
        if status != RunStatus.FAILED:
            break
        count += 1
    return count


def reconcile_orphaned_issues(session) -> list[str]:
    """Find issues whose actual work was cancelled or rejected but which never followed it out.

    A Run can end up CANCELLED (engine.cancel_issue, or an aging queued run someone killed) or
    REJECTED (a WAITING_APPROVAL run someone turned down) without the issue itself moving anywhere -
    it's left stranded in Backlog, Todo, In Progress or Blocked, looking like live work when the
    process behind it is actually dead. This is the research step for that: for every non-terminal
    issue with no active run, look at what its most recent run actually says happened, and if that
    was an explicit cancellation or rejection, finish the job - move the issue to Cancelled with a
    timeline note naming which run and why, instead of leaving it lost wherever it happened to sit.
    """
    resolved: list[str] = []
    candidates = session.scalars(select(Issue).where(Issue.status.in_(NON_TERMINAL_STATUSES))).all()
    for issue in candidates:
        if _has_active_run(session, issue):
            continue
        last_run = session.scalar(select(Run).where(Run.issue_id == issue.id).order_by(Run.created_at.desc()).limit(1))
        if last_run is None or last_run.status not in (RunStatus.CANCELLED, RunStatus.REJECTED):
            continue
        reason = last_run.error or f"its run was {last_run.status.value}"
        was = issue.status.value
        issue.status = IssueStatus.CANCELLED
        session.add(TimelineEvent(
            issue_id=issue.id, event_type="auto_cancelled",
            detail=f"Orphaned in {was}: last run ({last_run.id[:8]}) was {last_run.status.value} ({reason}) and nothing since picked the work back up.",
        ))
        session.commit()
        resolved.append(issue.id)
    return resolved


def requeue_idle_issues(session) -> list[str]:
    """Give a fresh run to any issue that needs active work but has none.

    An issue can end up idle - assigned to an agent, in Todo or In Progress, but with no run at all
    - if a run simply failed and nothing retried it, or if its status was changed by hand without
    going through apply_status_change (e.g. the board's status dropdown). This is the periodic
    safety net for that: it never leaves an idle issue waiting on its own agent to become free,
    since queuing a run here just adds a PENDING run that the dispatcher starts as soon as the
    agent has capacity - it does not itself decide whether the agent is busy.
    """
    queued: list[str] = []
    idle = session.scalars(select(Issue).where(Issue.status.in_(AUTO_START_STATUSES), Issue.assignee_agent_id.is_not(None))).all()
    for issue in idle:
        if _has_active_run(session, issue):
            continue
        agent = issue.assignee
        if agent is None or getattr(agent, "archived", False):
            continue
        last_run = session.scalar(select(Run).where(Run.issue_id == issue.id).order_by(Run.created_at.desc()).limit(1))
        if last_run is not None:
            settled_at = last_run.finished_at or last_run.created_at
            if settled_at and (datetime.now(timezone.utc) - _utc(settled_at)).total_seconds() < IDLE_COOLDOWN_SECONDS:
                continue
            failures = _consecutive_failures(session, issue.id)
            if failures >= MAX_IDLE_RETRIES:
                issue.status = IssueStatus.BLOCKED
                session.add(TimelineEvent(
                    issue_id=issue.id, event_type="run_abandoned",
                    detail=f"Failed {failures} times in a row while idle; blocked instead of retrying again.",
                ))
                session.commit()
                continue
        run = start_agent_run(session, issue)
        if run is None:
            continue
        session.add(TimelineEvent(
            issue_id=issue.id, event_type="run_requeued",
            detail="Assigned agent had no active run for this issue; queued automatically instead of leaving it idle.",
        ))
        session.commit()
        queued.append(run.id)
    return queued


def apply_status_change(session, issue: Issue, previous: IssueStatus, start: bool) -> Run | None:
    """Follow-up work after an issue's status was changed by a person or CLI (not by the engine)."""
    if issue.status == previous:
        return None
    if issue.status in FINISHED_STATUSES:
        after_issue_finished(session, issue)
        return None
    if start and issue.status in AUTO_START_STATUSES:
        return start_agent_run(session, issue)
    return None
