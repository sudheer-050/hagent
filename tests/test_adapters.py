import httpx
import pytest

from holly.adapters.claude import ClaudeRuntime
from holly.adapters.ollama import OllamaRuntime
from holly.adapters.openai import OpenAIRuntime


class FakeContentBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)]

    def model_dump(self):
        return {"content": [{"type": "text", "text": self.content[0].text}]}


def test_claude_runtime_returns_text(mocker):
    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = FakeMessage("hello from claude")
    mocker.patch("anthropic.Anthropic", return_value=fake_client)

    rt = ClaudeRuntime(model="claude-3-5-sonnet-latest", config={"api_key": "test-key"})
    result = rt.run("hi", context="be terse")

    assert result.output == "hello from claude"
    fake_client.messages.create.assert_called_once()


def test_claude_runtime_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rt = ClaudeRuntime(model="claude-3-5-sonnet-latest", config={})
    with pytest.raises(RuntimeError, match="No Anthropic API key"):
        rt.run("hi")


def test_ollama_runtime_returns_response(mocker):
    fake_response = mocker.Mock()
    fake_response.json.return_value = {"response": "hello from ollama"}
    fake_response.raise_for_status.return_value = None
    mocker.patch("httpx.post", return_value=fake_response)

    rt = OllamaRuntime(model="qwen3-fast8b", config={})
    result = rt.run("hi")

    assert result.output == "hello from ollama"


def test_ollama_runtime_http_error_raises(mocker):
    mocker.patch("httpx.post", side_effect=httpx.ConnectError("connection refused"))

    rt = OllamaRuntime(model="qwen3-fast8b", config={})
    with pytest.raises(RuntimeError, match="Ollama call failed"):
        rt.run("hi")


def test_openai_runtime_returns_text(mocker):
    fake_response = mocker.Mock()
    fake_response.json.return_value = {"choices": [{"message": {"content": "hello from openai"}}]}
    fake_response.raise_for_status.return_value = None
    mocker.patch("httpx.post", return_value=fake_response)

    rt = OpenAIRuntime(model="gpt-4o", config={"api_key": "test-key"})
    result = rt.run("hi")

    assert result.output == "hello from openai"


def test_openai_runtime_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rt = OpenAIRuntime(model="gpt-4o", config={})
    with pytest.raises(RuntimeError, match="No OpenAI API key"):
        rt.run("hi")
