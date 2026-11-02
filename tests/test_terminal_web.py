import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hagent import auth, db, terminal_web, web
from hagent.models import TerminalSession, User, Workspace


def test_members_cannot_access_terminal(cli_db):
    with db.get_session(scoped=False) as s:
        auth.create_user(s, 'owner', 'a long test password', 'owner')
        member = auth.create_user(s, 'member', 'a long test password', 'member')
        token, _ = auth.issue_token(s, member)
    client = TestClient(web.app, headers={'Authorization': 'Bearer ' + token})
    for url in ['/terminal', '/api/terminal/sessions', '/api/terminal/activity', '/api/terminal/agents']:
        assert client.get(url).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/terminal', headers={'Origin': 'http://testserver'}):
            pass


@pytest.mark.parametrize('failure', [None, 'stop', 'save'])
def test_terminal_session_closes_and_persists_output(cli_db, monkeypatch, tmp_path, failure):
    stopped = threading.Event()
    class Process:
        def __init__(self): self.reads = 0
        def read(self, size):
            self.reads += 1
            if self.reads == 1: return 'terminal result: 42\r\n'
            stopped.wait(10)
            raise EOFError()
        def isalive(self): return not stopped.is_set()
    monkeypatch.setattr(terminal_web, '_spawn', lambda cwd: Process())
    def stop(process):
        stopped.set()
        if failure == 'stop':
            raise OSError('simulated termination failure')
    monkeypatch.setattr(terminal_web, '_stop', stop)
    if failure == 'save':
        def save(*args):
            raise OSError('simulated disk failure')
        monkeypatch.setattr(terminal_web, '_save', save)
    with db.get_session() as s:
        workspace = db.get_active_workspace(s)
        s.commit()
    client = TestClient(web.app)
    with client.websocket_connect('/ws/terminal?cwd=' + str(tmp_path),
                                  headers={'Origin': 'http://testserver'}) as ws:
        ready = ws.receive_json()
        assert ready['type'] == 'ready'
        assert ws.receive_json()['data'] == 'terminal result: 42\r\n'
        ws.send_json({'type': 'stop'})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()
    assert stopped.is_set()
    assert 'local-owner' not in terminal_web._active
    result = client.get('/api/terminal/sessions/' + ready['id']).json()
    if failure != 'save':
        assert result['status'] == ('cleanup_failed' if failure == 'stop' else 'closed')
        assert result['transcript'] == 'terminal result: 42\r\n'


def test_terminal_history_is_workspace_scoped(cli_db):
    with db.get_session() as s:
        own = db.get_active_workspace(s)
        s.commit()
    with db.get_session(scoped=False) as s:
        other = Workspace(name='other')
        s.add(other); s.flush()
        row = TerminalSession(workspace_id=other.id, owner_id='local-owner', cwd='C:/', transcript='private')
        s.add(row); s.commit()
        row_id = row.id
    client = TestClient(web.app)
    assert client.get('/api/terminal/sessions/' + row_id).status_code == 404
    assert not client.get('/api/terminal/sessions').json()


def test_terminal_missing_directory_does_not_reserve_owner(cli_db, tmp_path):
    client = TestClient(web.app)
    with client.websocket_connect('/ws/terminal?cwd=' + str(tmp_path / 'missing'),
                                  headers={'Origin': 'http://testserver'}) as ws:
        assert ws.receive_json()['type'] == 'error'
        with pytest.raises(WebSocketDisconnect): ws.receive_json()
    assert 'local-owner' not in terminal_web._active


def test_restart_refuses_open_terminal(tmp_path):
    import os
    from pathlib import Path
    import sqlite3
    import subprocess
    import sys
    database = tmp_path / 'restart-check.db'
    with sqlite3.connect(database) as s:
        s.execute('CREATE TABLE runs (status TEXT)')
        s.execute('CREATE TABLE terminal_sessions (status TEXT)')
        s.execute("INSERT INTO terminal_sessions VALUES ('running')")
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'scripts/restart_hagent.py'), '--check'],
                            env=dict(os.environ, HAGENT_DB_PATH=str(database)),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'terminal session(s) are open' in result.stderr


def test_multiple_terminal_sessions_are_independent(cli_db, monkeypatch, tmp_path):
    class Process:
        def __init__(self):
            self.stopped = threading.Event()
            self.writes = []
        def read(self, size):
            self.stopped.wait(10)
            raise EOFError()
        def isalive(self): return not self.stopped.is_set()
        def write(self, data): self.writes.append(data)
    processes = []
    def spawn(cwd):
        process = Process()
        processes.append(process)
        return process
    monkeypatch.setattr(terminal_web, '_spawn', spawn)
    monkeypatch.setattr(terminal_web, '_stop', lambda p: p.stopped.set())
    monkeypatch.setattr(terminal_web, 'MAX_TERMINALS_PER_OWNER', 2)
    client = TestClient(web.app)
    endpoint = '/ws/terminal?cwd=' + str(tmp_path)
    headers = {'Origin': 'http://testserver'}
    with client.websocket_connect(endpoint, headers=headers) as first:
        first_id = first.receive_json()['id']
        with client.websocket_connect(endpoint, headers=headers) as second:
            assert second.receive_json()['id'] != first_id
            assert terminal_web._active['local-owner'] == 2
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(endpoint, headers=headers): pass
            second.send_json({'type': 'stop'})
            with pytest.raises(WebSocketDisconnect): second.receive_json()
        assert not processes[0].stopped.is_set()
        assert terminal_web._active['local-owner'] == 1
        first.send_json({'type': 'input', 'data': 'echo still running\r'})
        first.send_json({'type': 'stop'})
        with pytest.raises(WebSocketDisconnect): first.receive_json()
    assert processes[0].writes == ['echo still running\r']
    assert 'local-owner' not in terminal_web._active
