"""Runtime adapter interface.

Every AI backend (Claude, OpenAI, local Ollama, or a future provider) implements
this one interface. The task engine, CLI, and web dashboard only ever talk to
`BaseRuntime.run(...)` — adding a new provider never touches those layers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class RuntimeResult:
    output: str
    raw: dict | None = None


class BaseRuntime(ABC):
    def __init__(self, model: str, config: dict):
        self.model = model
        self.config = config

    @abstractmethod
    def run(
        self,
        prompt: str,
        context: str = "",
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, dict], dict | str] | None = None,
    ) -> RuntimeResult:
        """Execute a prompt (with optional agent instructions as context) and return the result.

        Raises RuntimeError (or a subclass) on failure — callers persist that as the
        Task's error and mark it FAILED rather than letting exceptions propagate raw.
        """
        raise NotImplementedError
