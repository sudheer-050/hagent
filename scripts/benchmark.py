"""Repeatable orchestration-overhead benchmark for Hagent.

Runs each scenario one at a time (never concurrently) against a throwaway
in-memory database with a stub runtime, so the numbers isolate Hagent's own
overhead from model latency.

    python scripts/benchmark.py [--runs 200] [--json out.json]
"""

import argparse
import json
import statistics
import time
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hagent.adapters.base import RuntimeResult
from hagent.engine import queue_issue_run, run_issue
from hagent.models import Agent, Base, Issue, IssueStatus, Project, Runtime, RuntimeType, Workspace


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def _summarize(name: str, samples_ms: list[float], wall_s: float) -> dict:
    return {
        "scenario": name,
        "n": len(samples_ms),
        "p50_ms": round(statistics.median(samples_ms), 2),
        "p95_ms": round(_pct(samples_ms, 0.95), 2),
        "max_ms": round(max(samples_ms), 2),
        "per_sec": round(len(samples_ms) / wall_s, 1),
    }


def _timed(fn, n: int) -> tuple[list[float], float]:
    samples = []
    start = time.perf_counter()
    for i in range(n):
        t = time.perf_counter()
        fn(i)
        samples.append((time.perf_counter() - t) * 1000)
    return samples, time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--json", help="write results to this file")
    args = parser.parse_args()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()  # matches hagent.db.SessionLocal

    ws = Workspace(name="bench")
    session.add(ws)
    session.commit()
    runtime = Runtime(workspace_id=ws.id, name="stub", type=RuntimeType.OLLAMA, model="stub", config_json="{}")
    session.add(runtime)
    session.commit()
    agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="agent", instructions="be terse")
    project = Project(workspace_id=ws.id, name="proj")
    session.add_all([agent, project])
    session.commit()

    results = []

    # 1. Issue creation
    issues: list[Issue] = []

    def create_issue(i):
        issue = Issue(project_id=project.id, title=f"issue {i}", description="ping", status=IssueStatus.BACKLOG)
        session.add(issue)
        session.commit()
        issues.append(issue)

    samples, wall = _timed(create_issue, args.runs)
    results.append(_summarize("issue create", samples, wall))

    # 2. Queue a run (persist a pending run)
    samples, wall = _timed(lambda i: queue_issue_run(session, issues[i], agent), args.runs)
    results.append(_summarize("run queue", samples, wall))

    # 3. End-to-end dispatch through the engine with a zero-latency stub runtime
    with mock.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="pong")):
        fresh = []
        for i in range(args.runs):
            issue = Issue(project_id=project.id, title=f"dispatch {i}", description="ping", status=IssueStatus.BACKLOG)
            session.add(issue)
            fresh.append(issue)
        session.commit()
        samples, wall = _timed(lambda i: run_issue(session, fresh[i], agent), args.runs)
    results.append(_summarize("run dispatch (stub runtime, 0 ms model)", samples, wall))

    print(f"{'scenario':<42}{'n':>6}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}{'ops/s':>10}")
    for r in results:
        print(f"{r['scenario']:<42}{r['n']:>6}{r['p50_ms']:>10}{r['p95_ms']:>10}{r['max_ms']:>10}{r['per_sec']:>10}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
