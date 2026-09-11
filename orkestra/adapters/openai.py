"""OpenAI runtime adapter — HTTP call to the Chat Completions API.

Stub for Phase 1: implemented but not wired to a default key. Set OPENAI_API_KEY
or pass config={"api_key": ...} on the Runtime to use it.
"""

import os

import httpx

from orkestra.adapters.base import BaseRuntime, RuntimeResult

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "") -> RuntimeResult:
        api_key = self.config.get("api_key") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("No OpenAI API key configured (set OPENAI_API_KEY)")

        base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self.model, "messages": messages},
                timeout=self.config.get("timeout", 60),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI API call failed: {exc}") from exc

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        return RuntimeResult(output=text, raw=data)
