from holly.adapters.base import RuntimeResult
from holly.engine import run_task
from holly.models import Agent, Runtime, RuntimeType, Task, TaskStatus, Workspace


def _make_agent(session, runtime_type=RuntimeType.OLLAMA):
    ws = Workspace(name="ws")
    session.add(ws)
    session.commit()

    rt = Runtime(workspace_id=ws.id, name="rt", type=runtime_type, model="some-model", config_json="{}")
    session.add(rt)
    session.commit()

    agent = Agent(workspace_id=ws.id, runtime_id=rt.id, name="agent", instructions="be terse")
    session.add(agent)
    session.commit()
    return agent


def test_run_task_success_persists_output(session, mocker):
    agent = _make_agent(session)
    task = Task(agent_id=agent.id, prompt="hi")
    session.add(task)
    session.commit()

    mocker.patch(
        "holly.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="pong"),
    )

    result = run_task(session, task)

    assert result.status == TaskStatus.COMPLETED
    assert result.output == "pong"
    assert result.error is None
    assert result.started_at is not None
    assert result.finished_at is not None


def test_run_task_failure_persists_error(session, mocker):
    agent = _make_agent(session)
    task = Task(agent_id=agent.id, prompt="hi")
    session.add(task)
    session.commit()

    mocker.patch(
        "holly.adapters.ollama.OllamaRuntime.run",
        side_effect=RuntimeError("boom"),
    )

    result = run_task(session, task)

    assert result.status == TaskStatus.FAILED
    assert result.output is None
    assert result.error == "boom"


def test_run_task_starts_as_pending_then_moves_through_states(session, mocker):
    agent = _make_agent(session)
    task = Task(agent_id=agent.id, prompt="hi")
    session.add(task)
    session.commit()
    assert task.status == TaskStatus.PENDING

    mocker.patch(
        "holly.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="ok"),
    )
    run_task(session, task)
    assert task.status == TaskStatus.COMPLETED
