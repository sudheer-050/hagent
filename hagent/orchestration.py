"""Project-level orchestration: starting agents when work is handed to them, and waking a parent
issue's agent when a stage of its sub-issues has finished.

Stages work like this: sub-issues that share a parent and a `stage` number form a barrier group.
Once every issue in a group is finished (in review, done or cancelled) the parent's assignee gets a
new run whose prompt summarises the group, and decides what happens next (usually: review the
results and create the next stage). Sub-issues without a stage never wake the parent.
"""

import logging

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
