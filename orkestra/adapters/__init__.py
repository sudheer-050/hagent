from orkestra.adapters.base import BaseRuntime, RuntimeResult
from orkestra.adapters.claude import ClaudeRuntime
from orkestra.adapters.ollama import OllamaRuntime
from orkestra.adapters.openai import OpenAIRuntime
from orkestra.models import RuntimeType

RUNTIME_CLASSES: dict[RuntimeType, type[BaseRuntime]] = {
    RuntimeType.CLAUDE: ClaudeRuntime,
    RuntimeType.OLLAMA: OllamaRuntime,
    RuntimeType.OPENAI: OpenAIRuntime,
}


def get_runtime_class(runtime_type: RuntimeType) -> type[BaseRuntime]:
    try:
        return RUNTIME_CLASSES[runtime_type]
    except KeyError:
        raise ValueError(f"Unsupported runtime type: {runtime_type}") from None


__all__ = ["BaseRuntime", "RuntimeResult", "ClaudeRuntime", "OllamaRuntime", "OpenAIRuntime", "get_runtime_class"]
