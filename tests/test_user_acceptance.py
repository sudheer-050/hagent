import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.models import (
    Agent,
    Attachment,
    Autopilot,
    AutopilotTrigger,
    Base,
    ChatMessage,
    Comment,
    Issue,
    IssueMetadata,
    IssueStatus,
    IssueSubscriber,
    Label,
    Memory,
    MemorySetting,
    Project,
    Repo,
    RoutingPolicy,
    Runtime,
    RuntimeType,
    Skill,
    Squad,
    SquadMember,
    UserProfile,
    Workspace,
)
from hagent.tenancy import WorkspaceSession
from hagent.web import app


@pytest.fixture()
def acceptance_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'acceptance.db'}",
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


def seed_world():
    with db.get_session(scoped=False) as session:
        workspace = db.get_or_create_default_workspace(session)
        runtime = Runtime(
            workspace_id=workspace.id,
            name="Acceptance runtime",
            type=RuntimeType.OLLAMA,
            model="qwen3",
        )
        session.add(runtime)
        session.flush()
        agent = Agent(
            workspace_id=workspace.id,
            runtime_id=runtime.id,
            name="Acceptance agent",
        )
        project = Project(workspace_id=workspace.id, name="Acceptance project")
        label = Label(workspace_id=workspace.id, name="verified")
        session.add_all([agent, project, label])
        session.flush()
        issue = Issue(project_id=project.id, title="Acceptance issue")
        session.add(issue)
        session.commit()
        return {
            "workspace": workspace.id,
            "runtime": runtime.id,
            "agent": agent.id,
            "project": project.id,
            "label": label.id,
            "issue": issue.id,
        }


def test_every_displayed_destination_renders(acceptance_db, monkeypatch):
    world = seed_world()
    monkeypatch.setattr(
        "hagent.web.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    client = TestClient(app)
    paths = [
        "/", "/board", "/agents", f"/agents/{world['agent']}", "/chat",
        f"/chat/{world['agent']}", "/projects", f"/projects/{world['project']}",
        f"/issues/{world['issue']}", f"/issues/{world['issue']}/diff", "/approvals",
        "/repos", "/squads", "/skills", "/autopilots", "/autopilots/graph",
        "/settings", "/usage", "/profile", "/workspaces", "/runtimes",
        f"/runtimes/{world['runtime']}", "/memory", "/routing",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Internal Server Error" not in response.text, path
        assert "{{" not in response.text, path
        assert " ? Hagent" not in response.text, path

    assert client.get("/api/chat/unread-count").status_code == 200
    assert client.get("/api/runtime-models", params={"provider": "codex_cli"}).status_code == 200
    assert client.get("/api/memory/export").status_code == 200
    assert client.get("/api/search", params={"q": "Acceptance"}).status_code == 200


def test_issue_controls_persist_and_download(acceptance_db):
    world = seed_world()
    client = TestClient(app)

    assert client.post(
        "/issues",
        data={
            "project_id": world["project"],
            "title": "Created from the UI",
            "description": "Full form path",
            "assignee_agent_id": world["agent"],
        },
        follow_redirects=False,
    ).status_code == 303
    with db.get_session(scoped=False) as session:
        created = session.scalar(select(Issue).where(Issue.title == "Created from the UI"))
        created_id = created.id
        assert created.status == IssueStatus.TODO

    assert client.post(f"/issues/{created_id}/assign", data={"agent_id": ""}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/status", data={"status": "in_progress"}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/labels", data={"label_id": world["label"]}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/comments", data={"body": "Acceptance comment"}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/metadata", data={"key": "browser", "value": "checked"}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/subscribers", data={"name": "Owner"}, follow_redirects=False).status_code == 303
    assert client.post(f"/issues/{created_id}/reorder", data={"position": "7"}, follow_redirects=False).status_code == 303
    upload = client.post(
        f"/issues/{created_id}/attachments",
        files={"file": ("proof.txt", b"acceptance attachment", "text/plain")},
        follow_redirects=False,
    )
    assert upload.status_code == 303

    with db.get_session(scoped=False) as session:
        issue = session.get(Issue, created_id)
        assert issue.status == IssueStatus.IN_PROGRESS
        assert issue.assignee_agent_id is None
        assert issue.position == 7
        assert [label.name for label in issue.labels] == ["verified"]
        assert session.scalar(select(Comment).where(Comment.issue_id == created_id)).body == "Acceptance comment"
        assert session.scalar(select(IssueMetadata).where(IssueMetadata.issue_id == created_id)).value == "checked"
        assert session.scalar(select(IssueSubscriber).where(IssueSubscriber.issue_id == created_id)).name == "Owner"
        attachment = session.scalar(select(Attachment).where(Attachment.issue_id == created_id))
        attachment_id = attachment.id
    download = client.get(f"/attachments/{attachment_id}")
    assert download.status_code == 200
    assert download.content == b"acceptance attachment"

    with db.get_session(scoped=False) as session:
        issue = session.get(Issue, created_id)
        issue.status = IssueStatus.IN_REVIEW
        session.commit()
    assert client.post(f"/issues/{created_id}/review/approve", follow_redirects=False).status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(Issue, created_id).status == IssueStatus.DONE

    assert client.post(f"/issues/{world['issue']}/cancel", follow_redirects=False).status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(Issue, world["issue"]).status == IssueStatus.CANCELLED


def test_workspace_profile_and_catalog_crud(acceptance_db, monkeypatch):
    world = seed_world()
    client = TestClient(app)

    assert client.post("/profile", data={"name": "QA Owner", "email": "qa@example.test", "bio": "Acceptance"}, follow_redirects=False).status_code == 303
    assert client.post("/workspaces", data={"name": "QA workspace"}, follow_redirects=False).status_code == 303
    assert client.post("/projects", data={"name": "UI project", "description": "Created from Projects"}, follow_redirects=False).status_code == 303
    assert client.post("/repos", data={"name": "UI repo", "url": "https://example.test/repo.git"}, follow_redirects=False).status_code == 303
    assert client.post("/skills", data={"name": "UI skill", "description": "Reusable", "content": "Check it"}, follow_redirects=False).status_code == 303
    assert client.post("/squads", data={"name": "UI squad", "description": "Acceptance"}, follow_redirects=False).status_code == 303

    with db.get_session(scoped=False) as session:
        profile = session.scalar(select(UserProfile))
        workspace = session.scalar(select(Workspace).where(Workspace.name == "QA workspace"))
        repo = session.scalar(select(Repo).where(Repo.name == "UI repo"))
        skill = session.scalar(select(Skill).where(Skill.name == "UI skill"))
        squad = session.scalar(select(Squad).where(Squad.name == "UI squad"))
        assert profile.email == "qa@example.test"
        assert repo.url == "https://example.test/repo.git"
        workspace_id, skill_id, squad_id = workspace.id, skill.id, squad.id

    assert client.post(f"/skills/{skill_id}/agents", data={"agent_ids": world["agent"]}, follow_redirects=False).status_code == 303
    assert client.post(f"/squads/{squad_id}/members", data={"agent_id": world["agent"], "role": "reviewer"}, follow_redirects=False).status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(Agent, world["agent"]) in session.get(Skill, skill_id).agents
        assert session.scalar(select(SquadMember).where(SquadMember.squad_id == squad_id)).role == "reviewer"

    created_runtime = client.post(
        "/runtimes",
        data={"name": "Temporary runtime", "type": "ollama", "model": "qwen3:8b"},
        follow_redirects=False,
    )
    assert created_runtime.status_code == 303
    with db.get_session(scoped=False) as session:
        runtime_id = session.scalar(select(Runtime).where(Runtime.name == "Temporary runtime")).id
    assert client.post(
        f"/runtimes/{runtime_id}",
        data={"name": "Edited runtime", "type": "ollama", "model": "qwen3:14b"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(f"/runtimes/{runtime_id}/archive", follow_redirects=False).status_code == 303

    created_agent = client.post(
        "/agents",
        data={
            "name": "UI agent",
            "designation": "Acceptance",
            "runtime_id": world["runtime"],
            "description": "Created through the form",
            "instructions": "Verify every path",
            "skill_ids": world["label"],
        },
        follow_redirects=False,
    )
    # A cross-type ID must be rejected cleanly instead of creating corrupt relations.
    assert created_agent.status_code == 404

    created_agent = client.post(
        "/agents",
        data={
            "name": "UI agent",
            "designation": "Acceptance",
            "runtime_id": world["runtime"],
            "description": "Created through the form",
            "instructions": "Verify every path",
            "skill_ids": skill_id,
            "verifier_agent_id": world["agent"],
        },
        follow_redirects=False,
    )
    assert created_agent.status_code == 303
    with db.get_session(scoped=False) as session:
        ui_agent = session.scalar(select(Agent).where(Agent.name == "UI agent"))
        ui_agent_id = ui_agent.id
    assert client.post(
        f"/agents/{ui_agent_id}",
        data={
            "name": "UI agent edited",
            "runtime_id": world["runtime"],
            "instructions": "Updated",
            "environment_json": json.dumps({"MODE": "qa"}),
            "delegation_limit": "2",
            "verifier_agent_id": world["agent"],
        },
        follow_redirects=False,
    ).status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(Agent, ui_agent_id).verifier_agent_id == world["agent"]
    detail = client.get(f"/agents/{ui_agent_id}")
    assert 'name="verifier_agent_id"' in detail.text
    assert f'value="{world["agent"]}" selected' in detail.text

    switched = TestClient(app).post(f"/workspaces/{workspace_id}/switch", follow_redirects=False)
    assert switched.status_code == 303
    assert switched.cookies.get("workspace_id") == workspace_id


def test_automation_chat_memory_and_routing(acceptance_db, monkeypatch):
    world = seed_world()
    client = TestClient(app)

    assert client.post(
        "/autopilots",
        data={
            "name": "UI autopilot",
            "agent_id": world["agent"],
            "project_id": world["project"],
            "filter_status": "backlog",
        },
        follow_redirects=False,
    ).status_code == 303
    with db.get_session(scoped=False) as session:
        autopilot_id = session.scalar(select(Autopilot).where(Autopilot.name == "UI autopilot")).id
    monkeypatch.setattr("hagent.web.start_scheduler", lambda: object())
    monkeypatch.setattr("hagent.web.sync_scheduler_jobs", lambda scheduler: None)
    assert client.post(f"/autopilots/{autopilot_id}/triggers", data={"cron": "*/5 * * * *"}, follow_redirects=False).status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.scalar(select(AutopilotTrigger).where(AutopilotTrigger.autopilot_id == autopilot_id))

    class Result:
        id = "run-id"
        status = "completed"
        summary = "acceptance"

    monkeypatch.setattr("hagent.web.run_autopilot_once", lambda _autopilot_id: Result())
    assert client.post(f"/autopilots/{autopilot_id}/trigger-now", follow_redirects=False).status_code == 303

    monkeypatch.setattr("hagent.web._run_chat_turn", lambda *args: ("Acceptance reply", "session-1", None, None))
    assert client.post(
        f"/chat/{world['agent']}/messages",
        data={"message": "Hello from acceptance"},
        follow_redirects=False,
    ).status_code == 303
    with db.get_session(scoped=False) as session:
        assert [message.content for message in session.scalars(select(ChatMessage).order_by(ChatMessage.created_at)).all()] == [
            "Hello from acceptance", "Acceptance reply"
        ]

    assert client.post(
        "/memory",
        data={"content": "Acceptance memory", "category": "explicit", "sensitivity": "normal", "verified": "1"},
        follow_redirects=False,
    ).status_code == 303
    with db.get_session(scoped=False) as session:
        memory = session.scalar(select(Memory).where(Memory.content == "Acceptance memory"))
        memory_id = memory.id
    assert client.post(
        f"/memory/{memory_id}",
        data={"content": "Corrected memory", "category": "decision", "verification_status": "verified", "sensitivity": "personal"},
        follow_redirects=False,
    ).status_code == 303
    settings_response = client.post(
        "/memory/settings",
        data={
            "enabled": "1",
            "retrieval_limit": "5",
            "token_budget": "900",
            "retention_days": "30",
            "embedding_provider": "",
            "embedding_model": "",
            "embedding_base_url": "",
        },
        follow_redirects=False,
    )
    assert settings_response.status_code == 303, settings_response.text
    with db.get_session(scoped=False) as session:
        assert session.get(Memory, memory_id).content == "Corrected memory"
        settings = session.scalar(select(MemorySetting))
        assert settings.retrieval_limit == 5
        assert settings.token_budget == 900
    assert client.get("/api/memory/export").status_code == 200
    assert client.post(f"/memory/{memory_id}/forget", follow_redirects=False).status_code == 303

    assert client.post(
        "/routing/settings",
        data={"mode": "provider_fixed", "provider": "ollama", "max_effort": "high", "latency_preference": "balanced"},
        follow_redirects=False,
    ).status_code == 303
    preview = client.get("/api/routing/preview", params={"prompt": "Write and test code", "runtime_id": world["runtime"]})
    assert preview.status_code == 200
    assert preview.json()["runtime_id"] == world["runtime"]
    with db.get_session(scoped=False) as session:
        assert session.scalar(select(RoutingPolicy)).provider == "ollama"
