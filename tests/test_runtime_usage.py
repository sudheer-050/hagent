from hagent.adapters.base import RuntimeResult
from hagent.engine import run_issue
from hagent.models import Agent, Issue, Project, Runtime, RuntimeType, Workspace


def test_runtime_result_aggregates_provider_usage_from_tool_turns():
    result = RuntimeResult(output='done', raw={'messages': [
        {'usage': {'prompt_tokens': 14, 'completion_tokens': 5}},
        {'usage': {'prompt_tokens': 8, 'completion_tokens': 3}},
    ]})

    assert (result.input_tokens, result.output_tokens) == (22, 8)


def test_runtime_result_normalizes_provider_usage_shapes():
    ollama = RuntimeResult(output='done', raw={'prompt_eval_count': 12, 'eval_count': 6})
    gemini = RuntimeResult(output='done', raw={'usageMetadata': {'promptTokenCount': 19, 'candidatesTokenCount': 7}})
    claude = RuntimeResult(output='done', raw={'usage': {'input_tokens': 21, 'output_tokens': 9}})
    compatible = RuntimeResult(output='done', raw={'usage': {
        'prompt_tokens': 11, 'completion_tokens': 4, 'input_tokens': 11, 'output_tokens': 4,
    }})

    assert (ollama.input_tokens, ollama.output_tokens) == (12, 6)
    assert (gemini.input_tokens, gemini.output_tokens) == (19, 7)
    assert (claude.input_tokens, claude.output_tokens) == (21, 9)
    assert (compatible.input_tokens, compatible.output_tokens) == (11, 4)


def test_run_persists_provider_reported_token_usage(session, mocker):
    workspace = Workspace(name='usage-workspace')
    session.add(workspace)
    session.flush()
    project = Project(workspace_id=workspace.id, name='usage-project')
    runtime = Runtime(workspace_id=workspace.id, name='usage-runtime', type=RuntimeType.OLLAMA, model='local')
    session.add_all([project, runtime])
    session.flush()
    agent = Agent(workspace_id=workspace.id, runtime_id=runtime.id, name='usage-agent')
    issue = Issue(project_id=project.id, title='Check usage', description='Run task')
    session.add_all([agent, issue])
    session.commit()

    mocker.patch(
        'hagent.adapters.ollama.OllamaRuntime.run',
        return_value=RuntimeResult(output='answer', raw={'prompt_eval_count': 15, 'eval_count': 4}),
    )
    run = run_issue(session, issue, agent)

    assert (run.input_tokens, run.output_tokens) == (15, 4)
