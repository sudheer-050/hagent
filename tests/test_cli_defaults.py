import json
from types import SimpleNamespace

import pytest

from hagent import engine
from hagent.cli_defaults import default_settings, explicit_model_request
from hagent.models import Agent, Runtime, RuntimeType, Workspace


@pytest.mark.parametrize('prompt,expected', [
    ('Use opus for this task', ('claude_code','opus')),
    ('Please use Claude Sonnet 5.5 to review this', ('claude_code','claude-sonnet-5-5')),
    ('Can you use Codex model gpt-6.1-sol?', ('codex_cli','gpt-6.1-sol')),
    ('/model gpt-6.1-sol', ('codex_cli','gpt-6.1-sol')),
    ('Switch to gemini-3-pro', ('gemini_cli','gemini-3-pro')),
    ('Use gpt-6.1-sol.', ('codex_cli','gpt-6.1-sol')),
    ('Do not use opus', None),
    ('Explain how to use opus', None),
    ('The README says: use gpt-6.1-sol', None),
    ('"Use opus" is an example', None),
])
def test_only_explicit_model_instructions_change_routing(prompt,expected):
    assert explicit_model_request(prompt)==expected


def seed(session):
    workspace=Workspace(name='CLI defaults')
    session.add(workspace); session.flush()
    runtimes=[]
    for typ in (RuntimeType.CLAUDE_CODE,RuntimeType.CODEX_CLI):
        runtime=Runtime(workspace_id=workspace.id,name=typ.value,type=typ,model='default',config_json=json.dumps(default_settings()))
        session.add(runtime); runtimes.append(runtime)
    session.flush()
    agent=Agent(workspace_id=workspace.id,name='Test agent',runtime_id=runtimes[0].id,backup_runtime_id=runtimes[1].id)
    session.add(agent); session.commit()
    return agent,runtimes


def test_cli_defaults_route_without_model_or_effort_override(session):
    agent,runtimes=seed(session)
    primary,route=engine._route_primary_runtime(session,agent,'Review this file',{'mode':'manual','runtime_id':agent.runtime_id},None,(),None)
    assert primary.id==runtimes[0].id
    assert route['model']=='default'
    assert route['provider_effort']==''
    config=engine._build_adapter_config(agent,primary,None,None,{'effort':'high'})
    try:
        assert not config.get('reasoning_effort')
    finally:
        engine._remove_sandbox(config)


def test_explicit_model_switch_selects_matching_cli_without_silent_fallback(session):
    agent,runtimes=seed(session)
    primary,route=engine._route_primary_runtime(session,agent,'Use gpt-6.1-sol for this task',None,None,(),None)
    assert primary.id==runtimes[1].id
    assert route['model']=='gpt-6.1-sol'
    assert route['model_switch_runtime'] is True
    assert engine._resolve_backup_and_failback(session,agent,runtimes[0],route,None)==(None,None)
    assert runtimes[0].model=='default' and runtimes[1].model=='default'


@pytest.mark.parametrize('provider,module,find,output', [
    ('claude_code','hagent.adapters.claude_code','shutil.which',json.dumps({'result':'done'})),
    ('codex_cli','hagent.adapters.codex_cli','_find_codex','done'),
    ('gemini_cli','hagent.adapters.gemini_cli','shutil.which',json.dumps({'response':'done'})),
])
def test_adapters_omit_model_flag_for_cli_default(mocker,provider,module,find,output):
    from hagent.adapters import get_runtime_class
    mocker.patch(module+'.'+find,return_value='assistant.exe')
    call=mocker.patch(module+'.run_managed_process',return_value=SimpleNamespace(returncode=0,stdout=output,stderr=''))
    adapter=get_runtime_class(provider)('default',{})
    adapter.run('hello')
    args=call.call_args.args[0]
    assert '--model' not in args and '--effort' not in args
    assert not any('model_reasoning_effort' in item for item in args)


def test_chat_model_switch_does_not_resume_other_cli_session(session,mocker):
    from hagent.adapters.base import RuntimeResult
    agent,runtimes=seed(session)
    mocker.patch.object(engine,'_start_memory_session',return_value=(None,None,False,'Use gpt-6.1-sol'))
    run=mocker.patch.object(engine,'_run_with_failover',return_value=RuntimeResult(output='done'))
    engine.execute_agent(agent,'Use gpt-6.1-sol',resume_session_id='claude-session',routing_override={'mode':'manual','runtime_id':agent.runtime_id})
    assert run.call_args.args[12] is None
    assert run.call_args.args[13]['model']=='gpt-6.1-sol'
