import json
from fastapi.testclient import TestClient
from sqlalchemy import select
from hagent import db, web
from hagent.models import Runtime, Agent
from hagent.adapters.base import RuntimeResult
from tests.test_agent_pages import local_db, seed_agent


def test_saved_status_does_not_probe_and_success_updates_timestamp(local_db, mocker):
    from datetime import datetime, timedelta, timezone
    from hagent.provider_health import record_runtime_success
    _, runtime_id, _ = seed_agent()
    probe = mocker.patch('hagent.provider_health._probe')
    with db.get_session(scoped=False) as s:
        runtime = s.get(Runtime, runtime_id)
        runtime.health_status = 'healthy'
        runtime.last_health_check_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        s.commit()
        old_time = TestClient(web.app).get('/api/runtime-status').json()['runtimes'][0]['checked_at']
        record_runtime_success(runtime)
    rows = TestClient(web.app).get('/api/runtime-status').json()['runtimes']
    assert rows[0]['id'] == runtime_id and rows[0]['checked_at'] != old_time and rows[0]['ok']
    probe.assert_not_called()


def test_connection_calls_selected_runtime_and_records_success(local_db, mocker):
    _, runtime_id, _ = seed_agent()
    call = mocker.patch('hagent.adapters.ollama.OllamaRuntime.run', return_value=RuntimeResult(output='OK'))
    result = TestClient(web.app).post(f'/runtimes/{runtime_id}/test')
    assert result.status_code == 200 and result.json()['ok'] is True
    call.assert_called_once()
    with db.get_session(scoped=False) as s:
        assert s.get(Runtime, runtime_id).health_status == 'healthy'
        assert s.get(Runtime, runtime_id).last_health_check_at is not None


def test_different_runtimes_are_checked_in_parallel_and_same_runtime_is_guarded(local_db, mocker):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from hagent.provider_health import manual_probe_lock_for
    _, first, _ = seed_agent()
    with db.get_session(scoped=False) as s:
        original = s.get(Runtime, first)
        other = Runtime(workspace_id=original.workspace_id, name='Second', type=original.type, model='test')
        s.add(other); s.commit()
        second = other.id
    barrier = threading.Barrier(2)
    probe = mocker.patch('hagent.provider_health._probe', side_effect=lambda _: barrier.wait(timeout=5))
    def check(ident):
        return TestClient(web.app).post(f'/runtimes/{ident}/test')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, [first, second]))
    assert all(response.status_code == 200 and response.json()['ok'] for response in results)
    assert probe.call_count == 2
    with manual_probe_lock_for(first):
        assert check(first).status_code == 409


def test_failed_connection_redacts_saved_key(local_db, mocker):
    _, runtime_id, _ = seed_agent()
    with db.get_session(scoped=False) as s:
        s.get(Runtime, runtime_id).config_json = json.dumps({'api_key': 'private-test-key'})
        s.commit()
    mocker.patch('hagent.provider_health._probe', side_effect=RuntimeError('401 invalid private-test-key'))
    result = TestClient(web.app).post(f'/runtimes/{runtime_id}/test').json()
    assert result['ok'] is False
    assert 'private-test-key' not in result['message']
    with db.get_session(scoped=False) as s:
        assert 'private-test-key' not in s.get(Runtime, runtime_id).health_detail


def test_empty_reply_is_not_a_success(local_db, mocker):
    _, runtime_id, _ = seed_agent()
    mocker.patch('hagent.adapters.ollama.OllamaRuntime.run', return_value=RuntimeResult(output=''))
    assert TestClient(web.app).post(f'/runtimes/{runtime_id}/test').json()['ok'] is False


def test_remove_guards_recovery_dependency_and_retains_history(local_db):
    agent_id, runtime_id, _ = seed_agent()
    client = TestClient(web.app)
    assert client.post(f'/runtimes/{runtime_id}/remove').status_code == 409
    with db.get_session(scoped=False) as s:
        original = s.get(Runtime, runtime_id)
        other = Runtime(workspace_id=original.workspace_id, name='Other', type=original.type, model='test')
        s.add(other); s.flush()
        agent = s.get(Agent, agent_id)
        agent.runtime_id = other.id
        agent.failback_runtime_id = runtime_id
        s.commit()
    assert client.post(f'/runtimes/{runtime_id}/remove').status_code == 409
    with db.get_session(scoped=False) as s:
        s.get(Agent, agent_id).failback_runtime_id = None
        s.commit()
    assert client.post(f'/runtimes/{runtime_id}/remove').json()['ok']
    with db.get_session(scoped=False) as s:
        assert s.get(Runtime, runtime_id).archived
    assert client.post(f'/runtimes/{runtime_id}/test').status_code == 409


def test_actions_present_on_list_and_configuration(local_db):
    _, runtime_id, _ = seed_agent()
    client = TestClient(web.app)
    for path in ['/runtimes', f'/runtimes/{runtime_id}']:
        page = client.get(path)
        assert 'Test connection' in page.text and 'Remove runtime' in page.text


def test_archived_agent_does_not_block_runtime_removal(local_db):
    agent_id, runtime_id, _ = seed_agent()
    with db.get_session(scoped=False) as s:
        agent = s.get(Agent, agent_id)
        agent.archived = True
        agent.backup_runtime_id = runtime_id
        agent.failback_runtime_id = runtime_id
        s.commit()
    response = TestClient(web.app).post(f'/runtimes/{runtime_id}/remove')
    assert response.status_code == 200
    with db.get_session(scoped=False) as s:
        assert s.get(Runtime, runtime_id).archived
        assert s.get(Agent, agent_id).runtime_id == runtime_id
