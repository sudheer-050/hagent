"""Google Gemini API runtime adapter."""

import os

import httpx

from hagent.adapters.base import BaseRuntime, RuntimeResult


DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None) -> RuntimeResult:
        api_key = self.config.get("api_key") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("No Gemini API key configured (set GEMINI_API_KEY)")

        base_url = self.config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        transcript = []

        for _ in range(self.config.get("max_tool_rounds", 8)):
            payload = {"contents": contents}
            if context:
                payload["systemInstruction"] = {"parts": [{"text": context}]}
            if tools:
                declarations = [tool["function"] for tool in tools if tool.get("type") == "function"]
                if declarations:
                    payload["tools"] = [{"functionDeclarations": declarations}]

            try:
                response = httpx.post(
                    f"{base_url}/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json=payload,
                    timeout=self.config.get("timeout", 120),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Gemini API call failed: {exc}") from exc

            data = response.json()
            transcript.append(data)
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError("Gemini API returned no candidates")
            model_content = candidates[0].get("content") or {}
            parts = model_content.get("parts") or []
            calls = [part["functionCall"] for part in parts if part.get("functionCall")]
            if not calls or not tool_executor:
                output = "".join(part.get("text", "") for part in parts)
                return RuntimeResult(output=output, raw={"messages": transcript})

            contents.append(model_content)
            responses = []
            for call in calls:
                name = call.get("name", "")
                result = tool_executor(name, call.get("args") or {})
                responses.append(
                    {"functionResponse": {"name": name, "response": {"result": str(result)}}}
                )
            contents.append({"role": "user", "parts": responses})

        raise RuntimeError("Gemini tool-calling loop exceeded max_tool_rounds")
