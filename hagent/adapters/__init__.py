from importlib import import_module

from hagent.adapters.base import BaseRuntime, RuntimeResult
from hagent.models import RuntimeType

# Adapter modules are imported on first use so commands that never run a model
# (most of the CLI) don't pay for httpx and every provider's dependencies.
_ADAPTER_MODULES: dict[str, str] = {
    "AntigravityCliRuntime": "hagent.adapters.antigravity_cli",
    "ClaudeRuntime": "hagent.adapters.claude",
    "ClaudeCodeRuntime": "hagent.adapters.claude_code",
    "CodexCliRuntime": "hagent.adapters.codex_cli",
    "GeminiRuntime": "hagent.adapters.gemini",
    "GeminiCliRuntime": "hagent.adapters.gemini_cli",
    "GenericCliRuntime": "hagent.adapters.generic_cli",
    "OllamaRuntime": "hagent.adapters.ollama",
    "OpencodeCliRuntime": "hagent.adapters.opencode_cli",
    "OpenAIRuntime": "hagent.adapters.openai",
}

_RUNTIME_CLASS_NAMES: dict[RuntimeType, str] = {
    RuntimeType.CLAUDE: "ClaudeRuntime",
    RuntimeType.OLLAMA: "OllamaRuntime",
    RuntimeType.OPENAI: "OpenAIRuntime",
    RuntimeType.OPENAI_COMPATIBLE: "OpenAIRuntime",
    RuntimeType.GEMINI: "GeminiRuntime",
    RuntimeType.GEMINI_CLI: "GeminiCliRuntime",
    RuntimeType.CODEX_CLI: "CodexCliRuntime",
    RuntimeType.CLAUDE_CODE: "ClaudeCodeRuntime",
    RuntimeType.ANTIGRAVITY_CLI: "AntigravityCliRuntime",
    RuntimeType.OPENCODE_CLI: "OpencodeCliRuntime",
    RuntimeType.GENERIC_CLI: "GenericCliRuntime",
}


def __getattr__(name: str):
    module_path = _ADAPTER_MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_path), name)
    globals()[name] = value
    return value


def get_runtime_class(runtime_type: RuntimeType) -> type[BaseRuntime]:
    try:
        name = _RUNTIME_CLASS_NAMES[runtime_type]
    except KeyError:
        raise ValueError(f"Unsupported runtime type: {runtime_type}") from None
    return __getattr__(name)


__all__ = [
    "BaseRuntime",
    "RuntimeResult",
    *_ADAPTER_MODULES,
    "get_runtime_class",
]
