"""Task execution engine: Task -> Agent -> Runtime -> result, persisted back to the DB."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orkestra.adapters import get_runtime_class
from orkestra.models import Agent, Runtime, Task, TaskStatus


def run_task(session: Session, task: Task) -> Task:
    agent: Agent = task.agent
    runtime: Runtime = agent.runtime

    task.status = TaskStatus.RUNNING
    task.started_at = datetime.now(timezone.utc)
    session.commit()

    runtime_cls = get_runtime_class(runtime.type)
    config = json.loads(runtime.config_json or "{}")
    adapter = runtime_cls(model=runtime.model, config=config)

    try:
        result = adapter.run(prompt=task.prompt, context=agent.instructions)
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.finished_at = datetime.now(timezone.utc)
        session.commit()
        return task

    task.status = TaskStatus.COMPLETED
    task.output = result.output
    task.finished_at = datetime.now(timezone.utc)
    session.commit()
    return task
