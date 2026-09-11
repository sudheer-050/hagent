import json

from click.testing import CliRunner

from hagent.adapters.base import RuntimeResult
from hagent.adapters.ollama import OllamaRuntime
from hagent.cli import cli
from hagent.engine import run_issue
from hagent.models import Agent, Issue, Project, Runtime, RuntimeType, Workspace


def test_multica_compatible_cli_groups_are_exposed():
    result = CliRunner().invoke(cli, ["agent", "--help"])
    assert result.exit_code == 0
    for command in ("archive", "copy", "env", "restore", "tasks", "update"):
        assert command in result.output

    result = CliRunner().invoke(cli, ["issue", "--help"])
    assert result.exit_code == 0
    for command in ("runs", "run-messages", "search", "timeline", "usage"):
        assert command in result.output


def test_ollama_tool_loop_executes_and_returns_final_text(mocker):
    first = mocker.Mock()
    first.raise_for_status.return_value = None
    first.json.return_value = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "clock__get_time", "arguments": {"zone": "UTC"}}}
            ],
        }
    }
    second = mocker.Mock()
    second.raise_for_status.return_value = None
    second.json.return_value = {
        "message": {"role": "assistant", "content": "The time is ready."}
    }
    mocker.patch("httpx.post", side_effect=[first, second])
    calls = []

    runtime = OllamaRuntime(model="qwen3", config={})
    result = runtime.run(
        "What time is it?",
        tools=[{"type": "function", "function": {"name": "clock__get_time"}}],
        tool_executor=lambda name, arguments: calls.append((name, arguments)) or "12:00 UTC",
    )

    assert result.output == "The time is ready."
    assert calls == [("clock__get_time", {"zone": "UTC"})]
    assert len(result.raw["messages"]) == 2


def test_run_issue_persists_raw_transcript(session, mocker):
    workspace = Workspace(name="ws")
    session.add(workspace)
    session.commit()
    runtime = Runtime(
        workspace_id=workspace.id,
        name="ollama",
        type=RuntimeType.OLLAMA,
        model="qwen3",
        config_json="{}",
    )
    session.add(runtime)
    session.flush()
    agent = Agent(workspace_id=workspace.id, runtime_id=runtime.id, name="agent")
    project = Project(workspace_id=workspace.id, name="project")
    session.add_all([agent, project])
    session.commit()
    issue = Issue(project_id=project.id, title="test", description="run this")
    session.add(issue)
    session.commit()

    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="done", raw={"messages": [{"content": "done"}]}),
    )

    run = run_issue(session, issue, agent)

    assert json.loads(run.transcript_json)["messages"][0]["content"] == "done"



