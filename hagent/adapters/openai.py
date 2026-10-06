"""OpenAI runtime adapter — HTTP call to the Chat Completions API.

Stub for Phase 1: implemented but not wired to a default key. Set OPENAI_API_KEY
or pass config={"api_key": ...} on the Runtime to use it.
"""

import base64
import os
import json

import httpx

from hagent.adapters.base import BaseRuntime, RuntimeResult

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Image generation/editing is a different API (multipart /images/edits, not chat
# completions) with its own model family - detect it by model id rather than by
# whether images were passed, since a non-image model can't be redirected there.
IMAGE_MODELS = {"gpt-image-1", "gpt-image-1-mini"}


class OpenAIRuntime(BaseRuntime):
    def run(self, prompt: str, context: str = "", tools=None, tool_executor=None, images=None) -> RuntimeResult:
        env_name = self.config.get("api_key_env", "OPENAI_API_KEY")
        api_key = self.config.get("api_key") or os.environ.get(env_name)
        if not api_key and not self.config.get("allow_no_key"):
            label = "OpenAI API key" if env_name == "OPENAI_API_KEY" else "API key"
            raise RuntimeError(f"No {label} configured (set {env_name})")

        base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        if self.model in IMAGE_MODELS:
            return self._edit_image(prompt, images, api_key, base_url)

        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        transcript = []
        for _ in range(self.config.get("max_tool_rounds", 8)):
            payload = {"model": self.model, "messages": messages}
            if self.config.get("max_tokens"):
                payload["max_tokens"] = self.config["max_tokens"]
            effort_parameter = self.config.get("effort_parameter")
            if effort_parameter and self.config.get("reasoning_effort"):
                payload[effort_parameter] = self.config["reasoning_effort"]
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

    def _edit_image(self, prompt: str, images: list[dict] | None, api_key: str, base_url: str) -> RuntimeResult:
        if not images:
            raise RuntimeError(f"{self.model} edits an existing image - no image was attached to this turn")
        files = [
            ("image[]", (f"image{i}.png", image["data"], image["mime_type"]))
            for i, image in enumerate(images)
        ]
        try:
            response = httpx.post(
                f"{base_url}/images/edits",
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": self.model, "prompt": prompt},
                files=files,
                timeout=self.config.get("timeout", 120),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI image edit call failed: {exc}") from exc
        data = response.json()
        output_images = [
            {"mime_type": "image/png", "data": base64.b64decode(item["b64_json"])}
            for item in data.get("data", [])
            if item.get("b64_json")
        ]
        if not output_images:
            raise RuntimeError("OpenAI image edit returned no image data")
        return RuntimeResult(output="Edited the image as requested.", raw=data, images=output_images)
