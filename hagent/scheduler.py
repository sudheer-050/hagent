"""Autopilot scheduler: cron-triggered agent runs against matching issues.

Mirrors Multica's daemon/autopilot concept for this single-process local app:
a BackgroundScheduler holds one APScheduler cron job per AutopilotTrigger, and
each firing looks up fresh state from the DB (autopilots/issues can change
between firings) rather than capturing stale objects at schedule time.
"""

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from hagent.db import get_session
from hagent.engine import run_issue
from hagent.models import Autopilot, AutopilotRun, AutopilotTrigger, Issue

_scheduler: BackgroundScheduler | None = None


def find_matching_issues(session, autopilot: Autopilot) -> list[Issue]:
    stmt = select(Issue)
    if autopilot.project_id:
        stmt = stmt.where(Issue.project_id == autopilot.project_id)
    if autopilot.filter_status:
        stmt = stmt.where(Issue.status == autopilot.filter_status)
    return list(session.scalars(stmt).all())


def run_autopilot_once(autopilot_id: str) -> AutopilotRun | None:
    with get_session() as session:
        autopilot = session.get(Autopilot, autopilot_id)
        if not autopilot or not autopilot.enabled:
            return None

        autopilot_run = AutopilotRun(autopilot_id=autopilot.id, status="running")
        session.add(autopilot_run)
        session.commit()

        issues = find_matching_issues(session, autopilot)
        ran = 0
        for issue in issues:
            run_issue(session, issue, autopilot.agent)
            ran += 1

        autopilot_run.status = "completed"
        autopilot_run.summary = f"Ran {ran} matching issue(s)"
        autopilot_run.finished_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(autopilot_run)
        return autopilot_run


def _job_id(trigger: AutopilotTrigger) -> str:
    return f"autopilot-trigger-{trigger.id}"


def sync_scheduler_jobs(scheduler: BackgroundScheduler) -> int:
    """(Re)register a cron job for every enabled autopilot's triggers. Returns job count."""
    for job in scheduler.get_jobs():
        scheduler.remove_job(job.id)

    count = 0
    with get_session() as session:
        triggers = session.scalars(select(AutopilotTrigger)).all()
        for trig in triggers:
            autopilot = session.get(Autopilot, trig.autopilot_id)
            if not autopilot or not autopilot.enabled:
                continue
            scheduler.add_job(
                run_autopilot_once,
                CronTrigger.from_crontab(trig.cron_expression),
                args=[autopilot.id],
                id=_job_id(trig),
                replace_existing=True,
            )
            count += 1
    return count


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    sync_scheduler_jobs(_scheduler)
    _scheduler.start()
    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
