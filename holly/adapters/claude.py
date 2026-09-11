"""Claude runtime adapter, backed by the Anthropic SDK."""

import os

from holly.adapters.base import BaseRuntime, RuntimeResult


class ClaudeRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "") -> RuntimeResult:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package is not installed") from None

        api_key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("No Anthropic API key configured (set ANTHROPIC_API_KEY)")

        client = anthropic.Anthropic(api_key=api_key)
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=self.config.get("max_tokens", 4096),
                system=context or None,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise RuntimeError(f"Claude API call failed: {exc}") from exc

        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        return RuntimeResult(output=text, raw=message.model_dump())
