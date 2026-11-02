from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hagent import db, web
from hagent.models import Agent, Runtime, RuntimeType, Workspace
from hagent.runtime_connections import consolidate, create_or_reuse, identity


def test_cli_repeat_add_returns_original_and_marks_connected(cli_db,mocker):
    mocker.patch('hagent.runtime_discovery.cli_command',return_value='claude.exe')
    client=TestClient(web.app)
    first=client.post('/runtimes',data={'type':'claude','connection':'cli'},follow_redirects=False)
    assert first.status_code==303
    second=client.post('/runtimes',data={'type':'claude','connection':'cli','name':'Another name'},follow_redirects=False)
    assert second.status_code==303 and 'already_connected' in second.headers['location']
    with db.get_session() as s:
        assert len(s.scalars(select(Runtime).where(Runtime.archived.is_(False))).all())==1
    inventory=client.get('/api/runtime-connections').json()['installed']
    assert next(row for row in inventory if row['provider']=='claude')['connected'] is True


def test_api_models_and_keys_do_not_create_extra_connections(cli_db):
    client=TestClient(web.app)
    for model,key in [('first-model','first-key'),('second-model','different-key')]:
        assert client.post('/runtimes',data={'type':'openai','model':model,'api_key':key},follow_redirects=False).status_code==303
    with db.get_session() as s:
        rows=s.scalars(select(Runtime)).all()
        assert len(rows)==1
        assert rows[0].model=='first-model'
        assert json.loads(rows[0].config_json)['api_key']=='first-key'


def test_same_provider_api_and_cli_are_separate_connections(cli_db,mocker):
    mocker.patch('hagent.runtime_discovery.cli_command',return_value='codex.exe')
    client=TestClient(web.app)
    assert client.post('/runtimes',data={'type':'openai','connection':'cli'},follow_redirects=False).status_code==303
    assert client.post('/runtimes',data={'type':'openai','model':'api-model','api_key':'key'},follow_redirects=False).status_code==303
    with db.get_session() as s:
        assert len(s.scalars(select(Runtime)).all())==2


def test_identity_normalizes_url_and_ignores_model_settings():
    assert identity('ollama',{'base_url':'http://LOCALHOST:11434/'})==identity('ollama',{'base_url':'http://127.0.0.1:11434'})
    assert identity('openai',{})!=identity('claude',{})
    assert identity('openai_compatible',{'base_url':'https://one.example/v1'})!=identity('openai_compatible',{'base_url':'https://two.example/v1'})


def test_parallel_adds_reuse_one_connection_per_workspace(cli_db):
    with db.get_session() as s:
        workspace=db.get_active_workspace(s);s.commit();ident=workspace.id
    def add(number):
        with db.get_session(workspace_id=ident) as s:
            row,created=create_or_reuse(s,ident,'Codex '+str(number),'codex_cli','default',{})
            s.commit()
            return row.id
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(add,range(8))))==1
    with db.get_session(scoped=False) as s:
        second=Workspace(name='Other');s.add(second);s.flush()
        row,_=create_or_reuse(s,second.id,'Codex','codex_cli','default',{});s.commit()
        assert row.workspace_id==second.id
        assert len(s.scalars(select(Runtime)).all())==2


def test_existing_duplicates_keep_original_and_reassign_agents(cli_db):
    with db.get_session() as s:
        workspace=db.get_active_workspace(s)
        original=Runtime(workspace_id=workspace.id,name='Original',type=RuntimeType.CLAUDE_CODE,model='default')
        duplicate=Runtime(workspace_id=workspace.id,name='Duplicate',type=RuntimeType.CLAUDE_CODE,model='opus')
        s.add(original);s.flush();s.add(duplicate);s.flush()
        agent=Agent(workspace_id=workspace.id,name='Agent',runtime_id=duplicate.id,backup_runtime_id=original.id)
        s.add(agent);s.commit()
        replacements=consolidate(s,workspace.id);s.commit()
        assert replacements=={duplicate.id:original.id}
        assert duplicate.archived and not original.archived
        assert agent.runtime_id==original.id and agent.backup_runtime_id is None
        assert s.get(Runtime,duplicate.id) is not None


def test_edit_cannot_turn_a_connection_into_an_existing_one(cli_db,mocker):
    mocker.patch('hagent.runtime_discovery.cli_command',return_value='assistant.exe')
    client=TestClient(web.app)
    for provider in ('claude','openai'):
        assert client.post('/runtimes',data={'type':provider,'connection':'cli'},follow_redirects=False).status_code==303
    with db.get_session() as s:
        codex=s.scalar(select(Runtime).where(Runtime.type==RuntimeType.CODEX_CLI));ident=codex.id
    assert client.post('/runtimes/'+ident,data={'type':'claude','connection':'cli'},follow_redirects=False).status_code==409


def test_cli_command_reuses_existing_runtime(cli_db):
    from click.testing import CliRunner
    from hagent.cli import cli
    runner=CliRunner()
    first=runner.invoke(cli,['runtime','create','--name','First','--type','codex_cli','--model','default'])
    second=runner.invoke(cli,['runtime','create','--name','Second','--type','codex_cli','--model','other-model'])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert 'Already connected' in second.output
    with db.get_session() as s:
        assert len(s.scalars(select(Runtime)).all()) == 1
