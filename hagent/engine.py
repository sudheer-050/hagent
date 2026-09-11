"""Task execution engine: Issue -> Agent -> Runtime -> Run result, persisted back to the DB."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from hagent.adapters import get_runtime_class
from hagent.models import Agent, Issue, IssueStatus, Run, RunStatus


def run_issue(session: Session, issue: Issue, agent: Agent, prompt: str | None = None) -> Run:
    """Create and execute a Run for an issue against its assigned agent's runtime.

    On success the issue moves to IN_REVIEW; on failure it's left wherever it was
    (still IN_PROGRESS) so a human/autopilot can see it needs attention.
    """
    runtime = agent.runtime
    run = Run(issue_id=issue.id, agent_id=agent.id, prompt=prompt or issue.description or issue.title)
    session.add(run)
    session.commit()
    session.refresh(run)

    issue.status = IssueStatus.IN_PROGRESS
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    session.commit()

    runtime_cls = get_runtime_class(runtime.type)
    config = json.loads(runtime.config_json or "{}")
    adapter = runtime_cls(model=runtime.model, config=config)

    try:
        result = adapter.run(prompt=run.prompt, context=agent.instructions)
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return run

    run.status = RunStatus.COMPLETED
    run.output = result.output
    run.finished_at = datetime.now(timezone.utc)
    issue.status = IssueStatus.IN_REVIEW
    session.commit()
    return run
