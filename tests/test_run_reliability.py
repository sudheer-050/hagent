"""Regression tests for the three causes of Hagent runs failing slowly (2026-10-09):
an inherited dead proxy URL, retrying known-broken providers on every run, and a 3-minute
wall-clock kill on agentic CLI runs."""

import socket
from datetime import datetime, timedelta, timezone

from hagent.adapters.base import RuntimeResult
from hagent.engine import _build_adapter_config, execute_agent
from hagent.models import Agent, Runtime, RuntimeType, Workspace
from hagent.provider_health import is_cooling_down, is_infrastructure_error, retry_after
from hagent.runtime_env import scrub_inherited_environment


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- inherited environment ---------------------------------------------------------------

def test_scrub_removes_claude_session_markers_and_dead_loopback_base_urls():
    dead = f"http://127.0.0.1:{_free_port()}"
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_EFFORT": "medium",
           "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "keep",
           "ANTHROPIC_BASE_URL": dead, "OPENAI_BASE_URL": dead + "/v1", "PATH": "keep"}

    removed = scrub_inherited_environment(env)

    assert env == {"PATH": "keep", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "keep"}
    assert set(removed) == {"CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_CODE_MESSAGING_TOKEN",
                            "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"}


def test_scrub_keeps_reachable_or_remote_base_urls():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        live = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env = {"ANTHROPIC_BASE_URL": live, "OPENAI_BASE_URL": "https://gateway.example.com/v1"}
        scrub_inherited_environment(env)
    assert env == {"ANTHROPIC_BASE_URL": live, "OPENAI_BASE_URL": "https://gateway.example.com/v1"}


def test_scrub_can_be_disabled():
    env = {"HAGENT_KEEP_INHERITED_ENV": "1", "CLAUDECODE": "1"}
    assert scrub_inherited_environment(env) == []
    assert "CLAUDECODE" in env


# --- provider cooldown (circuit breaker) --------------------------------------------------

def _two_runtime_agent(session):
    ws = Workspace(name="ws")
    session.add(ws)
    session.commit()
    primary = Runtime(workspace_id=ws.id, name="primary", type=RuntimeType.OLLAMA, model="primary-model", config_json="{}")
    backup = Runtime(workspace_id=ws.id, name="backup", type=RuntimeType.OLLAMA, model="backup-model", config_json="{}")
    session.add_all([primary, backup])
    session.commit()
    agent = Agent(workspace_id=ws.id, runtime_id=primary.id, backup_runtime_id=backup.id, name="agent", instructions="x")
    session.add(agent)
    session.commit()
    return agent, primary, backup


def _record_models(mocker, *results):
    calls = []

    def fake_run(self, *args, **kwargs):
        calls.append(self.model)
        outcome = results[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", fake_run)
    return calls


def test_retry_after_honors_the_reset_time_in_a_usage_limit_message():
    checked = datetime(2026, 10, 9, 17, 0).astimezone().astimezone(timezone.utc)
    runtime = Runtime(name="codex", health_status="limited", last_health_check_at=checked,
                      health_detail="You've hit your usage limit ... try again at 8:35 PM.")
    reset = retry_after(runtime).astimezone()
    assert (reset.hour, reset.minute) == (20, 35)
    assert is_cooling_down(runtime, now=checked + timedelta(hours=1))
    assert not is_cooling_down(runtime, now=checked + timedelta(hours=4))


def test_healthy_runtime_is_never_cooling_down():
    assert not is_cooling_down(Runtime(name="ok", health_status="healthy",
                                       last_health_check_at=datetime.now(timezone.utc)))


def test_cooling_down_primary_is_skipped_for_backup_without_a_wasted_attempt(session, mocker):
    agent, primary, backup = _two_runtime_agent(session)
    primary.health_status = "limited"
    primary.health_detail = "usage limit reached"
    primary.last_health_check_at = datetime.now(timezone.utc)
    session.commit()
    calls = _record_models(mocker, RuntimeResult(output="done on backup"))

    result = execute_agent(agent, "do it")

    assert result.output == "done on backup"
    assert calls == ["backup-model"]


def test_unreachable_provider_is_marked_and_skipped_on_the_next_run(session, mocker):
    agent, primary, backup = _two_runtime_agent(session)
    calls = _record_models(mocker,
                           RuntimeError("API Error: Connection refused (ECONNREFUSED)"),
                           RuntimeResult(output="first via backup"),
                           RuntimeResult(output="second via backup"))

    execute_agent(agent, "first")
    assert primary.health_status == "error"
    execute_agent(agent, "second")

    assert calls == ["primary-model", "backup-model", "backup-model"]


def test_successful_run_clears_an_error_mark(session, mocker):
    agent, primary, _ = _two_runtime_agent(session)
    primary.health_status = "error"
    primary.last_health_check_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.commit()
    _record_models(mocker, RuntimeResult(output="ok"))

    execute_agent(agent, "hello")

    assert primary.health_status == "healthy"


def test_timeouts_are_not_treated_as_provider_outages():
    assert not is_infrastructure_error("Claude Code failed to start: Command '...' timed out after 180 seconds")
    assert is_infrastructure_error("Claude Code failed to start: [WinError 2] not installed")


# --- timeouts ------------------------------------------------------------------------------

def test_router_timeout_never_cuts_an_agentic_cli_run_to_three_minutes():
    agent = Agent(name="a")
    runtime = Runtime(name="claude", type=RuntimeType.CLAUDE_CODE, model="default", config_json="{}")
    config = _build_adapter_config(agent, runtime, None, None, {"timeout": 180})
    assert config["timeout"] >= 1800


def test_router_timeout_never_lowers_a_runtimes_own_timeout():
    agent = Agent(name="a")
    runtime = Runtime(name="ollama", type=RuntimeType.OLLAMA, model="m", config_json='{"timeout": 300}')
    assert _build_adapter_config(agent, runtime, None, None, {"timeout": 180})["timeout"] == 300
    assert _build_adapter_config(agent, runtime, None, None, {"timeout": 900})["timeout"] == 900


# --- scratch directories -----------------------------------------------------------------

def test_non_terminal_run_removes_its_scratch_directory(session, mocker):
    agent, _, _ = _two_runtime_agent(session)
    seen = []

    def fake_run(self, *args, **kwargs):
        seen.append(self.config["working_directory"])
        return RuntimeResult(output="ok")

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", fake_run)
    execute_agent(agent, "hello")

    assert seen and seen[0] and "hagent-sandbox-" in seen[0]
    import os
    assert not os.path.exists(seen[0])


# --- double failover (both providers limited on the same day, 2026-10-11) -------------------

def test_agents_return_to_their_own_home_runtime_after_both_providers_were_limited(session):
    from hagent.provider_health import promote_backups_for_limited_runtime, restore_recovered_runtime
    ws = Workspace(name="ws")
    session.add(ws); session.commit()
    claude = Runtime(workspace_id=ws.id, name="Claude", type=RuntimeType.CLAUDE_CODE, model="default", config_json="{}")
    codex = Runtime(workspace_id=ws.id, name="Codex", type=RuntimeType.CODEX_CLI, model="default", config_json="{}")
    session.add_all([claude, codex]); session.commit()
    claude_home = Agent(workspace_id=ws.id, name="Jack", runtime_id=claude.id, backup_runtime_id=codex.id)
    codex_home = Agent(workspace_id=ws.id, name="Ruth", runtime_id=codex.id, backup_runtime_id=claude.id)
    session.add_all([claude_home, codex_home]); session.commit()

    promote_backups_for_limited_runtime(session, codex, "usage limit reached, try again at 12:23 PM")
    assert codex_home.runtime_id == claude.id                       # Ruth fails over to Claude
    promote_backups_for_limited_runtime(session, claude, "session limit reached")
    assert claude_home.runtime_id == claude.id                      # Jack stays: his backup is limited too
    assert codex_home.runtime_id == claude.id and codex_home.failback_runtime_id == codex.id

    codex.health_status = claude.health_status = "healthy"
    restore_recovered_runtime(session, codex)                       # Codex recovers first
    restore_recovered_runtime(session, claude)

    assert claude_home.runtime_id == claude.id                      # each back on its own home runtime
    assert codex_home.runtime_id == codex.id
    assert codex_home.failback_runtime_id is None


# --- waiting out usage limits instead of failing into them (2026-10-12) ---------------------
# Real failures: 42 of 42 runs after the cooldown fix failed on usage limits, retried blindly every
# ~6 minutes, and "Smoke test: Jack" was blocked after three limit failures that weren't its fault.

def _limited(runtime, detail, minutes_ago=1):
    runtime.health_status = "limited"
    runtime.health_detail = detail
    runtime.last_health_check_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_claude_reset_message_with_a_timezone_is_understood():
    from hagent.provider_health import parse_reset_time
    after = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)  # 4:00 PM in New York
    reset = parse_reset_time("You've hit your session limit · resets 5:20pm (America/New_York)", after)
    assert reset == datetime(2026, 10, 11, 21, 20, tzinfo=timezone.utc)


def test_reset_time_already_passed_today_means_tomorrow():
    from hagent.provider_health import parse_reset_time
    after = datetime(2026, 10, 11, 23, 0).astimezone()
    reset = parse_reset_time("try again at 5:23 PM.", after).astimezone()
    assert (reset.day, reset.hour, reset.minute) == (12, 17, 23)


def test_reset_time_deep_inside_a_long_cli_error_survives_truncation():
    from hagent.provider_health import limit_detail, parse_reset_time
    error = '{"type":"thread.started"}\n' + "x" * 3000 + "You've hit your usage limit ... or try again at 5:23 PM."
    detail = limit_detail(error)
    assert len(detail) <= 1000
    assert parse_reset_time(detail, datetime.now(timezone.utc)) is not None


def test_agent_waits_only_when_every_provider_it_can_use_is_limited(session):
    from hagent.orchestration import provider_wait_until
    agent, primary, backup = _two_runtime_agent(session)
    _limited(primary, "usage limit reached")
    assert provider_wait_until(session, agent) is None  # backup still usable
    _limited(backup, "session limit reached")
    wait = provider_wait_until(session, agent)
    assert wait is not None and wait > datetime.now(timezone.utc)


def test_usage_limit_failures_do_not_count_as_strikes_against_the_issue(session):
    from hagent.models import Issue, Project, Run, RunStatus
    from hagent.orchestration import _consecutive_failures
    agent, *_ = _two_runtime_agent(session)
    project = Project(workspace_id=agent.workspace_id, name="p")
    session.add(project); session.commit()
    issue = Issue(project_id=project.id, title="t")
    session.add(issue); session.commit()
    base = datetime.now(timezone.utc)
    for i, error in enumerate(["You've hit your usage limit", "429 too many requests", "session limit reached", "boom: real bug"]):
        session.add(Run(issue_id=issue.id, agent_id=agent.id, prompt="p", status=RunStatus.FAILED, error=error,
                        created_at=base - timedelta(minutes=i)))
    session.commit()
    assert _consecutive_failures(session, issue.id) == 1  # only the real bug counts


def test_backup_hitting_its_own_limit_is_recorded(session, mocker):
    agent, primary, backup = _two_runtime_agent(session)
    _record_models(mocker, RuntimeError("usage limit reached, try again at 5:23 PM"),
                   RuntimeError("You've hit your session limit - resets 5:20pm (America/New_York)"))
    try:
        execute_agent(agent, "hi", backup_runtime=backup)
    except Exception:
        pass
    assert primary.health_status == "limited"
    assert backup.health_status == "limited" and is_cooling_down(backup)


def test_dispatcher_holds_runs_until_the_providers_reset(cli_db, monkeypatch):
    from hagent import db as hagent_db, dispatcher
    from hagent.models import Issue, IssueStatus, Project, Run, RunStatus
    with hagent_db.SessionLocal() as s:
        agent, primary, backup = _two_runtime_agent(s)
        _limited(primary, "usage limit reached")
        _limited(backup, "usage limit reached")
        project = Project(workspace_id=agent.workspace_id, name="p")
        s.add(project); s.commit()
        issue = Issue(project_id=project.id, title="t", status=IssueStatus.IN_PROGRESS, assignee_agent_id=agent.id)
        s.add(issue); s.flush()
        s.add(Run(issue_id=issue.id, agent_id=agent.id, prompt="p", status=RunStatus.PENDING))
        s.commit()
        agent_id = agent.id
    started = []
    monkeypatch.setattr(dispatcher, "_execute", lambda run_id: started.append(run_id))
    monkeypatch.setattr(dispatcher, "_pool", lambda: type("P", (), {"submit": staticmethod(lambda fn, rid: fn(rid))})())
    dispatcher._inflight.clear()

    assert dispatcher.dispatch_pending() == 0 and started == []
    with hagent_db.SessionLocal() as s:
        reason = dispatcher.explain_queue(s)["queued"][0]["reason"]
        assert "providers to reset" in reason
        for runtime in (s.get(Agent, agent_id).runtime, s.get(Runtime, s.get(Agent, agent_id).backup_runtime_id)):
            runtime.health_status = "healthy"
        s.commit()
    assert dispatcher.dispatch_pending() == 1
    dispatcher._inflight.clear()
