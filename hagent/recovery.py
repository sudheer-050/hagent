"""Picking work back up after the application was closed (or crashed) mid-run.

A run that is RUNNING but whose owning process no longer exists was interrupted. It is marked
failed (so history stays honest) and a fresh PENDING run for the same issue is queued, with a
note telling the agent to check the work already done and continue rather than start over. The
dispatcher then runs it as usual. An issue that keeps getting interrupted is blocked after
MAX_RESUMES attempts instead of looping forever.

Files an agent already wrote live in its worktree and issue comments/timeline persist in the
database, so a resumed run sees the earlier progress.
"""

import logging
from datetime import datetime, timezone
from functools import lru_cache

import psutil
from sqlalchemy import select, update

from hagent.db import get_session
from hagent.models import Issue, IssueStatus, Run, RunStatus, TimelineEvent
from hagent.orchestration import CLOSED_STATUSES

log = logging.getLogger(__name__)

MAX_RESUMES = 3
RESUME_MARKER = "[Resumed after interruption]"
RESUME_NOTE = (
    f"\n\n{RESUME_MARKER} Your previous attempt was interrupted when the application closed. "
    "Check the work already done (the repository or worktree, the issue's comments and timeline) "
    "and continue from where you left off. Do not start over or redo finished work."
)
RECOVERY_INTERVAL_SECONDS = 60
_PID_REUSE_TOLERANCE_SECONDS = 2.0


@lru_cache(maxsize=1)
def current_owner() -> tuple[int, float]:
    """(pid, process start time) identifying this process, safe against pid reuse."""
    process = psutil.Process()
    return process.pid, process.create_time()


def owner_is_alive(pid: int | None, started: float | None) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        if started is not None and abs(process.create_time() - started) > _PID_REUSE_TOLERANCE_SECONDS:
            return False  # the pid now belongs to an unrelated process
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _resume_depth(session, run: Run) -> int:
    depth, seen = 0, {run.id}
    while run.resumed_from and run.resumed_from not in seen:
        seen.add(run.resumed_from)
        run = session.get(Run, run.resumed_from)
        if run is None:
            break
        depth += 1
    return depth


def _strip_note(prompt: str) -> str:
    return prompt.split(RESUME_NOTE, 1)[0] if RESUME_NOTE in prompt else prompt


def recover_interrupted_runs() -> list[str]:
    """Requeue every run whose owner process is gone. Returns the ids of the new runs."""
    queued: list[str] = []
    with get_session(scoped=False) as session:
        for run in session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)).all():
            if owner_is_alive(run.owner_pid, run.owner_started):
                continue
            # Atomic claim: if several processes recover at once, only one wins.
            won = session.execute(
                update(Run).where(Run.id == run.id, Run.status == RunStatus.RUNNING).values(
                    status=RunStatus.FAILED,
                    error="Interrupted: the application closed while this run was in progress.",
                    finished_at=datetime.now(timezone.utc),
                ),
                execution_options={"synchronize_session": False},
            ).rowcount
            session.commit()
            if not won:
                continue
            issue = session.get(Issue, run.issue_id)
            if issue is None or issue.status in CLOSED_STATUSES:
                continue
            depth = _resume_depth(session, run)
            if depth >= MAX_RESUMES:
                issue.status = IssueStatus.BLOCKED
                session.add(TimelineEvent(issue_id=issue.id, event_type="run_abandoned", detail=f"Interrupted {depth + 1} times in a row; blocked instead of retrying again."))
                session.commit()
                continue
            resumed = Run(
                issue_id=run.issue_id, agent_id=run.agent_id, status=RunStatus.PENDING, resumed_from=run.id,
                prompt=_strip_note(run.prompt) + RESUME_NOTE, session_id=run.session_id,
            )
            session.add(resumed)
            session.flush()
            session.add(TimelineEvent(issue_id=issue.id, event_type="run_resumed", detail=f"Run {run.id[:8]} was interrupted; resumed as run {resumed.id[:8]} (attempt {depth + 2})"))
            session.commit()
            queued.append(resumed.id)
            log.info("resumed interrupted run %s as %s", run.id, resumed.id)
    return queued
