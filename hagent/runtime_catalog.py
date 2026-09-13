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
    "lmstudio": {
        "name": "LM Studio",
        "group": "Local",
        "runtime_type": "openai_compatible",
        "kind": "local_openai",
        "best_for": "Trying local Hugging Face models through an easy desktop server.",
        "note": "Start LM Studio's local server first, then enter the exact loaded model ID.",
        "base_url": "http://127.0.0.1:1234/v1",
        "access_url": "https://lmstudio.ai/",
        "access_label": "Get LM Studio",
        "allow_no_key": True,
        "models": [],
    },
    "openai": {
        "name": "OpenAI API",
        "group": "Direct cloud APIs",
        "runtime_type": "openai",
        "kind": "api",
        "best_for": "Coding, agents, research, analysis, writing, and broad professional work.",
        "note": "Cloud runtime. Your local GPU and RAM do not limit model size.",
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
        "access_url": "https://platform.openai.com/api-keys",
        "access_label": "Create OpenAI API key",
        "models": [
            model("gpt-6-astra", "GPT-6 Astra", "Hard coding, research, complex workflows", "Highest capability; highest cost."),
            model("gpt-5.6-sol", "GPT-5.6 Sol", "Coding and complex professional work", "Strong flagship choice."),
            model("gpt-5.6-terra", "GPT-5.6 Terra", "Everyday agents, writing, marketing", "Balanced intelligence and cost."),
            model("gpt-5.6-luna", "GPT-5.6 Luna", "High-volume routine tasks", "Fast and cost-sensitive."),
        ],
    },
    "claude": {
        "name": "Anthropic Claude API",
        "group": "Direct cloud APIs",
        "runtime_type": "claude",
        "kind": "api",
        "best_for": "Long-form writing, careful analysis, coding, document work, and instruction following.",
        "note": "Cloud runtime. Uses an Anthropic API key, not merely a claude.ai subscription.",
        "env": "ANTHROPIC_API_KEY",
        "access_url": "https://console.anthropic.com/settings/keys",
        "access_label": "Create Anthropic API key",
        "models": [
            model("claude-opus-5", "Claude Opus 5", "Deep reasoning, hard coding, research", "Maximum capability."),
            model("claude-sonnet-5", "Claude Sonnet 5", "Coding, writing, everyday agents", "Best general balance."),
            model("claude-fable-5-1", "Claude Fable 5.1", "Creative writing and communication", "Optimized for expressive work."),
            model("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "Fast classification and summaries", "Low-latency, lower-cost choice."),
        ],
    },
    "gemini": {
        "name": "Google Gemini API",
        "group": "Direct cloud APIs",
        "runtime_type": "gemini",
        "kind": "api",
        "best_for": "Multimodal work, coding, large-context documents, fast general tasks, and agents.",
        "note": "Cloud runtime through Google AI Studio.",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "env": "GEMINI_API_KEY",
        "access_url": "https://aistudio.google.com/apikey",
        "access_label": "Create Gemini API key",
        "models": [
            model("gemini-3.8-flash", "Gemini 3.8 Flash", "Long-horizon coding and agents", "Current stable Flash flagship."),
            model("gemini-3.7-flash", "Gemini 3.7 Flash", "Coding and reliable workflows", "Strong previous-generation option."),
            model("gemini-3.6-flash", "Gemini 3.6 Flash", "General multimodal work", "Balanced speed and capability."),
            model("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "Marketing variants and high-volume tasks", "Fastest and most economical."),
            model("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "Complex reasoning and coding", "Preview model; availability may change."),
            model("gemini-2.5-pro", "Gemini 2.5 Pro", "Deep reasoning and documents", "Older stable thinking model."),
        ],
    },
    "xai": {
        "name": "xAI Grok API",
        "group": "Direct cloud APIs",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Coding, tool-using agents, current-event workflows, and general reasoning.",
        "note": "OpenAI-compatible xAI endpoint. Search features may require provider-specific tools.",
        "base_url": "https://api.x.ai/v1",
        "env": "XAI_API_KEY",
        "access_url": "https://console.x.ai/",
        "access_label": "Open xAI Console",
        "models": [
            model("grok-4.6", "Grok 4.6", "Coding, agents, general reasoning", "xAI's recommended text model."),
            model("grok-4.20-reasoning", "Grok 4.20 Reasoning", "Complex reasoning", "Use for difficult multi-step work."),
            model("grok-4.20-non-reasoning", "Grok 4.20 Non-reasoning", "Fast chat and writing", "Lower-latency responses."),
        ],
    },
    "mistral": {
        "name": "Mistral AI",
        "group": "Direct cloud APIs",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Coding, multilingual writing, extraction, summarization, and European-hosted workflows.",
        "note": "Uses Mistral's OpenAI-compatible chat endpoint.",
        "base_url": "https://api.mistral.ai/v1",
        "env": "MISTRAL_API_KEY",
        "access_url": "https://console.mistral.ai/api-keys",
        "access_label": "Create Mistral API key",
        "models": [
            model("mistral-large-latest", "Mistral Large", "Complex reasoning and writing", "Highest-capability general Mistral model."),
            model("mistral-medium-latest", "Mistral Medium", "General agents and analysis", "Balanced option."),
            model("mistral-small-latest", "Mistral Small", "Fast summaries and extraction", "Efficient production choice."),
            model("codestral-latest", "Codestral", "Coding", "Purpose-built for software work."),
        ],
    },
    "deepseek": {
        "name": "DeepSeek API",
        "group": "Direct cloud APIs",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Coding, reasoning, math, and cost-efficient agent workflows.",
        "note": "Uses DeepSeek's OpenAI-compatible API.",
        "base_url": "https://api.deepseek.com",
        "env": "DEEPSEEK_API_KEY",
        "access_url": "https://platform.deepseek.com/api_keys",
        "access_label": "Create DeepSeek API key",
        "models": [
            model("deepseek-v4-pro", "DeepSeek V4 Pro", "Hard reasoning and coding", "Higher-capability V4 model."),
            model("deepseek-v4-flash", "DeepSeek V4 Flash", "Fast coding and agents", "Cost-efficient with tool calling."),
        ],
    },
    "perplexity": {
        "name": "Perplexity Sonar",
        "group": "Search and research",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Web-grounded answers, market research, current events, and cited reports.",
        "note": "Sonar responses use live web search; research models trade speed for depth.",
        "base_url": "https://api.perplexity.ai",
        "env": "PERPLEXITY_API_KEY",
        "access_url": "https://www.perplexity.ai/settings/api",
        "access_label": "Open Perplexity API settings",
        "models": [
            model("sonar", "Sonar", "Fast factual search and summaries", "Lightweight grounded search."),
            model("sonar-pro", "Sonar Pro", "Product comparison and market research", "Advanced grounded search."),
            model("sonar-reasoning-pro", "Sonar Reasoning Pro", "Complex researched analysis", "Search plus multi-step reasoning."),
            model("sonar-deep-research", "Sonar Deep Research", "Exhaustive research reports", "Slowest and most comprehensive."),
        ],
    },
    "groq": {
        "name": "GroqCloud",
        "group": "Model gateways",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Very fast inference, voice pipelines, coding loops, and high-throughput agents.",
        "note": "Hosted models run on Groq hardware; your laptop specs do not limit them.",
        "base_url": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
        "access_url": "https://console.groq.com/keys",
        "access_label": "Create Groq API key",
        "models": [
            model("openai/gpt-oss-20b", "GPT-OSS 20B", "Fast coding and reasoning", "Very high hosted throughput."),
            model("openai/gpt-oss-120b", "GPT-OSS 120B", "Stronger reasoning and agents", "Large hosted open model."),
            model("qwen/qwen3.8-27b", "Qwen 3.8 27B", "Coding and multilingual tasks", "Preview availability may change."),
            model("groq/compound", "Groq Compound", "Web search and tool-assisted answers", "Managed system with built-in tools."),
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
            model("~openai/gpt-latest", "Latest OpenAI flagship", "Coding and general work", "OpenRouter rolling alias."),
            model("anthropic/claude-sonnet-5", "Claude Sonnet 5", "Writing and coding", "Anthropic through OpenRouter."),
            model("google/gemini-3.8-flash", "Gemini 3.8 Flash", "Multimodal coding and agents", "Google through OpenRouter."),
            model("x-ai/grok-4.6", "Grok 4.6", "Reasoning and current workflows", "xAI through OpenRouter."),
            model("openai/gpt-oss-20b", "GPT-OSS 20B", "Economical coding", "Hosted open-weight model."),
        ],
    },
    "together": {
        "name": "Together AI",
        "group": "Model gateways",
        "runtime_type": "openai_compatible",
        "kind": "api",
        "best_for": "Hosted open models, coding, fine-tuned models, and scalable inference.",
        "note": "OpenAI-compatible gateway for many open-source model families.",
        "base_url": "https://api.together.xyz/v1",
        "env": "TOGETHER_API_KEY",
        "access_url": "https://api.together.ai/settings/api-keys",
        "access_label": "Create Together API key",
        "models": [
            model("openai/gpt-oss-20b", "GPT-OSS 20B", "Coding and routine reasoning", "Fast economical open model."),
            model("openai/gpt-oss-120b", "GPT-OSS 120B", "Complex reasoning", "Large hosted model."),
            model("moonshotai/Kimi-K2.5", "Kimi K2.5", "Agentic coding and long context", "Strong open agent model."),
            model("deepseek-ai/DeepSeek-V3", "DeepSeek V3", "Coding and general tasks", "Hosted DeepSeek family model."),
        ],
    },
    "gemini_cli": {
        "name": "Gemini CLI",
        "group": "Installed assistants",
        "runtime_type": "gemini_cli",
        "kind": "cli",
        "best_for": "Coding and agent tasks using your existing Gemini CLI login.",
        "note": "Runs the official CLI in headless JSON mode. No API key is stored in Hagent.",
        "command": "gemini",
        "access_url": "https://github.com/google-gemini/gemini-cli",
        "access_label": "Install Gemini CLI",
        "models": [
            model("gemini-3.8-flash", "Gemini 3.8 Flash", "Coding and long agent tasks", "Recommended."),
            model("gemini-3.7-flash", "Gemini 3.7 Flash", "General coding", "Reliable alternative."),
            model("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "Complex reasoning", "Preview availability may vary."),
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