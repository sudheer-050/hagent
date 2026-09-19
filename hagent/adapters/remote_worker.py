"""Runs a model call on another device via the worker queue instead of on this machine."""

import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from hagent import db as hagent_db
from hagent.adapters.base import BaseRuntime, RuntimeResult
from hagent.models import WorkerJob
from hagent.worker_api import is_online

POLL_INTERVAL = 0.5
KEEP_FINISHED_DAYS = 7
# Only these settings travel to the worker. Notably absent: the executable path and the
# working directory, which the worker decides for itself.
FORWARDED_OPTIONS = ("terminal_enabled", "timeout", "resume_session_id", "reasoning_effort", "max_tokens")


class RemoteWorkerRuntime(BaseRuntime):
    def __init__(self, model: str, config: dict, runtime_type: str):
        super().__init__(model, config)
        self.runtime_type = runtime_type
        self.worker_name = str(config["worker"])

    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        # Tools and delegation are served by this machine's engine and cannot cross to a worker;
        # the installed assistants on the worker use their own tools.
        timeout = int(self.config.get("timeout", 600))
        options = {key: self.config[key] for key in FORWARDED_OPTIONS if key in self.config}
        options["timeout"] = timeout

        with hagent_db.SessionLocal() as s:
            if not is_online(s, self.worker_name):
                raise RuntimeError(f"Worker '{self.worker_name}' is offline. Start it on that device with: hagent worker start --name {self.worker_name}")
            s.execute(delete(WorkerJob).where(WorkerJob.finished_at < datetime.now(timezone.utc) - timedelta(days=KEEP_FINISHED_DAYS)))
            job = WorkerJob(
                worker_name=self.worker_name, runtime_type=self.runtime_type, model=self.model,
                options_json=json.dumps(options), prompt=prompt, context=context,
            )
            s.add(job)
            s.commit()
            job_id = job.id

        deadline = time.monotonic() + timeout + 60
        while time.monotonic() < deadline:
            with hagent_db.SessionLocal() as s:
                job = s.scalar(select(WorkerJob).where(WorkerJob.id == job_id))
                if job.status == "done":
                    return RuntimeResult(output=job.output, input_tokens=job.input_tokens, output_tokens=job.output_tokens, session_id=job.session_id)
                if job.status == "failed":
                    raise RuntimeError(f"Worker '{self.worker_name}' failed: {job.error}")
            time.sleep(POLL_INTERVAL)

        with hagent_db.SessionLocal() as s:
            job = s.get(WorkerJob, job_id)
            if job.status in {"pending", "running"}:
                job.status, job.finished_at = "abandoned", datetime.now(timezone.utc)
                s.commit()
        raise RuntimeError(f"Worker '{self.worker_name}' did not answer within {timeout + 60} seconds")
