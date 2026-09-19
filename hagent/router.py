"""Deterministic provider-neutral model routing with auditable decisions."""
from __future__ import annotations
from dataclasses import dataclass
import json, re
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from hagent.models import Project, RoutingDecision, RoutingPolicy, Runtime
from hagent.runtime_catalog import routing_capabilities

EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODES = {"auto", "provider_fixed", "model_fixed", "manual"}


@dataclass
class TaskSignals:
    task_type: str = "general"
    tool_depth: int = 0
    context_tokens: int = 0
    ambiguity: float = 0.
    risk: float = 0.
    expected_minutes: float = 1.
    latency_preference: str = "balanced"
    cost_budget_usd: float | None = None


def infer_signals(prompt, **overrides):
    value = str(prompt or "").casefold()
    task_type = "coding" if re.search(r"\b(code|implement|debug|refactor|test|repository)\b", value) else (
        "research" if re.search(r"\b(research|compare|sources?|evidence)\b", value) else (
        "planning" if re.search(r"\b(plan|architect|design|strategy)\b", value) else "general"))
    ambiguity = .7 if re.search(r"\b(maybe|unclear|explore|figure out|unknown)\b", value) else .2
    risk = .9 if re.search(r"\b(production|security|legal|medical|financial|migration|delete)\b", value) else .2
    tool_depth = min(10, len(re.findall(r"\b(run|inspect|search|edit|test|deploy|browse)\b", value)))
    expected = max(1., min(240., len(value.split()) / 20 + tool_depth * 2))
    data = dict(task_type=task_type, tool_depth=tool_depth,
                context_tokens=max(0, int(len(value.split()) * 1.35)),
                ambiguity=ambiguity, risk=risk, expected_minutes=expected)
    data.update({key: val for key, val in overrides.items() if val is not None})
    return TaskSignals(**data)


def complexity_score(signals):
    task_weight = {"general": .25, "coding": .65, "research": .6, "planning": .55}.get(signals.task_type, .45)
    return round(min(1., task_weight*.2 + min(1., signals.tool_depth/8)*.18
        + min(1., signals.context_tokens/100000)*.17 + min(1., signals.ambiguity)*.13
        + min(1., signals.risk)*.2 + min(1., signals.expected_minutes/120)*.12), 4)


def effort_for_score(score):
    if score < .28: return "low"
    if score < .5: return "medium"
    if score < .72: return "high"
    if score < .9: return "xhigh"
    return "max"


class ModelRouter:
    def __init__(self, session: Session, workspace_id: str, project_id: str | None = None):
        self.session, self.workspace_id, self.project_id = session, workspace_id, project_id
        selected = session.info.get("workspace_id")
        if selected and selected != workspace_id:
            raise PermissionError("Router scope is outside the selected workspace")
        if project_id:
            project = session.get(Project, project_id)
            if not project or project.workspace_id != workspace_id:
                raise PermissionError("Project is outside the routing workspace")

    def policy(self):
        statement = select(RoutingPolicy).where(RoutingPolicy.workspace_id == self.workspace_id)
        if self.project_id:
            project = self.session.scalar(statement.where(RoutingPolicy.project_id == self.project_id))
            if project: return project
        return self.session.scalar(statement.where(RoutingPolicy.project_id.is_(None)))

    def set_policy(self, **values):
        item = self.policy()
        if item is None or item.project_id != self.project_id:
            item = RoutingPolicy(
                workspace_id=self.workspace_id,
                project_id=self.project_id,
                mode="auto",
                max_effort="high",
                latency_preference="balanced",
            )
            self.session.add(item)
        allowed = {"mode", "provider", "model", "effort", "max_effort", "max_cost_usd",
                   "max_latency_ms", "latency_preference", "config_json"}
        for key, value in values.items():
            if key in {"max_cost_usd", "max_latency_ms"}:
                setattr(item, key, value)
            elif key in allowed and value is not None:
                setattr(item, key, value)
        if item.mode not in MODES: raise ValueError("Unsupported routing mode")
        if item.effort and item.effort not in EFFORTS: raise ValueError("Unsupported effort")
        if item.max_effort not in EFFORTS: raise ValueError("Unsupported maximum effort")
        self.session.commit()
        return self.policy_dict(item)

    @staticmethod
    def policy_dict(item):
        if not item:
            return {"mode": "provider_fixed", "max_effort": "high",
                    "latency_preference": "balanced"}
        return {key: getattr(item, key) for key in (
            "id", "workspace_id", "project_id", "mode", "provider", "model", "effort",
            "max_effort", "max_cost_usd", "max_latency_ms", "latency_preference", "config_json")}

    def classify(self, prompt, **signals):
        inferred = infer_signals(prompt, **signals)
        score = complexity_score(inferred)
        reasons = [f"task_type={inferred.task_type}", f"tool_depth={inferred.tool_depth}",
                   f"context_tokens={inferred.context_tokens}", f"risk={inferred.risk:.2f}",
                   f"ambiguity={inferred.ambiguity:.2f}"]
        return inferred, score, reasons

    @staticmethod
    def mapped_effort(capabilities, requested, cap):
        requested_index = min(EFFORTS.index(requested), EFFORTS.index(cap))
        supported = [item for item in capabilities.get("efforts", ["medium"]) if item in EFFORTS]
        if not supported:
            raise ValueError("Runtime declares no supported normalized effort tiers")
        eligible = [item for item in supported if EFFORTS.index(item) <= requested_index]
        normalized = max(eligible, key=EFFORTS.index) if eligible else min(supported, key=EFFORTS.index)
        return normalized, capabilities.get("effort_map", {}).get(normalized, "")

    @staticmethod
    def estimated_cost(capabilities, input_tokens, output_tokens):
        in_price, out_price = capabilities.get("input_cost_per_million"), capabilities.get("output_cost_per_million")
        if in_price is None or out_price is None:
            return 0. if capabilities.get("cost_rank") == 0 else None
        return round(input_tokens/1_000_000*float(in_price) + output_tokens/1_000_000*float(out_price), 6)

    def route(self, prompt, *, default_runtime=None, override=None, run_id=None,
              memory_session_id=None, persist=True, **signal_overrides):
        override = dict(override or {})
        policy = self.policy()
        policy_data = self.policy_dict(policy)
        mode = override.get("mode") or policy_data["mode"]
        if mode not in MODES: raise ValueError("Unsupported routing mode")
        inferred, score, reasons = self.classify(prompt, **signal_overrides)
        requested_effort = override.get("effort") or policy_data.get("effort") or effort_for_score(score)
        max_effort = policy_data.get("max_effort") or "high"
        if override.get("max_effort") in EFFORTS:
            max_effort = min(max_effort, override["max_effort"], key=EFFORTS.index)
        max_cost = override.get("max_cost_usd", policy_data.get("max_cost_usd"))
        max_latency = override.get("max_latency_ms", policy_data.get("max_latency_ms"))
        latency_pref = override.get("latency_preference") or policy_data.get("latency_preference") or inferred.latency_preference
        runtimes = list(self.session.scalars(select(Runtime).where(
            Runtime.workspace_id == self.workspace_id, Runtime.archived.is_(False))).all())
        if not runtimes: raise ValueError("No active runtimes are available")
        runtime_id = override.get("runtime_id")
        provider = override.get("provider") or policy_data.get("provider") or (
            default_runtime.type.value if default_runtime else "")
        model = override.get("model") or policy_data.get("model") or ""
        if mode == "manual":
            if not runtime_id and not default_runtime: raise ValueError("Manual mode requires runtime_id")
            candidates = [row for row in runtimes if row.id == (runtime_id or default_runtime.id)]
        elif mode == "model_fixed":
            candidates = [row for row in runtimes if (not provider or row.type.value == provider)
                          and (row.model == model or runtime_id == row.id)]
        elif mode == "provider_fixed":
            provider = provider or (default_runtime.type.value if default_runtime else "")
            candidates = [row for row in runtimes if row.type.value == provider]
            if default_runtime in candidates:
                candidates.remove(default_runtime); candidates.insert(0, default_runtime)
        else:
            candidates = runtimes
        if not candidates: raise ValueError("No runtime satisfies the fixed routing selection")
        output_tokens = max(512, min(32768, int(2048 + score*12288)))
        viable = []
        rejected = []
        for candidate_order, runtime in enumerate(candidates):
            capabilities = routing_capabilities(runtime)
            candidate_model = model if (mode in {"manual", "model_fixed"} and model) else runtime.model
            supported_models = capabilities.get("models") or [runtime.model]
            if candidate_model not in supported_models:
                rejected.append((runtime.id, "model not declared by runtime capabilities")); continue
            effort, provider_effort = self.mapped_effort(capabilities, requested_effort, max_effort)
            if inferred.context_tokens > int(capabilities.get("context_window", 32768)):
                rejected.append((runtime.id, "context exceeds capability")); continue
            expected_latency = int(capabilities.get("expected_latency_ms",
                (int(capabilities.get("latency_rank", 2))+1)*30000))
            if max_latency is not None and expected_latency > int(max_latency):
                rejected.append((runtime.id, "latency cap exceeded")); continue
            estimated = self.estimated_cost(capabilities, inferred.context_tokens, output_tokens)
            budget = inferred.cost_budget_usd if inferred.cost_budget_usd is not None else max_cost
            if budget is not None and (estimated is None or estimated > float(budget)):
                rejected.append((runtime.id, "cost cap cannot be satisfied")); continue
            adequacy = EFFORTS.index(effort)
            cost_rank = float(capabilities.get("cost_rank", 2))
            latency_rank = float(capabilities.get("latency_rank", 2))
            if latency_pref == "fast":
                rank = (latency_rank, cost_rank, -adequacy, candidate_order, runtime.id)
            elif latency_pref == "cheap":
                rank = (cost_rank, latency_rank, -adequacy, candidate_order, runtime.id)
            else:
                rank = (abs(adequacy-EFFORTS.index(requested_effort)),
                        cost_rank+latency_rank, candidate_order, runtime.id)
            viable.append((rank, runtime, candidate_model, effort, provider_effort, capabilities, estimated))
        if not viable:
            detail = "; ".join(f"{rid}: {why}" for rid, why in rejected)
            raise ValueError(f"No route satisfies policy and capability caps. {detail}")
        viable.sort(key=lambda row: row[0])
        _, runtime, selected_model, effort, provider_effort, capabilities, estimated = viable[0]
        fallback = [{"runtime_id": row[1].id, "provider": row[1].type.value,
                     "model": row[2], "effort": row[3], "provider_effort": row[4],
                     "estimated_cost_usd": row[6]}
                    for row in viable[1:4] if row[6] is not None and (estimated is None or row[6] <= max(estimated, max_cost or 0))]
        reasons.extend([f"mode={mode}", f"selected_runtime={runtime.name}",
                        f"normalized_effort={effort}", f"latency_preference={latency_pref}"])
        output_limit = min(output_tokens, int(capabilities.get("max_output", output_tokens)))
        timeout = max(30, min(3600, int(120 + inferred.expected_minutes*6 + score*300)))
        execution_mode = "extended" if score >= .72 else ("standard" if score >= .28 else "fast")
        decision = RoutingDecision(workspace_id=self.workspace_id, project_id=self.project_id,
            run_id=run_id, memory_session_id=memory_session_id, mode=mode,
            task_type=inferred.task_type, selected_runtime_id=runtime.id,
            selected_provider=runtime.type.value, selected_model=selected_model,
            effort=effort, provider_effort=provider_effort, complexity_score=score,
            reasons_json=json.dumps(reasons), fallback_json=json.dumps(fallback),
            output_limit=output_limit, timeout_seconds=timeout, execution_mode=execution_mode,
            estimated_cost_usd=estimated)
        if persist:
            self.session.add(decision); self.session.commit()
        return self.serialize(decision)

    def finalize(self, decision_id, *, input_tokens=None, output_tokens=None,
                 latency_ms=None, outcome="completed", actual_cost_usd=None):
        item = self.session.get(RoutingDecision, decision_id)
        if (not item or item.workspace_id != self.workspace_id
                or item.project_id != self.project_id):
            raise KeyError("Routing decision not found")
        item.input_tokens, item.output_tokens = input_tokens, output_tokens
        item.latency_ms, item.outcome, item.actual_cost_usd = latency_ms, outcome, actual_cost_usd
        self.session.commit()
        return self.serialize(item)

    @staticmethod
    def serialize(item):
        return {"id": item.id, "workspace_id": item.workspace_id, "project_id": item.project_id,
            "run_id": item.run_id, "memory_session_id": item.memory_session_id, "mode": item.mode,
            "task_type": item.task_type, "runtime_id": item.selected_runtime_id,
            "provider": item.selected_provider, "model": item.selected_model,
            "effort": item.effort, "provider_effort": item.provider_effort,
            "complexity_score": item.complexity_score, "reasons": json.loads(item.reasons_json or "[]"),
            "fallback": json.loads(item.fallback_json or "[]"), "output_limit": item.output_limit,
            "timeout_seconds": item.timeout_seconds, "execution_mode": item.execution_mode,
            "estimated_cost_usd": item.estimated_cost_usd, "actual_cost_usd": item.actual_cost_usd,
            "input_tokens": item.input_tokens, "output_tokens": item.output_tokens,
            "latency_ms": item.latency_ms, "outcome": item.outcome}
