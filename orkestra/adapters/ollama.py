"""Ollama runtime adapter — talks to a local Ollama daemon over its HTTP API."""

import httpx

from orkestra.adapters.base import BaseRuntime, RuntimeResult

DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "") -> RuntimeResult:
        base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        try:
            response = httpx.post(
                f"{base_url}/api/generate",
                json={"model": self.model, "prompt": full_prompt, "stream": False},
                timeout=self.config.get("timeout", 120),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama call failed ({base_url}): {exc}") from exc

        data = response.json()
        return RuntimeResult(output=data.get("response", ""), raw=data)
