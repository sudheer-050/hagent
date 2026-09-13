"""OpenAI runtime adapter — HTTP call to the Chat Completions API.

Stub for Phase 1: implemented but not wired to a default key. Set OPENAI_API_KEY
or pass config={"api_key": ...} on the Runtime to use it.
"""

import os
import json

import httpx

from hagent.adapters.base import BaseRuntime, RuntimeResult

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        env_name = self.config.get("api_key_env", "OPENAI_API_KEY")
        api_key = self.config.get("api_key") or os.environ.get(env_name)
        if not api_key and not self.config.get("allow_no_key"):
            label = "OpenAI API key" if env_name == "OPENAI_API_KEY" else "API key"
            raise RuntimeError(f"No {label} configured (set {env_name})")

        base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        transcript = []
        for _ in range(self.config.get("max_tool_rounds", 8)):
            payload = {"model": self.model, "messages": messages}
            if tools:
                payload["tools"] = tools
            try:
                response = httpx.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                    json=payload,
                    timeout=self.config.get("timeout", 60),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Model provider API call failed: {exc}") from exc
            data = response.json()
            transcript.append(data)
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls or not tool_executor:
                return RuntimeResult(output=message.get("content") or "", raw={"messages": transcript} if tools else data)
            messages.append(message)
            for call in calls:
                arguments = json.loads(call["function"].get("arguments") or "{}")
                result = tool_executor(call["function"]["name"], arguments)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})
        raise RuntimeError("OpenAI tool-calling loop exceeded max_tool_rounds")
