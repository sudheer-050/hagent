from hagent.adapters.base import BaseRuntime, RuntimeResult
from hagent.adapters.claude import ClaudeRuntime
from hagent.adapters.claude_code import ClaudeCodeRuntime
from hagent.adapters.codex_cli import CodexCliRuntime
from hagent.adapters.gemini import GeminiRuntime
from hagent.adapters.gemini_cli import GeminiCliRuntime
from hagent.adapters.ollama import OllamaRuntime
from hagent.adapters.openai import OpenAIRuntime
from hagent.models import RuntimeType

RUNTIME_CLASSES: dict[RuntimeType, type[BaseRuntime]] = {
    RuntimeType.CLAUDE: ClaudeRuntime,
    RuntimeType.OLLAMA: OllamaRuntime,
    RuntimeType.OPENAI: OpenAIRuntime,
    RuntimeType.OPENAI_COMPATIBLE: OpenAIRuntime,
    RuntimeType.GEMINI: GeminiRuntime,
    RuntimeType.GEMINI_CLI: GeminiCliRuntime,
    RuntimeType.CODEX_CLI: CodexCliRuntime,
    RuntimeType.CLAUDE_CODE: ClaudeCodeRuntime,
}


def get_runtime_class(runtime_type: RuntimeType) -> type[BaseRuntime]:
    try:
        return RUNTIME_CLASSES[runtime_type]
    except KeyError:
        raise ValueError(f"Unsupported runtime type: {runtime_type}") from None


__all__ = [
    "BaseRuntime",
    "RuntimeResult",
    "ClaudeRuntime",
    "ClaudeCodeRuntime",
    "CodexCliRuntime",
    "GeminiRuntime",
    "GeminiCliRuntime",
    "OllamaRuntime",
    "OpenAIRuntime",
    "get_runtime_class",
]
