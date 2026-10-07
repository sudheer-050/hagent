import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.adapters.base import RuntimeResult
from hagent.models import Agent, Runtime, RuntimeType, Skill
from hagent.models import Autopilot, AutopilotRun
from hagent.models import Issue, Project, Run, RunStatus, Squad, SquadMember, TimelineEvent
from hagent.skill_icons import SKILL_EMOJI_GROUPS, PERSON_EMOJIS
from hagent.tenancy import WorkspaceSession
from hagent.web import app


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False),
    )
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    db.init_db()
    yield engine
    engine.dispose()


def seed_agent():
    with db.get_session(scoped=False) as session:
        workspace = db.get_or_create_default_workspace(session)
        runtime = Runtime(
            workspace_id=workspace.id,
            name="Local Ollama",
            type=RuntimeType.OLLAMA,
            model="qwen3",
        )
        skill = Skill(workspace_id=workspace.id, name="Research", description="Find facts")
        session.add_all([runtime, skill])
        session.flush()
        agent = Agent(
            workspace_id=workspace.id,
            runtime_id=runtime.id,
            name="Scout",
            instructions="Investigate carefully",
        )
        session.add(agent)
        session.commit()
        return agent.id, runtime.id, skill.id


def test_agents_page_is_focused_inventory(local_db):
    agent_id, _, _ = seed_agent()
    response = TestClient(app).get("/agents")
    assert response.status_code == 200
    assert "Create agent" in response.text
    assert "Scout" in response.text
    assert f'/agents/{agent_id}' in response.text


def test_page_rendering_does_not_open_nested_database_sessions(tmp_path, monkeypatch):
    """Base-template helpers must not deadlock a route holding the only connection."""
    database_url = f"sqlite:///{tmp_path / 'single-connection.db'}"
    setup_engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(db, "engine", setup_engine)
    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=setup_engine, class_=WorkspaceSession, expire_on_commit=False),
    )
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    db.init_db()
    seed_agent()
    setup_engine.dispose()

    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False),
    )

    response = TestClient(app).get("/agents")

    assert response.status_code == 200
    assert "Scout" in response.text
    engine.dispose()


def test_settings_page_persists_branding_and_feature_toggles(local_db, tmp_path):
    client = TestClient(app)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Identity and appearance" in page.text
    assert "New-agent defaults" in page.text
    assert "Manage Hagent" in page.text

    response = client.post(
        "/settings",
        data={
            "app_name": "My Agent Desk",
            "tagline": "Private AI workspace",
            "accent_color": "#33aabb",
            "density": "compact",
            "default_agent_delegation_limit": "3",
            "default_agent_terminal_directory": str(tmp_path),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "My Agent Desk" in dashboard.text
    assert "Private AI workspace" in dashboard.text
    assert "--accent:#33aabb" in dashboard.text
    assert '<html class="density-compact"' in dashboard.text
    assert 'id="starsCanvas"' not in dashboard.text
    assert 'id="holly-fab"' not in dashboard.text

    saved = client.get("/settings")
    assert 'value="3"' in saved.text
    assert "settings-delegation" in saved.text


def test_agent_detail_updates_profile_and_skills(local_db, tmp_path):
    agent_id, runtime_id, skill_id = seed_agent()
    client = TestClient(app)
    detail = client.get(f"/agents/{agent_id}")
    assert detail.status_code == 200
    assert "Assigned work" in detail.text
    assert "MCP tool servers" in detail.text
    assert "Environment variables (JSON)" in detail.text

    response = client.post(
        f"/agents/{agent_id}",
        data={
            "name": "Lead Scout",
            "runtime_id": runtime_id,
            "instructions": "Lead investigations",
            "skill_ids": skill_id,
            "environment_json": '{"PROJECT_MODE": "review"}',
            "terminal_enabled": "1",
            "terminal_working_directory": str(tmp_path),
            "require_run_approval": "1",
            "delegation_limit": "3",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        agent = session.scalar(select(Agent).where(Agent.id == agent_id))
        assert agent.name == "Lead Scout"
        assert [skill.id for skill in agent.skills] == [skill_id]
        assert json.loads(agent.env_json) == {"PROJECT_MODE": "review"}
        assert agent.terminal_enabled is True
        assert agent.require_run_approval is True
        assert agent.delegation_limit == 3
        assert agent.terminal_working_directory == str(tmp_path.resolve())


def test_skills_library_assigns_reusable_skill_to_any_agent(local_db):
    agent_id, _, skill_id = seed_agent()
    client = TestClient(app)

    library = client.get("/skills")
    assert library.status_code == 200
    assert "Skills library" in library.text
    assert 'class="skill-badge"' in library.text
    assert f'data-modal="skill-modal-{skill_id}"' in library.text
    assert 'Last used' in library.text
    assert 'Never used' in library.text
    assert 'Save skill details' in library.text
    assert 'skill-person-icon' not in library.text
    assert "Agents permitted to use this skill" in library.text
    assert 'name="agent_ids"' in library.text

    response = client.post(f"/skills/{skill_id}/agents", data={"agent_ids": agent_id}, follow_redirects=False)
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        assert [skill.id for skill in agent.skills] == [skill_id]

    response = client.post(f"/skills/{skill_id}/agents", data={}, follow_redirects=False)
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        assert agent.skills == []


def test_new_skill_icons_match_type_are_unique_and_persist(local_db):
    seed_agent()
    client = TestClient(app)
    for name in ('Code review', 'Python debugging', 'Science research'):
        created = client.post('/skills', data={'name': name}, follow_redirects=False)
        assert created.status_code == 303

    with db.get_session(scoped=False) as session:
        skills = {skill.name: skill for skill in session.scalars(select(Skill)).all()}
        icons = {name: skill.emoji for name, skill in skills.items()}

    assert icons['Code review'] in SKILL_EMOJI_GROUPS['coding']
    assert icons['Python debugging'] in SKILL_EMOJI_GROUPS['coding']
    assert icons['Science research'] in SKILL_EMOJI_GROUPS['research']
    assert icons['Research'] in SKILL_EMOJI_GROUPS['research']
    assert len(set(icons.values())) == len(icons)
    assert set(icons.values()) <= set(PERSON_EMOJIS)
    for _ in range(2):
        page = client.get('/skills')
        assert page.status_code == 200
        badges = re.findall(r'<svg class="skill-badge".*?</svg>', page.text, re.S)
        assert len(badges) == len(skills)
        assert all(icon not in page.text for icon in icons.values())


def test_missing_and_duplicate_skill_icons_are_repaired(local_db):
    seed_agent()
    with db.get_session(scoped=False) as session:
        first = session.scalar(select(Skill))
        duplicate = Skill(
            workspace_id=first.workspace_id,
            name='Research evidence',
            emoji=first.emoji,
        )
        first.emoji = ''
        session.add(duplicate)
        session.commit()

    db.init_db()

    with db.get_session(scoped=False) as session:
        icons = [skill.emoji for skill in session.scalars(select(Skill)).all()]
    assert len(icons) == 2
    assert all(icons)
    assert len(set(icons)) == 2
    assert set(icons) <= set(SKILL_EMOJI_GROUPS['research'])


def test_skill_rows_show_badges_and_details_save_all_fields(local_db):
    agent_id, _, skill_id = seed_agent()
    client = TestClient(app)
    for name, content in (
        ('Simple notes', ''),
        ('Moderate instructions', 'x' * 200),
        ('Deep analysis', 'x' * 800),
        ('Expert orchestration', 'x' * 1700),
    ):
        assert client.post('/skills', data={'name': name, 'content': content}, follow_redirects=False).status_code == 303

    page = client.get('/skills')
    rows = re.findall(r'<button type="button" class="collection-row skill-row skill-open".*?</button>', page.text, re.S)
    for name in ('Simple notes', 'Moderate instructions', 'Deep analysis', 'Expert orchestration'):
        assert any(f'Open details for {name}' in row and 'class="skill-badge"' in row for row in rows)
    research_row = next(row for row in rows if 'Open details for Research' in row)
    assert 'Find facts' not in research_row
    assert 'Never used' in research_row

    saved = client.post(
        f'/skills/{skill_id}',
        data={
            'name': 'Research Plus',
            'description': 'Find updated facts',
            'content': 'Verify every claim.',
            'last_used_at': '2026-09-01T14:30',
            'agent_ids': agent_id,
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    with db.get_session(scoped=False) as session:
        skill = session.get(Skill, skill_id)
        assert skill.name == 'Research Plus'
        assert skill.description == 'Find updated facts'
        assert skill.content == 'Verify every claim.'
        assert skill.last_used_at.strftime('%Y-%m-%dT%H:%M') == '2026-09-01T14:30'
        assert [agent.id for agent in skill.agents] == [agent_id]
    assert 'Research Plus' in client.get('/skills').text
    assert client.post(f'/skills/{skill_id}', data={'name': 'Research Plus', 'last_used_at': 'not-a-date'}).status_code == 400


def test_agent_list_uses_distinct_stable_generated_avatars(local_db):
    agent_id, runtime_id, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        original = session.get(Agent, agent_id)
        second = Agent(workspace_id=original.workspace_id, runtime_id=runtime_id, name='Second agent')
        session.add(second)
        session.commit()
        second_id = second.id
    client = TestClient(app)
    page = client.get('/agents')
    avatars = re.findall(r'class="agent-avatar-img" src="([^"]+)"', page.text)
    assert len(avatars) == 2
    assert len(set(avatars)) == 2
    assert any(f'seed={agent_id}' in avatar for avatar in avatars)
    assert any(f'seed={second_id}' in avatar for avatar in avatars)
    assert f'seed={agent_id}' in client.get(f'/agents/{agent_id}').text
    assert f'seed={second_id}' in client.get(f'/agents/{second_id}').text
    with db.get_session(scoped=False) as session:
        second = session.get(Agent, second_id)
        second.name = 'A renamed agent'
        session.commit()
    reordered = client.get('/agents')
    agent_rows = re.findall(r'href="/agents/([^"]+)".*?class="agent-avatar-img" src="([^"]+)"', reordered.text, re.S)
    assert f'seed={agent_id}' in dict(agent_rows)[agent_id]
    assert f'seed={second_id}' in dict(agent_rows)[second_id]


def test_generated_agent_avatars_stay_unique_for_large_lists(local_db):
    agent_id, runtime_id, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        original = session.get(Agent, agent_id)
        session.add_all(
            Agent(workspace_id=original.workspace_id, runtime_id=runtime_id, name=f'Agent {index:02d}')
            for index in range(25)
        )
        session.commit()
    page = TestClient(app).get('/agents')
    avatars = re.findall(r'class="agent-avatar-img" src="([^"]+)"', page.text)
    assert len(avatars) == 26
    assert len(set(avatars)) == len(avatars)


def test_autopilot_page_shows_recent_execution_outcomes(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        autopilot = Autopilot(workspace_id=agent.workspace_id, name='Daily review', agent_id=agent.id)
        session.add(autopilot)
        session.flush()
        session.add(AutopilotRun(autopilot_id=autopilot.id, status='partial', summary='2 succeeded, 1 failed'))
        session.commit()

    response = TestClient(app).get('/autopilots')

    assert response.status_code == 200
    assert 'Recent executions' in response.text
    assert 'Daily review' in response.text
    assert '2 succeeded, 1 failed' in response.text
    assert 'partial' in response.text


def test_issue_run_executes_from_persisted_background_queue(local_db, mocker):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        project = Project(workspace_id=agent.workspace_id, name='Background project')
        session.add(project)
        session.flush()
        issue = Issue(
            project_id=project.id,
            title='Background task',
            description='Do work',
            assignee_agent_id=agent.id,
        )
        session.add(issue)
        session.commit()
        issue_id = issue.id
    mocker.patch(
        'hagent.adapters.ollama.OllamaRuntime.run',
        return_value=RuntimeResult(output='background completed'),
    )

    response = TestClient(app).post(f'/issues/{issue_id}/run', follow_redirects=False)

    assert response.status_code == 303
    with db.get_session() as session:
        run = session.scalar(select(Run).where(Run.issue_id == issue_id))
        assert run.status == RunStatus.COMPLETED
        assert run.output == 'background completed'


def test_failed_issue_run_can_be_retried_with_same_agent_and_prompt(local_db, mocker):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        project = Project(workspace_id=agent.workspace_id, name="Retry project")
        session.add(project)
        session.flush()
        issue = Issue(project_id=project.id, title="Retry me", assignee_agent_id=agent.id)
        session.add(issue)
        session.flush()
        failed = Run(issue_id=issue.id, agent_id=agent.id, prompt="Original task", status=RunStatus.FAILED, error="temporary provider error")
        session.add(failed)
        session.commit()
        issue_id, failed_id = issue.id, failed.id
    mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="retry completed"),
    )

    response = TestClient(app).post(
        f"/issues/{issue_id}/runs/{failed_id}/retry",
        follow_redirects=False,
    )

    assert response.status_code == 303
    with db.get_session() as session:
        runs = session.scalars(select(Run).where(Run.issue_id == issue_id).order_by(Run.created_at)).all()
        assert len(runs) == 2
        assert runs[0].status == RunStatus.FAILED
        assert runs[1].status == RunStatus.COMPLETED
        assert runs[1].agent_id == agent_id
        assert runs[1].prompt == "Original task"
        assert runs[1].output == "retry completed"


def test_retry_endpoint_rejects_active_runs_and_cross_issue_ids(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        project = Project(workspace_id=agent.workspace_id, name="Retry guards")
        session.add(project)
        session.flush()
        issue_a = Issue(project_id=project.id, title="Issue A")
        issue_b = Issue(project_id=project.id, title="Issue B")
        session.add_all([issue_a, issue_b])
        session.flush()
        active = Run(issue_id=issue_a.id, agent_id=agent.id, prompt="Active", status=RunStatus.RUNNING)
        failed = Run(issue_id=issue_b.id, agent_id=agent.id, prompt="Failed elsewhere", status=RunStatus.FAILED)
        session.add_all([active, failed])
        session.commit()
        issue_a_id, issue_b_id, active_id, failed_id = issue_a.id, issue_b.id, active.id, failed.id

    client = TestClient(app)
    assert client.post(f"/issues/{issue_a_id}/runs/{active_id}/retry").status_code == 409
    assert client.post(f"/issues/{issue_a_id}/runs/{failed_id}/retry").status_code == 404


def test_agent_run_waits_for_approval_then_runs_after_approval(local_db, mocker):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        agent.require_run_approval = True
        project = Project(workspace_id=agent.workspace_id, name="Approval project")
        session.add(project)
        session.flush()
        issue = Issue(project_id=project.id, title="Review before run", assignee_agent_id=agent.id)
        session.add(issue)
        session.commit()
        issue_id = issue.id
    runtime_call = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="approved execution completed"),
    )
    client = TestClient(app)

    queued = client.post(f"/issues/{issue_id}/run", follow_redirects=False)

    assert queued.status_code == 303
    runtime_call.assert_not_called()
    with db.get_session() as session:
        run = session.scalar(select(Run).where(Run.issue_id == issue_id))
        assert run.status == RunStatus.WAITING_APPROVAL
        run_id = run.id
    page = client.get(f"/issues/{issue_id}")
    assert "Awaiting your approval" in page.text
    assert "Approve and run" in page.text

    approved = client.post(f"/issues/{issue_id}/runs/{run_id}/approve", follow_redirects=False)

    assert approved.status_code == 303
    runtime_call.assert_called_once()
    with db.get_session() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.output == "approved execution completed"
        events = session.scalars(select(TimelineEvent).where(TimelineEvent.issue_id == issue_id)).all()
        assert {event.event_type for event in events} >= {"run_approval_requested", "run_approved", "run_started"}


def test_rejected_approval_never_executes_and_is_audited(local_db, mocker):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        agent.require_run_approval = True
        project = Project(workspace_id=agent.workspace_id, name="Reject project")
        session.add(project)
        session.flush()
        issue = Issue(project_id=project.id, title="Reject this run", assignee_agent_id=agent.id)
        session.add(issue)
        session.commit()
        issue_id = issue.id
    runtime_call = mocker.patch("hagent.adapters.ollama.OllamaRuntime.run")
    client = TestClient(app)
    client.post(f"/issues/{issue_id}/run", follow_redirects=False)
    with db.get_session() as session:
        run = session.scalar(select(Run).where(Run.issue_id == issue_id))
        run_id = run.id

    rejected = client.post(f"/issues/{issue_id}/runs/{run_id}/reject", follow_redirects=False)

    assert rejected.status_code == 303
    runtime_call.assert_not_called()
    with db.get_session() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.REJECTED
        assert run.error == "rejected by user"
        assert session.scalar(select(TimelineEvent).where(TimelineEvent.issue_id == issue_id, TimelineEvent.event_type == "run_rejected"))
    assert client.post(f"/issues/{issue_id}/runs/{run_id}/approve").status_code == 409


def test_approvals_inbox_lists_pending_run_and_review_controls(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        project = Project(workspace_id=agent.workspace_id, name="Inbox project")
        session.add(project)
        session.flush()
        issue = Issue(project_id=project.id, title="Need approval")
        session.add(issue)
        session.flush()
        run = Run(issue_id=issue.id, agent_id=agent.id, prompt="Review this plan", status=RunStatus.WAITING_APPROVAL)
        session.add(run)
        session.commit()

    response = TestClient(app).get("/approvals")

    assert response.status_code == 200
    assert "Approvals" in response.text
    assert "Need approval" in response.text
    assert "Review this plan" in response.text
    assert "Approve and run" in response.text
    assert "Reject" in response.text


def test_runtimes_have_their_own_page(local_db):
    seed_agent()
    response = TestClient(app).get("/runtimes")
    assert response.status_code == 200
    assert "Local Ollama" in response.text
    assert "Add runtime" in response.text


def test_runtime_form_saves_api_credentials_without_echoing_them(local_db):
    # "openai" direct-API used to be a catalog entry; it was removed because it
    # isn't a connectable provider in this environment (see runtime_catalog.py).
    # This test now exercises the same credential-save/non-echo behavior through
    # "openai_compatible", the still-supported generic entry, instead.
    client = TestClient(app)
    response = client.post(
        "/runtimes",
        data={
            "name": "OpenAI Primary",
            "type": "openai_compatible",
            "model": "gpt-5.6",
            "api_key": "test-secret-key",
            "base_url": "https://example.test/v1/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        runtime = session.scalar(select(Runtime).where(Runtime.name == "OpenAI Primary"))
        config = json.loads(runtime.config_json)
        assert config == {
            "provider_id": "openai_compatible",
            "api_key": "test-secret-key",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://example.test/v1",
        }

    page = client.get("/runtimes")
    assert page.status_code == 200
    assert "API key saved" in page.text
    assert "test-secret-key" not in page.text

def test_custom_provider_maps_to_openai_compatible_runtime(local_db):
    # "xai" used to be a dedicated catalog entry that quietly mapped down to the
    # generic openai_compatible runtime type; it was removed as non-connectable
    # in this environment. The behavior worth protecting - an arbitrary custom
    # provider still lands on RuntimeType.OPENAI_COMPATIBLE with its own base
    # URL and key - is exercised directly through "openai_compatible" here.
    client = TestClient(app)
    response = client.post(
        "/runtimes",
        data={
            "name": "Grok",
            "type": "openai_compatible",
            "model": "grok-4.6",
            "api_key": "xai-secret",
            "base_url": "https://api.x.ai/v1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        runtime = session.scalar(select(Runtime).where(Runtime.name == "Grok"))
        config = json.loads(runtime.config_json)
        assert runtime.type == RuntimeType.OPENAI_COMPATIBLE
        assert runtime.model == "grok-4.6"
        assert config["provider_id"] == "openai_compatible"
        assert config["base_url"] == "https://api.x.ai/v1"
        assert config["api_key_env"] == "OPENAI_API_KEY"


def test_runtime_page_has_scrollable_guided_dialog(local_db):
    response = TestClient(app).get("/runtimes")

    assert response.status_code == 200
    assert "OpenRouter" in response.text
    assert "Codex CLI" in response.text
    assert "Claude Code" in response.text
    assert 'id="runtime-model-info"' in response.text
    assert 'id="runtime-hardware"' in response.text

def test_runtime_can_be_edited_without_revealing_or_erasing_saved_key(local_db):
    client = TestClient(app)
    client.post(
        "/runtimes",
        data={"name": "Grok", "type": "openai_compatible", "model": "grok-4.6", "api_key": "saved-secret"},
    )
    with db.get_session(scoped=False) as session:
        runtime_id = session.scalar(select(Runtime).where(Runtime.name == "Grok")).id

    detail = client.get(f"/runtimes/{runtime_id}")
    assert detail.status_code == 200
    assert "Runtime configuration" in detail.text
    assert "saved-secret" not in detail.text

    response = client.post(
        f"/runtimes/{runtime_id}",
        data={
            "name": "Grok Research",
            "type": "openai_compatible",
            "model": "grok-4.20-reasoning",
            "api_key": "",
            "base_url": "",
            "command": "",
            "working_directory": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        runtime = session.get(Runtime, runtime_id)
        assert runtime.name == "Grok Research"
        assert runtime.model == "grok-4.20-reasoning"
        assert json.loads(runtime.config_json)["api_key"] == "saved-secret"


def test_unassigned_runtime_can_be_archived(local_db):
    client = TestClient(app)
    client.post("/runtimes", data={"name": "Temporary", "type": "ollama", "model": "qwen3:8b"})
    with db.get_session(scoped=False) as session:
        runtime_id = session.scalar(select(Runtime).where(Runtime.name == "Temporary")).id

    response = client.post(f"/runtimes/{runtime_id}/archive", follow_redirects=False)

    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(Runtime, runtime_id).archived is True
def test_agent_creation_saves_description_and_selected_skills(local_db):
    _, runtime_id, skill_id = seed_agent()
    client = TestClient(app)
    response = client.post(
        "/agents",
        data={
            "name": "Research Partner",
            "description": "Find evidence and summarize it.",
            "runtime_id": runtime_id,
            "instructions": "Use trustworthy sources.",
            "skill_ids": [skill_id],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        agent = session.scalar(select(Agent).where(Agent.name == "Research Partner"))
        assert agent.description == "Find evidence and summarize it."
        assert agent.instructions == "Use trustworthy sources."
        assert [skill.id for skill in agent.skills] == [skill_id]


def test_agent_builder_drafts_instructions_with_selected_runtime(local_db, mocker):
    _, runtime_id, _ = seed_agent()
    from hagent.adapters.base import RuntimeResult

    run = mocker.patch(
        "hagent.adapters.ollama.OllamaRuntime.run",
        return_value=RuntimeResult(output="You are a careful research assistant."),
    )
    response = TestClient(app).post(
        "/api/agents/draft",
        json={"runtime_id": runtime_id, "purpose": "Research market trends and cite sources."},
    )
    assert response.status_code == 200
    assert response.json()["instructions"] == "You are a careful research assistant."
    assert "Research market trends" in run.call_args.args[0]


def test_low_priority_cancelled_issues_fade_off_the_board_after_5_minutes(local_db):
    from datetime import datetime, timedelta, timezone

    from hagent.models import IssueStatus
    from hagent.web import CANCELLED_FADE_SECONDS

    with db.get_session(scoped=False) as session:
        workspace = db.get_or_create_default_workspace(session)
        project = Project(workspace_id=workspace.id, name="Fade project")
        session.add(project)
        session.flush()
        stale_noise = Issue(project_id=project.id, title="Stale noise", status=IssueStatus.CANCELLED, priority="low")
        fresh_noise = Issue(project_id=project.id, title="Fresh noise", status=IssueStatus.CANCELLED, priority="none")
        important = Issue(project_id=project.id, title="Actually important", status=IssueStatus.CANCELLED, priority="high")
        session.add_all([stale_noise, fresh_noise, important])
        session.commit()
        stale_noise.updated_at = datetime.now(timezone.utc) - timedelta(seconds=CANCELLED_FADE_SECONDS + 1)
        session.commit()
        project_id = project.id

    page = TestClient(app).get(f"/projects/{project_id}")

    assert "Stale noise" not in page.text
    assert "Fresh noise" in page.text
    assert "Actually important" in page.text

