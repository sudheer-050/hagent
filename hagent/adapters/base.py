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
    input_tokens: int | None = None
    output_tokens: int | None = None
    session_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.raw, (dict, list)):
            return
        input_total = output_total = None
        pending = [self.raw]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
                continue
            if not isinstance(item, dict):
                continue

            def add_counts(input_count, output_count):
                nonlocal input_total, output_total
                if input_count is not None:
                    input_total = (input_total or 0) + input_count
                if output_count is not None:
                    output_total = (output_total or 0) + output_count

            usage = item.get('usage')
            if isinstance(usage, dict):
                input_field, output_field = (
                    ('prompt_tokens', 'completion_tokens')
                    if 'prompt_tokens' in usage or 'completion_tokens' in usage
                    else ('input_tokens', 'output_tokens')
                )
                input_count, output_count = extract_token_usage(
                    item, usage_field='usage', input_field=input_field, output_field=output_field
                )
                add_counts(input_count, output_count)
            if isinstance(item.get('usageMetadata'), dict):
                add_counts(*extract_token_usage(
                    item, usage_field='usageMetadata',
                    input_field='promptTokenCount', output_field='candidatesTokenCount',
                ))
            if 'prompt_eval_count' in item or 'eval_count' in item:
                add_counts(*extract_token_usage(
                    item, usage_field=None,
                    input_field='prompt_eval_count', output_field='eval_count',
                ))
            pending.extend(item.values())
        if self.input_tokens is None:
            self.input_tokens = input_total
        if self.output_tokens is None:
            self.output_tokens = output_total


def extract_token_usage(
    payload: dict,
    *,
    usage_field='usage',
    input_field='prompt_tokens',
    output_field='completion_tokens',
) -> tuple[int | None, int | None]:
    '''Read provider token counts without guessing when they are absent.'''
    usage = payload.get(usage_field, {}) if usage_field else payload
    if not isinstance(usage, dict):
        return None, None

    def count(field):
        value = usage.get(field)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    return count(input_field), count(output_field)


class ResumableError(RuntimeError):
    """A run failed (usually a timeout) but a session id was recovered from whatever
    partial output the CLI produced before failing - the work isn't necessarily lost,
    it can potentially be picked up with 'issue continue' instead of starting over."""

    def __init__(self, message: str, session_id: str | None = None):
        super().__init__(message)
        self.session_id = session_id


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
