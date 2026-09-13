import json
from types import SimpleNamespace

from hagent.adapters import get_runtime_class
from hagent.adapters.claude_code import ClaudeCodeRuntime
from hagent.adapters.codex_cli import CodexCliRuntime
from hagent.adapters.gemini import GeminiRuntime
from hagent.adapters.gemini_cli import GeminiCliRuntime
from hagent.adapters.openai import OpenAIRuntime
from hagent.models import RuntimeType
from hagent.runtime_catalog import catalog_for, local_fit, provider_config
from hagent.web import runtime_models


def test_provider_factory_supports_new_runtimes():
    assert get_runtime_class(RuntimeType.GEMINI) is GeminiRuntime
    assert get_runtime_class(RuntimeType.GEMINI_CLI) is GeminiCliRuntime
    assert get_runtime_class(RuntimeType.CODEX_CLI) is CodexCliRuntime
    assert get_runtime_class(RuntimeType.CLAUDE_CODE) is ClaudeCodeRuntime
    assert get_runtime_class(RuntimeType.OPENAI_COMPATIBLE) is OpenAIRuntime


def test_gemini_api_returns_text(mocker):
    response = mocker.Mock()
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}]
    }
    response.raise_for_status.return_value = None
    post = mocker.patch("hagent.adapters.gemini.httpx.post", return_value=response)

    result = GeminiRuntime("gemini-3.8-flash", {"api_key": "secret"}).run("Hello")

    assert result.output == "hello from gemini"
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "secret"}


def test_gemini_cli_uses_headless_json_mode(mocker):
    mocker.patch("hagent.adapters.gemini_cli.shutil.which", return_value="gemini")
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"response": "hello from cli", "stats": {}}),
        stderr="",
    )
    run = mocker.patch("hagent.adapters.gemini_cli.subprocess.run", return_value=completed)

    result = GeminiCliRuntime("gemini-3.8-flash", {}).run("Hello")

    assert result.output == "hello from cli"
    args = run.call_args.args[0]
    assert args[:5] == ["gemini", "--model", "gemini-3.8-flash", "--output-format", "json"]
    assert "--yolo" not in args


def test_codex_cli_is_ephemeral_and_read_only(mocker):
    mocker.patch("hagent.adapters.codex_cli._find_codex", return_value="codex")
    completed = SimpleNamespace(returncode=0, stdout="review complete", stderr="")
    run = mocker.patch("hagent.adapters.codex_cli.subprocess.run", return_value=completed)

    result = CodexCliRuntime("default", {}).run("Review this")

    assert result.output == "review complete"
    args = run.call_args.args[0]
    assert args[:7] == ["codex", "exec", "--ephemeral", "--sandbox", "read-only", "--color", "never"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in args


def test_claude_code_uses_print_json_mode(mocker):
    mocker.patch("hagent.adapters.claude_code.shutil.which", return_value="claude")
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"result": "done", "session_id": "abc"}),
        stderr="",
    )
    run = mocker.patch("hagent.adapters.claude_code.subprocess.run", return_value=completed)

    result = ClaudeCodeRuntime("sonnet", {}).run("Review this")

    assert result.output == "done"
    args = run.call_args.args[0]
    assert args[:2] == ["claude", "--print"]
    assert "--output-format" in args
    assert "--dangerously-skip-permissions" not in args


def test_runtime_catalog_includes_descriptions_and_grok():
    gemini = runtime_models("gemini")
    grok = catalog_for("xai")

    assert any(item["id"] == "gemini-3.8-flash" for item in gemini["model_details"])
    assert gemini["provider_info"]["best_for"]
    assert provider_config("xai")["base_url"] == "https://api.x.ai/v1"
    assert any(item["id"] == "grok-4.6" for item in grok["model_details"])


def test_hardware_fit_discourages_oversized_local_models():
    hardware = {"vram_gb": 8.0, "ram_gb": 32.0}

    assert local_fit(8, hardware)["level"] == "good"
    assert local_fit(14, hardware)["level"] == "tight"
    assert local_fit(32, hardware)["level"] == "poor"

def test_cli_runtimes_only_bypass_permissions_when_agent_terminal_is_enabled(mocker, tmp_path):
    mocker.patch("hagent.adapters.codex_cli._find_codex", return_value="codex")
    codex_run = mocker.patch(
        "hagent.adapters.codex_cli.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="done", stderr=""),
    )
    CodexCliRuntime("default", {"terminal_enabled": True, "working_directory": str(tmp_path)}).run("work")
    assert "--dangerously-bypass-approvals-and-sandbox" in codex_run.call_args.args[0]
    assert codex_run.call_args.kwargs["cwd"] == str(tmp_path)

    mocker.patch("hagent.adapters.gemini_cli.shutil.which", return_value="gemini")
    gemini_run = mocker.patch(
        "hagent.adapters.gemini_cli.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"response": "done"}), stderr=""),
    )
    GeminiCliRuntime("gemini", {"terminal_enabled": True, "working_directory": str(tmp_path)}).run("work")
    assert "--yolo" in gemini_run.call_args.args[0]
    assert gemini_run.call_args.kwargs["cwd"] == str(tmp_path)

    mocker.patch("hagent.adapters.claude_code.shutil.which", return_value="claude")
    claude_run = mocker.patch(
        "hagent.adapters.claude_code.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"result": "done"}), stderr=""),
    )
    ClaudeCodeRuntime("default", {"terminal_enabled": True, "working_directory": str(tmp_path)}).run("work")
    assert "--dangerously-skip-permissions" in claude_run.call_args.args[0]
    assert claude_run.call_args.kwargs["cwd"] == str(tmp_path)