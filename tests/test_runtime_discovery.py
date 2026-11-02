import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hagent import db, web, runtime_discovery as discovery
from hagent.models import Runtime


def response(data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request('GET','https://provider.example/models'))


@pytest.mark.parametrize('provider,pages,param', [
    ('claude', [{'data':[{'id':'first'}], 'has_more':True, 'last_id':'first'}, {'data':[{'id':'second'}], 'has_more':False}], 'after_id'),
    ('gemini', [{'models':[{'name':'models/first'}], 'nextPageToken':'next'}, {'models':[{'name':'models/second'}]}], 'pageToken'),
    ('cohere', [{'models':[{'name':'first'}], 'next_page_token':'next'}, {'models':[{'name':'second'}]}], 'page_token'),
])
def test_discovery_follows_all_pages_and_keeps_key_out_of_url(mocker, provider, pages, param):
    calls=[]
    def get(client, url, **kwargs):
        calls.append((url,dict(kwargs)))
        return response(pages[len(calls)-1])
    mocker.patch('httpx.Client.get', autospec=True, side_effect=get)
    rows=discovery.api_models(provider,'private-discovery-key')
    assert [r['id'] for r in rows] == ['first','second']
    assert param in calls[1][1]['params']
    assert all('private-discovery-key' not in url for url,_ in calls)


def test_together_array_and_openrouter_efforts(mocker):
    call=mocker.patch('httpx.Client.get',return_value=response([{'id':'array-model'}]))
    assert discovery.api_models('together','key')[0]['id']=='array-model'
    call.return_value=response({'data':[{'id':'live-model','reasoning':{'supported_efforts':['high','low']}}]})
    row=discovery.api_models('openrouter','key')[0]
    assert call.call_args.args[0]=='https://openrouter.ai/api/v1/models/user'
    assert row['efforts']==['low','high']


def test_key_discovery_save_generated_name_and_effort(cli_db,mocker):
    mocker.patch('httpx.Client.get',return_value=response({'data':[{'id':'live-model','reasoning':{'supported_efforts':['high','low']}}]}))
    client=TestClient(web.app)
    catalog=client.post('/api/runtime-models',json={'provider':'openrouter','connection':'api','api_key':'test-secret'})
    assert catalog.status_code==200, catalog.text
    assert catalog.json()['models'][0]['id']=='live-model'
    assert 'test-secret' not in catalog.text
    payload={'type':'openrouter','connection':'api','api_key':'test-secret','model':'live-model','effort':'high','catalog_token':catalog.json()['catalog_token']}
    # Stale discovery may not be used to save a different key, model, or effort.
    for field,value in [('api_key','changed-key'),('model','not-in-provider'),('effort','max')]:
        assert client.post('/runtimes',data=dict(payload,**{field:value}),follow_redirects=False).status_code==400
    assert client.post('/runtimes',data=payload,follow_redirects=False).status_code==303
    with db.get_session() as s:
        row=s.scalar(select(Runtime))
        assert row.name=='OpenRouter - live-model'
        config=json.loads(row.config_json)
        assert config['effort_selection']=='high'
        assert config['reasoning_effort']=='high'
        assert config['effort_parameter']=='reasoning'


def test_cli_options_and_live_models_are_returned(cli_db,mocker):
    mocker.patch.object(discovery,'cli_command',return_value='claude.exe')
    mocker.patch.object(discovery,'cli_models',return_value=[{'id':'actual-cli-model','label':'Actual','efforts':['low','high']}])
    client=TestClient(web.app)
    assert client.get('/api/runtime-connections?provider=claude').json()['cli_available']
    result=client.post('/api/runtime-models',json={'provider':'claude','connection':'cli'})
    assert result.status_code==200
    assert result.json()['models'][0]['id']=='actual-cli-model'
    saved=client.post('/runtimes',data={'type':'claude','connection':'cli','model':'actual-cli-model','effort':'low','catalog_token':result.json()['catalog_token']},follow_redirects=False)
    assert saved.status_code==303
    with db.get_session() as s:
        runtime=s.scalar(select(Runtime))
        assert runtime.type.value=='claude_code'
        assert runtime.model == 'default'
        assert json.loads(runtime.config_json)['use_cli_defaults'] is True
        assert not json.loads(runtime.config_json)['reasoning_effort']


def test_catalog_is_bound_to_workspace_and_owner():
    token=discovery.remember_catalog(('workspace-a','owner-a'),'fingerprint','openai','api',[{'id':'m','efforts':['high']}])
    for scope in [('workspace-b','owner-a'),('workspace-a','owner-b')]:
        with pytest.raises(ValueError,match='Load models again'):
            discovery.selected_settings(token,scope,'fingerprint','m','high')


def test_discovery_errors_do_not_return_provider_body_or_key(cli_db,mocker):
    mocker.patch('httpx.Client.get',return_value=response({'error':'secret-key-debug-body'},401))
    result=TestClient(web.app).post('/api/runtime-models',json={'provider':'openai','connection':'api','api_key':'secret-key-debug-body'})
    assert result.status_code==400
    assert result.json()['detail']=='API key rejected.'
    assert 'secret-key-debug-body' not in result.text


def test_openrouter_effort_is_sent_as_nested_reasoning(mocker):
    from hagent.adapters.openai import OpenAIRuntime
    call=mocker.patch('httpx.post',return_value=response({'choices':[{'message':{'content':'done'}}]}))
    result=OpenAIRuntime('model',{'api_key':'key','reasoning_effort':'high','effort_parameter':'reasoning'}).run('hello')
    assert result.output=='done'
    assert call.call_args.kwargs['json']['reasoning']=={'effort':'high'}


def test_major_providers_are_available_and_name_is_not_required(cli_db):
    from hagent.runtime_catalog import provider_options
    ids={p['id'] for p in provider_options()}
    assert {'openai','claude','gemini','openrouter','xai','deepseek','mistral','groq','together','fireworks','cerebras','cohere','ollama'} <= ids
    text=TestClient(web.app).get('/runtimes').text
    assert 'for="runtime-name"' not in text
    assert 'runtime-connection' in text and 'runtime-effort' in text


def test_cli_inventory_only_lists_installed_assistants(cli_db, mocker):
    mocker.patch.object(discovery, 'cli_command', side_effect=lambda provider: 'claude.exe' if provider == 'claude' else None)
    result = TestClient(web.app).get('/api/runtime-connections')
    assert result.status_code == 200
    assert result.json() == {'installed':[{'provider':'claude','label':'Claude','connected':False}]}
    mocker.patch.object(discovery, 'cli_command', return_value=None)
    assert TestClient(web.app).get('/api/runtime-connections').json() == {'installed':[]}


def test_connection_choice_precedes_provider(cli_db):
    text = TestClient(web.app).get('/runtimes').text
    assert text.index('id="runtime-mode"') < text.index('id="runtime-type"')
    assert 'id="runtime-connection" name="connection"' in text


def test_cli_can_be_saved_without_model_discovery_or_key(cli_db, mocker):
    mocker.patch.object(discovery, 'cli_command', return_value='codex.exe')
    listing=mocker.patch.object(discovery, 'cli_models', side_effect=AssertionError('must not discover models'))
    result=TestClient(web.app).post('/runtimes', data={'type':'openai','connection':'cli'}, follow_redirects=False)
    assert result.status_code == 303
    listing.assert_not_called()
    with db.get_session() as session:
        runtime=session.scalar(select(Runtime))
        assert runtime.type.value == 'codex_cli' and runtime.model == 'default'
        assert json.loads(runtime.config_json)['use_cli_defaults'] is True
