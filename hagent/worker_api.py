"""Server side of worker devices: a queue that `hagent worker` processes poll for model calls.

A worker authenticates with a worker token whose *name* is the only worker identity it may act as,
so one compromised device cannot claim or answer another device's jobs.
"""

import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from hagent import db as hagent_db
from hagent.models import Worker, WorkerJob

router = APIRouter()

MAX_WAIT_SECONDS = 25
POLL_INTERVAL = 0.5
ONLINE_WINDOW_SECONDS = 90
MAX_RESULT_CHARS = 2_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session():
    return hagent_db.SessionLocal()


def _worker_name(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or user.kind != "worker":
        raise HTTPException(403, "Worker endpoints need a worker token")
    return user.token_name


def is_online(session, name: str) -> bool:
    worker = session.scalar(select(Worker).where(Worker.name == name))
    if not worker or not worker.last_seen_at:
        return False
    seen = worker.last_seen_at if worker.last_seen_at.tzinfo else worker.last_seen_at.replace(tzinfo=timezone.utc)
    return _now() - seen < timedelta(seconds=ONLINE_WINDOW_SECONDS)


@router.get("/api/worker/ping")
def ping():
    return {"ok": True}


class ClaimRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    types: list[str] = Field(default_factory=list, max_length=32)
    wait: float = Field(default=MAX_WAIT_SECONDS, ge=0, le=MAX_WAIT_SECONDS)


def _touch_worker(session, name: str, types: list[str]) -> None:
    worker = session.scalar(select(Worker).where(Worker.name == name))
    if not worker:
        worker = Worker(name=name)
        session.add(worker)
    worker.last_seen_at = _now()
    worker.capabilities = json.dumps(sorted(set(types)))
    session.commit()


def _claim_one(session, name: str, types: list[str]) -> WorkerJob | None:
    candidates = session.scalars(
        select(WorkerJob).where(WorkerJob.worker_name == name, WorkerJob.status == "pending", WorkerJob.runtime_type.in_(types)).order_by(WorkerJob.created_at).limit(5)
    ).all()
    for job in candidates:
        # The status condition makes the claim atomic even if two polls race.
        claimed = session.execute(
            update(WorkerJob).where(WorkerJob.id == job.id, WorkerJob.status == "pending").values(status="running", claimed_at=_now())
        ).rowcount
        session.commit()
        if claimed:
            return job
    return None


@router.post("/api/worker/claim")
def claim(payload: ClaimRequest, request: Request):
    name = _worker_name(request)
    if payload.name != name:
        raise HTTPException(403, "This token may only act as worker '%s'" % name)
    deadline = time.monotonic() + payload.wait
    while True:
        with _session() as s:
            _touch_worker(s, name, payload.types)
            job = _claim_one(s, name, payload.types)
            if job:
                return {"job": {
                    "id": job.id, "runtime_type": job.runtime_type, "model": job.model,
                    "prompt": job.prompt, "context": job.context, "options": json.loads(job.options_json or "{}"),
                }}
        if time.monotonic() >= deadline:
            return {"job": None}
        time.sleep(POLL_INTERVAL)


class CompleteRequest(BaseModel):
    output: str = Field(default="", max_length=MAX_RESULT_CHARS)
    error: str = Field(default="", max_length=20_000)
    input_tokens: int | None = None
    output_tokens: int | None = None
    session_id: str | None = Field(default=None, max_length=200)


@router.post("/api/worker/jobs/{job_id}/complete")
def complete(job_id: str, payload: CompleteRequest, request: Request):
    name = _worker_name(request)
    with _session() as s:
        job = s.get(WorkerJob, job_id)
        if not job or job.worker_name != name:
            raise HTTPException(404, "Job not found")
        if job.status != "running":
            raise HTTPException(409, f"Job is {job.status}")
        job.status = "failed" if payload.error else "done"
        job.output, job.error = payload.output, payload.error
        job.input_tokens, job.output_tokens, job.session_id = payload.input_tokens, payload.output_tokens, payload.session_id
        job.finished_at = _now()
        s.commit()
    return {"ok": True}
