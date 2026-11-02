import json
import subprocess
import sys

import psutil
import pytest

from hagent import cli_sessions as sessions


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, '_stopping', False)
    path = tmp_path / 'fake_cli.py'
    path.write_text('''import json,os,sys,time,urllib.request
kind=sys.argv[1]
bridge=None
args=sys.argv[2:]
if kind=='codex_cli':
 for arg in args:
  if arg.startswith('mcp_servers.hagent.args='):
   bridge=json.loads(arg.split('=',1)[1])[1]
else:
 if '--mcp-config' in args:
  with open(args[args.index('--mcp-config')+1]) as f:
   bridge=json.load(f)['mcpServers']['hagent']['args'][1]
sid='initial'
def send(data): print(json.dumps(data),flush=True)
def reply(text):
 if text.endswith('hang'): time.sleep(30)
 result='OK'
 if bridge:
  req=urllib.request.Request(bridge+'/call',data=json.dumps({'name':'echo','arguments':{}}).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req) as r: result=json.load(r)['result']
 return str(os.getpid())+':'+result
for line in sys.stdin:
 m=json.loads(line)
 if kind=='codex_cli':
  method=m.get('method');p=m.get('params',{})
  if method=='initialize': send({'id':m['id'],'result':{}})
  if method in ('thread/start','thread/resume'):
   if p.get('sandbox') not in ('read-only','workspace-write','danger-full-access'):
    send({'id':m['id'],'error':{'code':-32600,'message':'Invalid sandbox value'}});continue
   if method=='thread/resume' and p.get('threadId')=='missing':
    send({'id':m['id'],'error':{'code':-32600,'message':'thread not found: missing'}});continue
   sid=p.get('threadId', 'new-'+str(m['id']))
   send({'id':m['id'],'result':{'thread':{'id':sid}}})
  if method=='turn/start':
   if p['threadId']!=sid:
    send({'id':m['id'],'error':{'code':-32600,'message':'thread not loaded'}});continue
   value=reply(p['input'][0]['text'])
   # Events may arrive before the turn/start acknowledgement.
   send({'method':'item/completed','params':{'threadId':sid,'item':{'type':'agentMessage','text':value}}})
   send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
   send({'method':'turn/completed','params':{'threadId':sid,'turn':{'status':'completed'}}})
 else:
  if m.get('type')=='control_request':
   send({'type':'control_response','response':{'subtype':'success','request_id':m['request_id'],'response':{}}})
  if m.get('type')=='user':
   send({'type':'result','subtype':'success','result':reply(m['message']['content']),'session_id':'claude-session'})
''', encoding='utf-8')
    original = subprocess.Popen
    processes = []
    def launch(args, **kwargs):
        kind = 'codex_cli' if 'app-server' in args else 'claude_code'
        process = original([sys.executable, '-u', str(path), kind, *args[1:]], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr('hagent.runtime_discovery.cli_command', lambda _: sys.executable)
    monkeypatch.setattr(sessions.subprocess, 'Popen', launch)
    yield processes
    sessions.stop()


@pytest.mark.parametrize('kind', ['codex_cli', 'claude_code'])
@pytest.mark.parametrize('terminal_enabled', [False, True])
def test_startup_is_idle_and_next_turn_reuses_process_and_new_tool_handler(fake_cli, kind, terminal_enabled):
    config = {'_persistent_key': 'agent:runtime', 'timeout': 5, 'terminal_enabled': terminal_enabled}
    tools = [{'name': 'echo', 'description': 'echo', 'input_schema': {'type': 'object'}}]
    worker = sessions.acquire(kind, 'default', config, tools)
    with worker.lock:
        worker.prepare()
    assert len(fake_cli) == 1 and sessions.ready('runtime')
    assert worker.session_id is None  # Initialization sent no paid model turn.
    first = sessions.run(kind, 'default', config, 'hello', '', tools, lambda *_: 'first')
    second_config = {**config, 'resume_session_id': first.session_id}
    second = sessions.run(kind, 'default', second_config, 'again', '', tools, lambda *_: 'second')
    assert len(fake_cli) == 1
    # A Windows venv launcher can own a separate child interpreter PID.
    assert first.output.endswith(':first') and second.output.endswith(':second')
    assert first.output.split(':')[0] == second.output.split(':')[0]
    assert first.session_id == second.session_id
    pid = worker.process.pid
    sessions.stop()
    assert not psutil.pid_exists(pid) and not sessions.ready('runtime')


def test_separate_agents_and_permission_changes_do_not_share_sessions(fake_cli):
    config = {'_persistent_key': 'one:runtime'}
    first = sessions.acquire('codex_cli', 'default', config, [])
    second = sessions.acquire('codex_cli', 'default', {**config, '_persistent_key': 'two:runtime'}, [])
    assert first is not second
    with first.lock:
        first.prepare()
    replacement = sessions.acquire('codex_cli', 'default', {**config, 'terminal_enabled': True}, [])
    assert replacement is not first and first.closed


@pytest.mark.parametrize('kind', ['codex_cli', 'claude_code'])
def test_live_probes_send_new_turns_using_same_prepared_process(fake_cli, kind):
    config = {'_persistent_key': '_runtime:runtime', 'timeout': 5}
    worker = sessions.acquire(kind, 'default', config, [])
    with worker.lock:
        worker.prepare()
    first = sessions.probe(kind, 'default', 'runtime', config, 'Reply OK')
    second = sessions.probe(kind, 'default', 'runtime', config, 'Reply OK again')
    assert len(fake_cli) == 1
    assert first.output == second.output
    assert first.session_id == second.session_id


def test_hung_turn_is_killed_and_can_be_restarted(fake_cli):
    config = {'_persistent_key': 'agent:runtime', 'timeout': .5}
    worker = sessions.acquire('codex_cli', 'default', config, [])
    with pytest.raises(TimeoutError):
        worker.run('hang', '', config, None)
    assert worker.closed and not psutil.pid_exists(worker.process.pid)
    replacement = sessions.acquire('codex_cli', 'default', config, [])
    assert replacement is not worker


def test_existing_codex_thread_is_loaded_before_first_turn(fake_cli):
    config = {'_persistent_key': 'agent:runtime', 'timeout': 2, 'resume_session_id': 'stored-thread'}
    result = sessions.run('codex_cli', 'default', config, 'hello', '', [], None)
    assert result.session_id == 'stored-thread'


def test_missing_codex_thread_has_specific_recoverable_error(fake_cli):
    from hagent.adapters.base import CLIHistoryMissing
    config = {'_persistent_key': 'agent:runtime', 'timeout': 2, 'resume_session_id': 'missing'}
    with pytest.raises(CLIHistoryMissing, match='thread not found'):
        sessions.run('codex_cli', 'default', config, 'hello', '', [], None)


def test_app_start_initializes_each_connected_provider_without_running_every_agent(fake_cli, cli_db, monkeypatch):
    from hagent import db, engine
    from hagent.models import Agent
    from tests.test_cli_defaults import seed
    with db.get_session(scoped=False) as session:
        agent, runtimes = seed(session)
        for i in range(8):
            session.add(Agent(workspace_id=agent.workspace_id, name=f'Extra {i}', runtime_id=runtimes[0].id))
        ids = [r.id for r in runtimes]
        session.commit()
    monkeypatch.setattr(engine, '_build_agent_tools', lambda *_: ([], {}, {}, {}))
    thread = sessions.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(fake_cli) == 3
    assert all(sessions.ready(ident) for ident in ids)
    assert all(worker.session_id is None for _, worker in sessions._pool.values())
