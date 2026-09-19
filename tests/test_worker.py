import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import uvicorn
from click.testing import CliRunner
from fastapi.testclient import TestClient

from hagent import auth, remote, web, worker
from hagent import db as hagent_db
from hagent.adapters.base import RuntimeResult
from hagent.cli import cli
from hagent.engine import run_issue
from hagent.models import Agent, Issue, Project, Runtime, RuntimeType, RunStatus, User, Worker, WorkerJob, Workspace

PASSWORD = "correct horse battery"


@pytest.fixture()
def accounts(cli_db):
    with hagent_db.SessionLocal() as s:
        user = auth.create_user(s, "sudheer", PASSWORD)
        secrets = {}
        for name in ("laptop", "phone"):
            secrets[name], _ = auth.issue_token(s, s.get(User, user.id), kind="worker", name=name)
        secrets["api"], _ = auth.issue_token(s, s.get(User, user.id), kind="api", name="cli")
    return secrets


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def add_job(worker_name="laptop", runtime_type="claude_code", status="pending", prompt="hello"):
    with hagent_db.SessionLocal() as s:
        job = WorkerJob(worker_name=worker_name, runtime_type=runtime_type, model="default", prompt=prompt, status=status, options_json="{}")
        s.add(job)
        s.commit()
        return job.id


def claim(client, secret, name="laptop", types=("claude_code",)):
    return client.post("/api/worker/claim", json={"name": name, "types": list(types), "wait": 0}, headers=bearer(secret))


# --- server API -------------------------------------------------------------

def test_claim_hands_out_a_matching_job_once_and_records_the_heartbeat(accounts):
    client = TestClient(web.app)
    job_id = add_job()

    first = claim(client, accounts["laptop"]).json()["job"]
    second = claim(client, accounts["laptop"]).json()["job"]

    assert first["id"] == job_id and first["prompt"] == "hello" and second is None
    with hagent_db.SessionLocal() as s:
        assert s.query(WorkerJob).one().status == "running"
        assert s.query(Worker).one().last_seen_at is not None


def test_claim_only_returns_jobs_for_this_worker_and_offered_types(accounts):
    client = TestClient(web.app)
    add_job(worker_name="phone")
    add_job(runtime_type="codex_cli")

    assert claim(client, accounts["laptop"], types=("claude_code",)).json()["job"] is None


def test_a_worker_token_can_only_act_as_its_own_name(accounts):
    client = TestClient(web.app)

    assert claim(client, accounts["laptop"], name="phone").status_code == 403


def test_api_tokens_and_anonymous_callers_cannot_use_worker_endpoints(accounts):
    client = TestClient(web.app)

    assert claim(client, accounts["api"]).status_code == 403
    assert client.post("/api/worker/claim", json={"name": "laptop", "wait": 0}).status_code == 401


def test_complete_records_the_result_and_is_single_use(accounts):
    client = TestClient(web.app)
    job_id = add_job()
    claim(client, accounts["laptop"])
    body = {"output": "done!", "input_tokens": 5, "output_tokens": 2, "session_id": "s1"}

    ok = client.post(f"/api/worker/jobs/{job_id}/complete", json=body, headers=bearer(accounts["laptop"]))
    again = client.post(f"/api/worker/jobs/{job_id}/complete", json=body, headers=bearer(accounts["laptop"]))

    assert ok.status_code == 200 and again.status_code == 409
    with hagent_db.SessionLocal() as s:
        job = s.query(WorkerJob).one()
        assert (job.status, job.output, job.session_id, job.input_tokens) == ("done", "done!", "s1", 5)


def test_a_worker_cannot_answer_another_workers_job(accounts):
    client = TestClient(web.app)
    job_id = add_job(worker_name="laptop")
    claim(client, accounts["laptop"])

    stolen = client.post(f"/api/worker/jobs/{job_id}/complete", json={"output": "x"}, headers=bearer(accounts["phone"]))

    assert stolen.status_code == 404


def test_an_error_result_marks_the_job_failed(accounts):
    client = TestClient(web.app)
    job_id = add_job()
    claim(client, accounts["laptop"])

    client.post(f"/api/worker/jobs/{job_id}/complete", json={"error": "boom"}, headers=bearer(accounts["laptop"]))

    with hagent_db.SessionLocal() as s:
        assert (s.query(WorkerJob).one().status, s.query(WorkerJob).one().error) == ("failed", "boom")


def test_worker_tokens_must_be_named(cli_db):
    with hagent_db.SessionLocal() as s:
        user = auth.create_user(s, "sudheer", PASSWORD)
        with pytest.raises(auth.AuthError, match="needs --name"):
            auth.issue_token(s, s.get(User, user.id), kind="worker")


# --- what a worker will and won't run --------------------------------------

@pytest.fixture()
def adapter(mocker):
    instance = mocker.Mock()
    instance.run.return_value = RuntimeResult(output="answer", input_tokens=3, output_tokens=1, session_id="sid")
    factory = mocker.Mock(return_value=instance)
    mocker.patch("hagent.adapters.get_runtime_class", return_value=factory)
    return factory, instance


def job(**overrides):
    return {"id": "j1", "runtime_type": "claude_code", "model": "sonnet", "prompt": "p", "context": "c", "options": {}, **overrides}


def test_execute_job_runs_the_local_adapter_and_returns_its_result(adapter):
    factory, instance = adapter

    result = worker.execute_job(job(), None, False, ["claude_code"])

    assert result == {"output": "answer", "input_tokens": 3, "output_tokens": 1, "session_id": "sid"}
    instance.run.assert_called_once_with("p", "c")
    assert factory.call_args.kwargs["model"] == "sonnet"


@pytest.mark.parametrize("runtime_type", ["generic_cli", "claude", "ollama", "openai", "nonsense"])
def test_execute_job_refuses_types_outside_the_allowlist(adapter, runtime_type):
    factory, _ = adapter

    result = worker.execute_job(job(runtime_type=runtime_type), None, False, [runtime_type, "claude_code"])

    assert "does not run" in result["error"]
    factory.assert_not_called()


def test_execute_job_refuses_types_this_worker_did_not_offer(adapter):
    assert "does not run" in worker.execute_job(job(runtime_type="codex_cli"), None, False, ["claude_code"])["error"]


def test_server_supplied_command_and_directory_are_never_used(adapter):
    factory, _ = adapter
    hostile = {"command": "C:\\evil.exe", "working_directory": "C:\\Windows", "timeout": 30}

    worker.execute_job(job(options=hostile), "D:\\work", False, ["claude_code"])

    config = factory.call_args.kwargs["config"]
    assert "command" not in config and config["working_directory"] == "D:\\work" and config["timeout"] == 30


def test_terminal_access_needs_both_the_server_request_and_the_local_opt_in(adapter):
    factory, _ = adapter
    wants_terminal = job(options={"terminal_enabled": True})

    worker.execute_job(wants_terminal, None, False, ["claude_code"])
    assert "terminal_enabled" not in factory.call_args.kwargs["config"]

    worker.execute_job(job(options={}), None, True, ["claude_code"])
    assert "terminal_enabled" not in factory.call_args.kwargs["config"]

    worker.execute_job(wants_terminal, None, True, ["claude_code"])
    assert factory.call_args.kwargs["config"]["terminal_enabled"] is True


def test_timeouts_are_capped_and_adapter_errors_are_reported(adapter):
    factory, instance = adapter
    instance.run.side_effect = RuntimeError("cli exploded")

    result = worker.execute_job(job(options={"timeout": 10**9}), None, False, ["claude_code"])

    assert result == {"error": "cli exploded"}
    assert factory.call_args.kwargs["config"]["timeout"] == worker.MAX_TIMEOUT


# --- end to end: server + worker + engine ----------------------------------

@pytest.fixture()
def live(accounts, monkeypatch, tmp_path):
    instance = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{instance.servers[0].sockets[0].getsockname()[1]}"
    monkeypatch.setattr("hagent.adapters.remote_worker.POLL_INTERVAL", 0.05)
    yield url
    instance.should_exit = True
    thread.join(timeout=5)


def start_worker(url, token, name="laptop"):
    stop = threading.Event()
    thread = threading.Thread(
        target=worker.run_worker, args=({"url": url, "token": token}, name, None, False, ["claude_code"]), kwargs={"stop": stop, "log": lambda *_: None, "wait": 1}, daemon=True,
    )
    thread.start()
    return stop, thread


def make_run(session, worker_name="laptop"):
    ws = Workspace(name="ws")
    session.add(ws)
    session.commit()
    runtime = Runtime(workspace_id=ws.id, name="remote", type=RuntimeType.CLAUDE_CODE, model="default", config_json=json.dumps({"worker": worker_name, "timeout": 5}))
    session.add(runtime)
    session.commit()
    agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="agent", instructions="be terse")
    project = Project(workspace_id=ws.id, name="proj")
    session.add_all([agent, project])
    session.commit()
    issue = Issue(project_id=project.id, title="hi", description="say hi")
    session.add(issue)
    session.commit()
    return issue, agent


def test_an_agent_run_executes_on_the_worker_and_comes_back(live, accounts, session, mocker):
    ran_on_worker = mocker.patch("hagent.adapters.claude_code.ClaudeCodeRuntime.run", return_value=RuntimeResult(output="hello from the laptop", input_tokens=7, output_tokens=3))
    stop, thread = start_worker(live, accounts["laptop"])
    try:
        for _ in range(100):  # wait for the first heartbeat so the worker counts as online
            with hagent_db.SessionLocal() as s:
                if s.query(Worker).count():
                    break
            time.sleep(0.05)
        issue, agent = make_run(session)

        run = run_issue(session, issue, agent)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert run.status == RunStatus.COMPLETED and run.output == "hello from the laptop"
    assert ran_on_worker.call_count == 1
    with hagent_db.SessionLocal() as s:
        assert s.query(WorkerJob).one().status == "done"


def test_a_run_fails_clearly_when_its_worker_is_offline(cli_db, session):
    issue, agent = make_run(session, worker_name="ghost")

    run = run_issue(session, issue, agent)

    assert run.status == RunStatus.FAILED and "Worker 'ghost' is offline" in run.error


def test_a_worker_that_vanishes_mid_job_times_out_and_the_job_is_abandoned(cli_db, session, monkeypatch):
    monkeypatch.setattr("hagent.adapters.remote_worker.POLL_INTERVAL", 0.01)
    with hagent_db.SessionLocal() as s:
        s.add(Worker(name="laptop", last_seen_at=datetime.now(timezone.utc)))
        s.commit()
    from hagent.adapters.remote_worker import RemoteWorkerRuntime

    runtime = RemoteWorkerRuntime("default", {"worker": "laptop", "timeout": -59}, "claude_code")  # deadline = 1s

    with pytest.raises(RuntimeError, match="did not answer"):
        runtime.run("hi")
    with hagent_db.SessionLocal() as s:
        assert s.query(WorkerJob).one().status == "abandoned"


def test_stale_heartbeat_means_offline(cli_db):
    from hagent.worker_api import is_online

    with hagent_db.SessionLocal() as s:
        s.add(Worker(name="old", last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
        s.add(Worker(name="fresh", last_seen_at=datetime.now(timezone.utc)))
        s.commit()
        assert not is_online(s, "old") and is_online(s, "fresh") and not is_online(s, "missing")


# --- CLI --------------------------------------------------------------------

def test_worker_start_rejects_bad_input_before_connecting(cli_db):
    run = lambda *args: CliRunner().invoke(cli, ["worker", "start", *args])  # noqa: E731

    assert "--url" in run("--name", "x", "--token", "t").output
    plain = run("--name", "x", "--token", "t", "--url", "http://203.0.113.7:8000")
    assert plain.exit_code != 0 and "plain http" in plain.output
    unsupported = run("--name", "x", "--token", "t", "--url", "https://h", "--type", "generic_cli")
    assert unsupported.exit_code != 0 and "Unsupported worker type" in unsupported.output


def test_worker_list_shows_online_state(cli_db):
    with hagent_db.SessionLocal() as s:
        s.add(Worker(name="laptop", last_seen_at=datetime.now(timezone.utc), capabilities='["claude_code"]'))
        s.commit()

    output = CliRunner().invoke(cli, ["worker", "list"]).output

    assert "laptop" in output and "online" in output and "claude_code" in output
