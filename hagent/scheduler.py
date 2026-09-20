"""Autopilot scheduler: cron-triggered agent runs against matching issues.

Mirrors Multica's daemon/autopilot concept for this single-process local app:
a BackgroundScheduler holds one APScheduler cron job per AutopilotTrigger, and
each firing looks up fresh state from the DB (autopilots/issues can change
between firings) rather than capturing stale objects at schedule time.
"""

from datetime import datetime, timezone
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from hagent.db import get_session
from hagent.engine import process_queued_run_by_id, run_issue
from hagent.models import Autopilot, AutopilotRun, AutopilotTrigger, Issue, IssueStatus, Project, Run, RunStatus, TimelineEvent, TriggerType
from hagent.provider_health import ensure_provider_monitor_agents, run_provider_health_checks

_scheduler: BackgroundScheduler | None = None


def find_matching_issues(session, autopilot: Autopilot) -> list[Issue]:
    stmt = select(Issue).join(Project).where(Project.workspace_id == autopilot.workspace_id)
    if autopilot.project_id:
        stmt = stmt.where(Issue.project_id == autopilot.project_id)
    if autopilot.filter_status:
        stmt = stmt.where(Issue.status == autopilot.filter_status)
    return list(session.scalars(stmt).all())


def run_autopilot_once(autopilot_id: str) -> AutopilotRun | None:
    with get_session(scoped=False) as session:
        autopilot = session.get(Autopilot, autopilot_id)
        if not autopilot or not autopilot.enabled:
            return None

        session.info["workspace_id"] = autopilot.workspace_id
        if not autopilot.agent or autopilot.agent.workspace_id != autopilot.workspace_id:
            raise ValueError("Autopilot agent crosses workspaces")
        autopilot_run = AutopilotRun(autopilot_id=autopilot.id, status="running")
        session.add(autopilot_run)
        session.commit()

        if autopilot.mode == "create_issue":
            return _run_create_issue_autopilot(session, autopilot, autopilot_run)

        issues = find_matching_issues(session, autopilot)
        ran = succeeded = failed = cancelled = awaiting_approval = skipped = 0
        for issue in issues:
            result = run_issue(session, issue, autopilot.agent)
            ran += 1
            if result.status == RunStatus.COMPLETED:
                succeeded += 1
            elif result.status == RunStatus.FAILED:
                failed += 1
            elif result.status == RunStatus.CANCELLED:
                cancelled += 1
            elif result.status == RunStatus.WAITING_APPROVAL:
                awaiting_approval += 1
            else:
                skipped += 1

        autopilot_run.status = "completed"
        autopilot_run.finished_at = datetime.now(timezone.utc)
        if failed and succeeded:
            autopilot_run.status = 'partial'
        elif failed:
            autopilot_run.status = 'failed'
        elif cancelled and not succeeded and not skipped:
            autopilot_run.status = 'cancelled'
        elif cancelled or awaiting_approval or skipped:
            autopilot_run.status = 'partial'
        autopilot_run.summary = f'Ran {ran} matching issue(s): {succeeded} succeeded, {failed} failed, {cancelled} cancelled, {awaiting_approval} awaiting approval, {skipped} skipped'
        session.commit()
        session.refresh(autopilot_run)
        return autopilot_run


def _run_create_issue_autopilot(session, autopilot: Autopilot, autopilot_run: AutopilotRun) -> AutopilotRun:
    """Recurring job: each firing creates a fresh issue for the agent and queues its run."""
    from hagent.keys import issue_key
    from hagent.orchestration import start_agent_run
    from hagent.triggers import render_issue_title

    try:
        if not autopilot.project_id:
            raise ValueError("This autopilot creates issues but has no project; set one with `autopilot update --project`")
        issue = Issue(
            project_id=autopilot.project_id,
            title=render_issue_title(autopilot.issue_title_template, autopilot.name),
            description=autopilot.description,
            assignee_agent_id=autopilot.agent_id,
            status=IssueStatus.TODO,
        )
        session.add(issue)
        session.flush()  # assigns the id the timeline event needs
        session.add(TimelineEvent(issue_id=issue.id, event_type="created_by_autopilot", detail=autopilot.name))
        session.commit()
        run = start_agent_run(session, issue)
        autopilot_run.status = "completed"
        autopilot_run.summary = f"Created {issue_key(session, issue)} '{issue.title}'" + (f" and queued a run ({run.status.value})" if run else "")
    except Exception as exc:  # noqa: BLE001 - recorded on the run so it shows up in `autopilot runs`
        session.rollback()
        autopilot_run.status = "failed"
        autopilot_run.summary = str(exc)
    autopilot_run.finished_at = datetime.now(timezone.utc)
    session.add(autopilot_run)
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
    with get_session(scoped=False) as session:
        ensure_provider_monitor_agents(session)
        triggers = session.scalars(select(AutopilotTrigger)).all()
        for trig in triggers:
            autopilot = session.get(Autopilot, trig.autopilot_id)
            if not autopilot or not autopilot.enabled or not trig.enabled or not trig.cron_expression or trig.type.value != "cron":
                continue
            scheduler.add_job(
                run_autopilot_once,
                CronTrigger.from_crontab(trig.cron_expression, timezone=trig.timezone or None),
                args=[autopilot.id],
                id=_job_id(trig),
                replace_existing=True,
            )
            count += 1
    try:
        monitor_minutes = max(1, int(os.environ.get("HAGENT_PROVIDER_HEALTH_MINUTES", "15")))
    except ValueError:
        monitor_minutes = 15
    from hagent.dispatcher import DISPATCH_INTERVAL_SECONDS, dispatch_pending
    from hagent.recovery import RECOVERY_INTERVAL_SECONDS, recover_interrupted_runs
    from hagent.orchestration import IDLE_SWEEP_INTERVAL_SECONDS, requeue_idle_issues

    scheduler.add_job(
        recover_interrupted_runs,
        trigger="interval",
        seconds=RECOVERY_INTERVAL_SECONDS,
        id="hagent-run-recovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        dispatch_pending,
        trigger="interval",
        seconds=DISPATCH_INTERVAL_SECONDS,
        id="hagent-run-dispatcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_provider_health_checks,
        trigger="interval",
        minutes=monitor_minutes,
        id="hagent-provider-health-monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _sweep_idle_issues,
        trigger="interval",
        seconds=IDLE_SWEEP_INTERVAL_SECONDS,
        id="hagent-idle-issue-sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return count


def _sweep_idle_issues() -> list[str]:
    from hagent.orchestration import reconcile_orphaned_issues, requeue_idle_issues

    with get_session(scoped=False) as session:
        reconcile_orphaned_issues(session)  # first: retire anything whose work was actually cancelled/rejected
        return requeue_idle_issues(session)


def find_webhook_trigger(token: str) -> AutopilotTrigger | None:
    with get_session(scoped=False) as session:
        return session.scalar(select(AutopilotTrigger).where(AutopilotTrigger.webhook_token == token, AutopilotTrigger.type == TriggerType.WEBHOOK, AutopilotTrigger.enabled.is_(True)))


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler()
    sync_scheduler_jobs(_scheduler)
    _scheduler.start()
    try:
        # Pick up runs that were in progress when the application last closed.
        from hagent.recovery import recover_interrupted_runs

        recover_interrupted_runs()
        from hagent.dispatcher import start_dispatch_loop

        start_dispatch_loop()
    except Exception:  # noqa: BLE001 - a recovery problem must not stop the server from starting
        import logging

        logging.getLogger(__name__).exception("could not recover interrupted runs at startup")
    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def stop_scheduler(*, wait: bool = False) -> None:
    """Stop scheduled jobs and the dispatcher threads owned by this process."""
    global _scheduler
    scheduler = _scheduler
    _scheduler = None
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=wait)
    from hagent.dispatcher import shutdown_dispatcher

    shutdown_dispatcher(wait=wait)


def resume_pending_runs() -> int:
    """Schedule persisted runs queued but not started before shutdown.

    RUNNING runs are deliberately not replayed: an adapter may already have
    performed external side effects before the process stopped.
    """
    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        return 0

    with get_session(scoped=False) as session:
        run_ids = session.scalars(
            select(Run.id).where(Run.status == RunStatus.PENDING).order_by(Run.created_at)
        ).all()

    for run_id in run_ids:
        scheduler.add_job(
            process_queued_run_by_id,
            trigger="date",
            run_date=datetime.now(timezone.utc),
            args=[run_id],
            id=f"hagent-pending-run-{run_id}",
            replace_existing=True,
        )
    return len(run_ids)
