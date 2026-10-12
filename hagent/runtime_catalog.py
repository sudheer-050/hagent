"""Provider, model, and local-hardware metadata used by the runtime UI."""

from functools import lru_cache
import ctypes
import os
import platform
import re
import subprocess


def model(model_id, label, best_for, note, parameters_b=None):
    item = {"id": model_id, "label": label, "best_for": best_for, "note": note}
    if parameters_b is not None:
        item["parameters_b"] = parameters_b
    return item


PROVIDERS = {
    "ollama": {
        "name": "Ollama",
        "group": "Local",
        "runtime_type": "ollama",
        "kind": "local",
        "best_for": "Private local chat, drafting, lightweight coding, and offline work.",
        "note": "Runs on this computer. Model size matters; Hagent checks it against your RAM and GPU.",
        "base_url": "http://127.0.0.1:11434",
        "access_url": "https://ollama.com/download",
        "access_label": "Install Ollama",
        "models": [
            model("qwen3:8b", "Qwen 3 8B", "Coding, reasoning, general work", "Recommended balance for this PC.", 8),
            model("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B", "Coding", "Fast local coding and code explanation.", 7),
            model("llama3.1:8b", "Llama 3.1 8B", "Writing, chat, summaries", "Reliable general-purpose local assistant.", 8),
            model("deepseek-r1:8b", "DeepSeek R1 8B", "Reasoning, math, coding", "Small reasoning model; slower than plain chat models.", 8),
            model("gemma3:4b", "Gemma 3 4B", "Fast writing and routine tasks", "Very comfortable on modest hardware.", 4),
            model("phi4-mini:3.8b", "Phi-4 Mini", "Fast instructions and utilities", "Low memory use and quick responses.", 3.8),
            model("qwen3:14b", "Qwen 3 14B", "Stronger reasoning and coding", "May spill into system RAM on an 8 GB GPU.", 14),
            model("qwen3:30b", "Qwen 3 30B", "Complex reasoning", "Not recommended on an 8 GB laptop GPU.", 30),
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "group": "Model gateways",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Choosing among hundreds of models with one key, fallbacks, and cost routing.",
        "note": "Best broad-coverage option: one OpenAI-compatible endpoint exposes 400+ cloud models.",
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
        "access_url": "https://openrouter.ai/settings/keys",
        "access_label": "Create OpenRouter key",
        "models": [
            model("stealth/union-alpha", "Union Alpha", "Coding, research, and agentic workflows", "Free stealth preview with text and image input; provider identity and availability may change."),
            model("~openai/gpt-latest", "Latest OpenAI flagship", "Coding and general work", "OpenRouter rolling alias."),
            model("anthropic/claude-sonnet-5", "Claude Sonnet 5", "Writing and coding", "Anthropic through OpenRouter."),
            model("google/gemini-3.8-flash", "Gemini 3.8 Flash", "Multimodal coding and agents", "Google through OpenRouter."),
            model("x-ai/grok-4.6", "Grok 4.6", "Reasoning and current workflows", "xAI through OpenRouter."),
            model("openai/gpt-oss-20b", "GPT-OSS 20B", "Economical coding", "Hosted open-weight model."),
        ],
    },
    "codex_cli": {
        "name": "Codex CLI",
        "group": "Installed assistants",
        "runtime_type": "codex_cli",
        "kind": "cli",
        "best_for": "Repository analysis, coding, review, debugging, and long-running software tasks.",
        "note": "Uses your saved Codex login. Hagent invokes non-interactive, ephemeral, read-only mode.",
        "command": "codex",
        "access_url": "https://learn.chatgpt.com/docs/non-interactive-mode",
        "access_label": "Codex CLI setup",
        "models": [
            model("default", "Account default", "Coding and repository work", "Uses your configured Codex model."),
            model("gpt-6-astra", "GPT-6 Astra", "Hard end-to-end coding", "Requires account access."),
            model("gpt-5.6-sol", "GPT-5.6 Sol", "Complex coding", "Strong general coding model."),
        ],
    },
    "claude_code": {
        "name": "Claude Code",
        "group": "Installed assistants",
        "runtime_type": "claude_code",
        "kind": "cli",
        "best_for": "Repository analysis, coding, refactoring, and careful long-form implementation.",
        "note": "Uses the installed Claude Code login and print-mode JSON output.",
        "command": "claude",
        "access_url": "https://docs.anthropic.com/en/docs/claude-code/getting-started",
        "access_label": "Install Claude Code",
        "models": [
            model("default", "Account default", "Coding and repository work", "Uses your configured Claude Code model."),
            model("sonnet", "Latest Sonnet", "Everyday coding", "Balanced capability and speed."),
            model("opus", "Latest Opus", "Hard coding and analysis", "Highest capability; higher usage."),
        ],
    },
    "openai_compatible": {
        "name": "Other OpenAI-compatible API",
        "group": "Advanced",
        "runtime_type": "openai_compatible",
        "kind": "custom",
        "best_for": "Any compatible provider, proxy, self-hosted server, or future service not listed above.",
        "note": "Enter the service's /v1 base URL and exact model ID.",
        "env": "OPENAI_API_KEY",
        "access_url": "",
        "access_label": "",
        "models": [],
    },
}

# Provider-neutral routing defaults. A runtime's config_json.capabilities object
# can override these values with provider metadata discovered by an operator.
# Model IDs deliberately do not appear here.
ROUTING_CAPABILITIES = {
    "ollama": {"efforts": ["low", "medium"], "effort_map": {"low": "", "medium": ""},
               "cost_rank": 0, "latency_rank": 1, "context_window": 16384, "max_output": 4096},
    "codex_cli": {"efforts": ["low", "medium", "high", "xhigh", "max"],
                  "effort_map": {"low": "low", "medium": "medium", "high": "high",
                                 "xhigh": "xhigh", "max": "max"},
                  "cost_rank": 2, "latency_rank": 2, "context_window": 200000, "max_output": 32768},
    "claude_code": {"efforts": ["low", "medium", "high", "xhigh", "max"],
                    "effort_map": {"low": "low", "medium": "medium", "high": "high",
                                   "xhigh": "xhigh", "max": "max"},
                    "cost_rank": 2, "latency_rank": 2, "context_window": 200000, "max_output": 32768},
    "openai_compatible": {"efforts": ["medium"], "effort_map": {"medium": ""},
                          "cost_rank": 2, "latency_rank": 2, "context_window": 32768, "max_output": 8192},
    "generic_cli": {"efforts": ["medium"], "effort_map": {"medium": ""},
                    "cost_rank": 2, "latency_rank": 2, "context_window": 32768, "max_output": 8192},
}


def routing_capabilities(runtime):
    """Resolve capabilities from central defaults plus runtime/provider metadata."""
    import json
    runtime_type = runtime.type.value if hasattr(runtime.type, "value") else str(runtime.type)
    result = dict(ROUTING_CAPABILITIES.get(runtime_type, ROUTING_CAPABILITIES["openai_compatible"]))
    try:
        configured = json.loads(runtime.config_json or "{}").get("capabilities", {})
    except (TypeError, ValueError):
        configured = {}
    if isinstance(configured, dict):
        result.update(configured)
    result["provider"] = runtime_type
    result["model"] = runtime.model
    return result


def provider_options():
    return [{"id": provider_id, **provider} for provider_id, provider in PROVIDERS.items()]


def provider_config(provider_id):
    return PROVIDERS.get(provider_id, PROVIDERS["openai_compatible"])


@lru_cache(maxsize=1)
def local_hardware_profile():
    ram_gb = None
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram_gb = round(status.ullTotalPhys / (1024 ** 3), 1)

    gpu_name, vram_gb = "", None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().splitlines()[0]
            gpu_name, memory_mb = [part.strip() for part in line.rsplit(",", 1)]
            vram_gb = round(float(memory_mb) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    return {
        "cpu": platform.processor() or platform.machine(),
        "ram_gb": ram_gb,
        "gpu": gpu_name or "No NVIDIA GPU detected",
        "vram_gb": vram_gb,
    }


def local_fit(parameters_b, hardware=None):
    hardware = hardware or local_hardware_profile()
    vram = hardware.get("vram_gb") or 0
    ram = hardware.get("ram_gb") or 0
    if parameters_b is None:
        return {"level": "unknown", "label": "Check model size", "note": "Size could not be inferred."}
    if parameters_b <= max(4, vram):
        return {"level": "good", "label": "Good fit", "note": "Expected to run comfortably when quantized."}
    if parameters_b <= 14 and ram >= 24:
        return {"level": "tight", "label": "May be slow", "note": "Likely to spill into system RAM; use a small context."}
    return {"level": "poor", "label": "Not recommended", "note": "Choose a 7B–9B model for this computer."}


def infer_parameters(model_id):
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)b(?!\w)", model_id.lower())
    return float(matches[-1]) if matches else None


def catalog_for(provider_id, installed_models=None):
    provider = provider_config(provider_id)
    models = [dict(item) for item in provider.get("models", [])]
    source = "recommended"
    hardware = None
    if provider_id == "ollama":
        hardware = local_hardware_profile()
        if installed_models:
            known = {item["id"]: item for item in models}
            models = [
                dict(known.get(model_id, model(model_id, model_id, "Local model", "Installed in Ollama", infer_parameters(model_id))))
                for model_id in installed_models
            ]
            source = "installed"
        for item in models:
            item["fit"] = local_fit(item.get("parameters_b"), hardware)
    return {
        "provider": provider_id,
        "provider_info": {
            key: provider.get(key)
            for key in ("name", "group", "kind", "best_for", "note", "base_url", "env", "command", "access_url", "access_label", "allow_no_key")
        },
        "models": [item["id"] for item in models],
        "model_details": models,
        "source": source,
        "hardware": hardware,
    }
