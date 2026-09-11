"""Ollama runtime adapter — talks to a local Ollama daemon over its HTTP API."""

import httpx
import json

from hagent.adapters.base import BaseRuntime, RuntimeResult

DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        if not tools:
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

        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})
        transcript = []
        for _ in range(self.config.get("max_tool_rounds", 8)):
            try:
                response = httpx.post(
                    f"{base_url}/api/chat",
                    json={"model": self.model, "messages": messages, "tools": tools, "stream": False},
                    timeout=self.config.get("timeout", 120),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Ollama call failed ({base_url}): {exc}") from exc
            data = response.json()
            transcript.append(data)
            message = data.get("message", {})
            calls = message.get("tool_calls") or []
            if not calls or not tool_executor:
                return RuntimeResult(output=message.get("content", ""), raw={"messages": transcript})
            messages.append(message)
            for call in calls:
                fn = call.get("function", {})
                arguments = fn.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments or "{}")
                result = tool_executor(fn.get("name", ""), arguments)
                messages.append({"role": "tool", "content": str(result)})
        raise RuntimeError("Ollama tool-calling loop exceeded max_tool_rounds")
