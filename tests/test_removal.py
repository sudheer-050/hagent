from fastapi.testclient import TestClient
from sqlalchemy import select, text
from hagent import db, models as m, removal
from hagent.web import app
from tests.test_agent_pages import local_db, seed_agent


def test_project_removal_cascades_owned_work_and_detaches_memory(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session() as s:
        agent = s.get(m.Agent, agent_id)
        p = m.Project(workspace_id=agent.workspace_id, name='Disposable')
        s.add(p); s.flush()
        issue = m.Issue(project_id=p.id, title='Parent')
        s.add(issue); s.flush()
        child = m.Issue(project_id=p.id, parent_issue_id=issue.id, title='Child')
        s.add(child); s.flush()
        s.add_all([m.Run(issue_id=child.id, agent_id=agent_id, prompt='Test', status=m.RunStatus.COMPLETED),
                   m.Comment(issue_id=child.id, body='Test'),
                   m.Memory(workspace_id=agent.workspace_id, project_id=p.id, content='Keep this', user_id='test', category='fact', normalized_hash='test')])
        s.commit(); ident = p.id
    client = TestClient(app)
    preview = client.get(f'/manage/remove/projects/{ident}')
    assert preview.status_code == 200 and '2 issues' in preview.text
    result = client.post(f'/manage/remove/projects/{ident}', data={'confirmation': ident}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with db.get_session() as s:
        assert s.get(m.Project, ident) is None
        assert not s.scalar(select(m.Run.id))
        assert s.scalar(select(m.Memory)).project_id is None
        assert not s.execute(text('PRAGMA foreign_key_check')).all()


def test_active_run_blocks_delete_without_partial_changes(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session() as s:
        p = m.Project(workspace_id=s.get(m.Agent, agent_id).workspace_id, name='Busy')
        s.add(p); s.flush()
        issue = m.Issue(project_id=p.id, title='Busy')
        s.add(issue); s.flush()
        s.add(m.Run(issue_id=issue.id, agent_id=agent_id, prompt='Test', status=m.RunStatus.RUNNING))
        s.commit(); ident = p.id
    result = TestClient(app).post(f'/manage/remove/projects/{ident}', data={'confirmation': ident})
    assert result.status_code == 409
    with db.get_session() as s:
        assert s.get(m.Project, ident) is not None
        assert s.scalar(select(m.Run.id))


def test_skill_delete_detaches_assignments(local_db):
    agent_id, _, skill_id = seed_agent()
    with db.get_session() as s:
        s.get(m.Agent, agent_id).skills = [s.get(m.Skill, skill_id)]
        s.commit()
    result = TestClient(app).post(f'/manage/remove/skills/{skill_id}', data={'confirmation': skill_id})
    assert result.status_code == 200
    with db.get_session() as s:
        assert s.get(m.Skill, skill_id) is None
        assert not s.get(m.Agent, agent_id).skills


def test_archive_agent_preserves_record_and_hides_picker(local_db):
    ident, _, _ = seed_agent()
    result = TestClient(app).post(f'/manage/remove/agents/{ident}', data={'confirmation': ident})
    assert result.status_code == 200
    with db.get_session() as s:
        assert s.get(m.Agent, ident).archived
    assert ident not in TestClient(app).get('/manage/remove?kind=agents').text


def test_current_workspace_cannot_be_deleted(local_db):
    with db.get_session() as s:
        ident = db.get_active_workspace(s).id
    response = TestClient(app).post(f'/manage/remove/workspaces/{ident}', data={'confirmation': ident})
    assert response.status_code == 409


def test_cross_workspace_chat_message_cannot_be_removed(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as s:
        ws = m.Workspace(name='Other'); s.add(ws); s.flush()
        rt = m.Runtime(workspace_id=ws.id, name='Other', type=m.RuntimeType.OLLAMA, model='x')
        s.add(rt); s.flush()
        agent = m.Agent(workspace_id=ws.id, runtime_id=rt.id, name='Other')
        s.add(agent); s.flush()
        thread = m.ChatThread(workspace_id=ws.id, agent_id=agent.id)
        s.add(thread); s.flush()
        message = m.ChatMessage(thread_id=thread.id, role='user', content='Private')
        s.add(message); s.commit(); ident = message.id
    response = TestClient(app).post(f'/manage/remove/messages/{ident}', data={'confirmation': ident})
    assert response.status_code == 404


def test_every_removal_category_renders(local_db):
    seed_agent()
    client = TestClient(app)
    for kind in removal.KINDS:
        response = client.get('/manage/remove', params={'kind': kind})
        assert response.status_code == 200, (kind, response.text)


def test_workspace_removes_its_archived_history_only(local_db):
    seed_agent()
    with db.get_session(scoped=False) as s:
        ws = m.Workspace(name='Disposable'); s.add(ws); s.flush()
        rt = m.Runtime(workspace_id=ws.id, name='Archived', type=m.RuntimeType.OLLAMA, model='x', archived=True)
        s.add(rt); s.flush()
        a = m.Agent(workspace_id=ws.id, runtime_id=rt.id, name='Archived', archived=True)
        s.add(a); s.flush()
        p = m.Project(workspace_id=ws.id, name='History'); s.add(p); s.flush()
        issue = m.Issue(project_id=p.id, title='Finished'); s.add(issue); s.flush()
        s.add(m.Run(issue_id=issue.id, agent_id=a.id, prompt='Test', status=m.RunStatus.COMPLETED))
        s.commit(); ident = ws.id
    result = TestClient(app).post(f'/manage/remove/workspaces/{ident}', data={'confirmation': ident})
    assert result.status_code == 200, result.text
    with db.get_session(scoped=False) as s:
        assert s.get(m.Workspace, ident) is None
        assert len(s.scalars(select(m.Agent)).all()) == 1
        assert not s.execute(text('PRAGMA foreign_key_check')).all()


def test_terminal_history_can_only_be_deleted_after_stop(local_db):
    with db.get_session() as s:
        row = m.TerminalSession(workspace_id=db.get_active_workspace(s).id, owner_id='local-owner', cwd='C:/', status='running')
        s.add(row); s.commit(); ident = row.id
    client = TestClient(app)
    assert client.post(f'/manage/remove/terminal-sessions/{ident}', data={'confirmation': ident}).status_code == 409
    with db.get_session() as s:
        s.get(m.TerminalSession, ident).status = 'closed'; s.commit()
    assert client.post(f'/manage/remove/terminal-sessions/{ident}', data={'confirmation': ident}).status_code == 200


def test_verifier_dependency_blocks_archive(local_db):
    ident, runtime_id, _ = seed_agent()
    with db.get_session() as s:
        agent = s.get(m.Agent, ident)
        s.add(m.Agent(workspace_id=agent.workspace_id, runtime_id=runtime_id,
                      name='Needs verification', verifier_agent_id=ident))
        s.commit()
    response = TestClient(app).post(f'/manage/remove/agents/{ident}', data={'confirmation': ident})
    assert response.status_code == 409
    assert 'Choose another verifier' in response.text


def test_remove_label_unlinks_only_selected_issue(local_db):
    with db.get_session() as s:
        ws = db.get_active_workspace(s)
        project = m.Project(workspace_id=ws.id, name='Labels'); s.add(project); s.flush()
        label = m.Label(workspace_id=ws.id, name='Keep label')
        first = m.Issue(project_id=project.id, title='First', labels=[label])
        second = m.Issue(project_id=project.id, title='Second', labels=[label])
        s.add_all([first, second]); s.commit()
        first_id, second_id, label_id = first.id, second.id, label.id
    response = TestClient(app).post(f'/issues/{first_id}/labels/{label_id}/remove', follow_redirects=False)
    assert response.status_code == 303
    with db.get_session() as s:
        assert not s.get(m.Issue, first_id).labels
        assert s.get(m.Issue, second_id).labels[0].id == label_id


def test_agent_archive_is_profile_only_and_returns_to_agents(local_db):
    ident, _, _ = seed_agent()
    client = TestClient(app)
    assert 'Archive agent' not in client.get('/agents').text
    assert 'Delete or archive items' not in client.get('/agents').text
    assert '<option value="agents"' not in client.get('/manage/remove').text
    profile = client.get(f'/agents/{ident}')
    assert f'action="/agents/{ident}/archive"' in profile.text
    response = client.post(f'/agents/{ident}/archive', follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/agents'
    with db.get_session() as s:
        assert s.get(m.Agent, ident).archived
