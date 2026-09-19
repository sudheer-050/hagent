"""Shared trigger validation for CLI and HTTP mutations."""
import re
import secrets
from datetime import datetime, timezone

import click
from apscheduler.util import astimezone
from apscheduler.triggers.cron import CronTrigger
from hagent.models import TriggerType


_TEMPLATE_TOKEN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def validate_timezone(name):
    """Return the IANA timezone name if APScheduler can resolve it, else raise a CLI error."""
    try:
        astimezone(name)
    except Exception as exc:  # noqa: BLE001 - unknown zones surface as several error types
        raise click.ClickException(f"Unknown timezone '{name}'. Use an IANA name such as Europe/London or Asia/Kolkata.") from exc
    return name


def validate_title_template(template):
    """Only {{date}} may be interpolated; anything else is rejected up front, not at run time."""
    for token in _TEMPLATE_TOKEN.findall(template or ""):
        if token != "date":
            raise click.ClickException(f"Unsupported template token '{{{{{token}}}}}'. Only {{{{date}}}} is available.")
    return template


def render_issue_title(template, fallback):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _TEMPLATE_TOKEN.sub(today, template).strip() if template else fallback


def configure(trigger, *, cron=None, webhook=False, enabled=None, timezone_name=None, label=None):
    if cron is not None and webhook:
        raise click.ClickException("Choose cron or webhook")
    if timezone_name is not None:
        trigger.timezone = validate_timezone(timezone_name) if timezone_name else None
    if label is not None:
        trigger.label = label
    if cron is not None:
        try:
            CronTrigger.from_crontab(cron, timezone=trigger.timezone or None)
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
