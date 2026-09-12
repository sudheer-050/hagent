"""Shared trigger validation for CLI and HTTP mutations."""
import secrets
import click
from apscheduler.triggers.cron import CronTrigger
from hagent.models import TriggerType


def configure(trigger, *, cron=None, webhook=False, enabled=None):
    if cron is not None and webhook:
        raise click.ClickException("Choose cron or webhook")
    if cron is not None:
        try:
            CronTrigger.from_crontab(cron)
        except ValueError as exc:
            raise click.ClickException(f"Invalid cron: {exc}") from exc
        trigger.type = TriggerType.CRON
        trigger.cron_expression = cron
        trigger.webhook_token = None
    if webhook:
        trigger.type = TriggerType.WEBHOOK
        trigger.cron_expression = None
        trigger.webhook_token = secrets.token_urlsafe(32)
    if enabled is not None:
        trigger.enabled = enabled
    return trigger
