import json

import pytest

from hagent.client_wrappers import build_claude_args, build_codex_args
from hagent.models import Project, RoutingDecision, Runtime, RuntimeType, Workspace
from hagent.router import ModelRouter, complexity_score, infer_signals


def runtime(session, workspace_id, name, runtime_type, model, capabilities):
    item = Runtime(
        workspace_id=workspace_id,
        name=name,
        type=runtime_type,
        model=model,
        config_json=json.dumps({"capabilities": capabilities}),
    )
    session.add(item)
    session.flush()
    return item


def test_complexity_classification_is_deterministic_and_signal_sensitive():
    simple = infer_signals("Summarize this note")
    complex_task = infer_signals(
        "Implement and test a production security migration; inspect, search, edit, run, and deploy",
        context_tokens=90_000,
        ambiguity=.8,
        risk=.95,
        expected_minutes=120,
    )
    assert complexity_score(simple) == complexity_score(infer_signals("Summarize this note"))
    assert complexity_score(complex_task) > complexity_score(simple)


def test_project_policy_precedence_and_manual_override(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    session.flush()
    project = Project(workspace_id=workspace.id, name="Widget")
    session.add(project)
    one = runtime(session, workspace.id, "Codex A", RuntimeType.CODEX_CLI, "model-a", {
        "models": ["model-a"], "efforts": ["low", "medium", "high"],
        "effort_map": {"low": "low", "medium": "medium", "high": "high"},
        "cost_rank": 1, "latency_rank": 1,
    })
    two = runtime(session, workspace.id, "Codex B", RuntimeType.CODEX_CLI, "model-b", {
        "models": ["model-b", "chosen-model"], "efforts": ["low", "medium", "high"],
        "effort_map": {"low": "low", "medium": "medium", "high": "high"},
        "cost_rank": 1, "latency_rank": 1,
    })
    session.commit()

    ModelRouter(session, workspace.id).set_policy(mode="provider_fixed", provider="codex_cli")
    project_router = ModelRouter(session, workspace.id, project.id)
    inherited = project_router.route("routine task", default_runtime=one)
    assert inherited["runtime_id"] == one.id

    project_router.set_policy(mode="model_fixed", provider="codex_cli", model="model-b")
    selected = project_router.route("routine task", default_runtime=one)
    assert selected["runtime_id"] == two.id
    assert selected["model"] == "model-b"

    manual = project_router.route(
        "user selected route",
        default_runtime=one,
        override={"mode": "manual", "runtime_id": two.id, "model": "chosen-model", "effort": "high"},
    )
    assert manual["runtime_id"] == two.id
    assert manual["model"] == "chosen-model"
    assert manual["effort"] == "high"

    project_router.set_policy(max_cost_usd=.5, max_latency_ms=1000)
    cleared = project_router.set_policy(max_cost_usd=None, max_latency_ms=None)
    assert cleared["max_cost_usd"] is None
    assert cleared["max_latency_ms"] is None


def test_router_rejects_cross_workspace_project(session):
    one = Workspace(name="One")
    two = Workspace(name="Two")
    session.add_all([one, two])
    session.flush()
    foreign_project = Project(workspace_id=two.id, name="Foreign")
    session.add(foreign_project)
    session.commit()
    with pytest.raises(PermissionError, match="outside the routing workspace"):
        ModelRouter(session, one.id, foreign_project.id)


def test_caps_fallbacks_capability_validation_and_audit(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    session.flush()
    fast = runtime(session, workspace.id, "Fast", RuntimeType.CODEX_CLI, "fast-model", {
        "models": ["fast-model"], "efforts": ["low", "medium"],
        "effort_map": {"low": "minimal", "medium": "standard"},
        "cost_rank": 0, "latency_rank": 0, "expected_latency_ms": 500,
        "input_cost_per_million": 0, "output_cost_per_million": 0,
        "context_window": 200_000, "max_output": 4_000,
    })
    backup = runtime(session, workspace.id, "Backup", RuntimeType.CLAUDE_CODE, "backup-model", {
        "models": ["backup-model"], "efforts": ["low", "medium", "high"],
        "effort_map": {"low": "low", "medium": "medium", "high": "high"},
        "cost_rank": 0, "latency_rank": 1, "expected_latency_ms": 900,
        "input_cost_per_million": 0, "output_cost_per_million": 0,
        "context_window": 200_000, "max_output": 8_000,
    })
    costly = runtime(session, workspace.id, "Costly", RuntimeType.OPENAI, "costly-model", {
        "models": ["costly-model"], "efforts": ["medium"],
        "effort_map": {"medium": "provider-medium"},
        "cost_rank": 4, "latency_rank": 4, "expected_latency_ms": 60_000,
        "input_cost_per_million": 100, "output_cost_per_million": 100,
        "context_window": 200_000,
    })
    session.commit()
    router = ModelRouter(session, workspace.id)

    route = router.route(
        "Implement and test this repository migration",
        default_runtime=fast,
        override={"mode": "auto", "max_effort": "medium", "max_cost_usd": .01},
        context_tokens=20_000,
        risk=.8,
    )
    assert route["runtime_id"] == fast.id
    assert route["effort"] == "medium"
    assert route["provider_effort"] == "standard"
    assert route["output_limit"] <= 4_000
    assert [entry["runtime_id"] for entry in route["fallback"]] == [backup.id]
    assert route["fallback"][0]["provider_effort"] in {"medium", "high"}

    finalized = router.finalize(
        route["id"], input_tokens=120, output_tokens=45, latency_ms=321,
        outcome="fallback_completed", actual_cost_usd=0,
    )
    assert finalized["outcome"] == "fallback_completed"
    assert finalized["input_tokens"] == 120
    assert session.get(RoutingDecision, route["id"]).selected_runtime_id == fast.id

    with pytest.raises(ValueError, match="No route satisfies"):
        router.route(
            "large task",
            override={"mode": "manual", "runtime_id": costly.id, "max_cost_usd": .000001},
            context_tokens=100_000,
        )
    with pytest.raises(ValueError, match="No route satisfies"):
        router.route(
            "wrong model",
            override={"mode": "manual", "runtime_id": fast.id, "model": "undeclared"},
        )


def test_supported_cli_wrapper_flags_only():
    codex = build_codex_args(
        "codex",
        {"model": "selected", "provider_effort": "high"},
        {
            "HAGENT_MEMORY_WORKSPACE_ID": "ws",
            "HAGENT_MEMORY_USER_ID": "user",
            "HAGENT_MEMORY_PROJECT_ID": "project",
        },
        prompt="do work",
    )
    assert codex[:3] == ["codex", "--model", "selected"]
    assert "model_reasoning_effort='high'" in codex
    assert "mcp_servers.hagent_memory.command" in " ".join(codex)

    claude = build_claude_args(
        "claude", {"model": "sonnet", "provider_effort": "xhigh"}, "memory.json",
        prompt="do work",
    )
    assert claude == ["claude", "--mcp-config", "memory.json", "--model", "sonnet",
                      "--effort", "xhigh", "do work"]
