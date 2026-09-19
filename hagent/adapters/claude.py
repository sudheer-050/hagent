"""Claude runtime adapter, backed by the Anthropic SDK."""

import os

from hagent.adapters.base import BaseRuntime, RuntimeResult


class ClaudeRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package is not installed") from None

        api_key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("No Anthropic API key configured (set ANTHROPIC_API_KEY)")

        client = anthropic.Anthropic(api_key=api_key)
        messages = [{"role": "user", "content": prompt}]
        transcript = []
        try:
            for _ in range(self.config.get("max_tool_rounds", 8)):
                kwargs = {
                    "model": self.model,
                    "max_tokens": self.config.get("max_tokens", 4096),
                    "system": context or None,
                    "messages": messages,
                }
                if tools:
                    kwargs["tools"] = tools
                effort_parameter = self.config.get("effort_parameter")
                if effort_parameter and self.config.get("reasoning_effort"):
                    kwargs[effort_parameter] = self.config["reasoning_effort"]
                message = client.messages.create(**kwargs)
                raw = message.model_dump() if hasattr(message, "model_dump") else {}
                transcript.append(raw)
                if getattr(message, "stop_reason", None) != "tool_use" or not tool_executor:
                    text = "".join(getattr(block, "text", "") for block in message.content if getattr(block, "type", None) == "text")
                    return RuntimeResult(output=text, raw={"messages": transcript} if tools else raw)
                messages.append({"role": "assistant", "content": [getattr(block, "model_dump", lambda: block)() for block in message.content]})
                results = []
                for block in message.content:
                    if getattr(block, "type", None) == "tool_use":
                        result = tool_executor(block.name, block.input or {})
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result)})
                messages.append({"role": "user", "content": results})
            raise RuntimeError("Claude tool-calling loop exceeded max_tool_rounds")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Claude API call failed: {exc}") from exc
