from types import SimpleNamespace

import pytest

from hagent import engine
from hagent.adapters.base import RuntimeResult
from hagent.models import RuntimeType


@pytest.mark.parametrize('runtime_type,resume,includes_history', [
    (RuntimeType.CLAUDE_CODE, 'existing', False),
    (RuntimeType.CODEX_CLI, 'existing', False),
    (RuntimeType.OPENCODE_CLI, 'existing', False),
    (RuntimeType.CODEX_CLI, None, True),
    (RuntimeType.OPENAI, 'existing', True),
    (RuntimeType.GEMINI_CLI, 'existing', True),
])
def test_only_resumed_stateful_clis_omit_duplicate_history(mocker, runtime_type, resume, includes_history):
    agent = SimpleNamespace(name='Test', instructions='Instructions', skills=[])
    runtime = SimpleNamespace(type=runtime_type)
    mocker.patch.object(engine, '_build_adapter_config', return_value={'resume_session_id': resume})
    adapter = SimpleNamespace(run=mocker.Mock(return_value=RuntimeResult(output='OK')))
    mocker.patch.object(engine, '_build_adapter', return_value=adapter)
    mocker.patch.object(engine, '_build_agent_tools', return_value=([], {}, {}, {}))
    engine._execute_with_runtime(agent, runtime, 'New request', None, None,
        resume_session_id=resume, history=[{'role': 'user', 'content': 'Earlier message'}])
    assert ('Earlier message' in adapter.run.call_args.kwargs['context']) == includes_history
    assert adapter.run.call_args.kwargs['prompt'] == 'New request'


def test_missing_cli_history_retries_once_with_retained_chat_context(mocker):
    from hagent.adapters.base import CLIHistoryMissing
    config = {'resume_session_id': 'missing'}
    agent = SimpleNamespace(name='Holly', instructions='Instructions', skills=[])
    runtime = SimpleNamespace(type=RuntimeType.CODEX_CLI)
    mocker.patch.object(engine, '_build_adapter_config', return_value=config)
    adapter = SimpleNamespace(run=mocker.Mock(side_effect=[CLIHistoryMissing('thread not found'), RuntimeResult(output='Hi')]))
    mocker.patch.object(engine, '_build_adapter', return_value=adapter)
    mocker.patch.object(engine, '_build_agent_tools', return_value=([], {}, {}, {}))
    result = engine._execute_with_runtime(agent, runtime, 'hi', None, None, resume_session_id='missing',
        history=[{'role': 'user', 'content': 'Earlier context'}])
    assert result.output == 'Hi' and adapter.run.call_count == 2
    assert 'resume_session_id' not in config
    assert 'Earlier context' not in adapter.run.call_args_list[0].kwargs['context']
    assert 'Earlier context' in adapter.run.call_args_list[1].kwargs['context']
