import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.models import Agent, Issue, IssueStatus, Project, Runtime, RuntimeType, Workspace
from hagent.tenancy import WorkspaceSession
from hagent.web import app


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False))
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    db.init_db()
    yield engine
    engine.dispose()


def seed(local_db):
    with db.get_session(scoped=False) as s:
        ws = db.get_or_create_default_workspace(s)
        project = Project(workspace_id=ws.id, name="Rocket Ship")
        runtime = Runtime(workspace_id=ws.id, name="local-ollama", type=RuntimeType.OLLAMA, model="qwen3")
        s.add_all([project, runtime]); s.flush()
        agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="Deploy Bot")
        s.add(agent); s.flush()
        issue = Issue(project_id=project.id, title="Fix the launch pad", status=IssueStatus.IN_PROGRESS)
        s.add(issue); s.commit()
        return project.id, agent.id, issue.id


def test_dashboard_shows_counts_and_attention(local_db):
    project_id, agent_id, issue_id = seed(local_db)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text
    assert "Fix the launch pad" in resp.text  # needs-attention: in_progress


def test_projects_list_still_works_standalone(local_db):
    seed(local_db)
    client = TestClient(app)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert "Rocket Ship" in resp.text


@pytest.mark.parametrize("query,expected", [
    ("rocket", "project"),
    ("deploy", "agent"),
    ("launch pad", "issue"),
])
def test_api_search_matches_each_entity_type(local_db, query, expected):
    seed(local_db)
    client = TestClient(app)
    resp = client.get("/api/search", params={"q": query})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["type"] == expected for r in results)


def test_api_search_empty_query_returns_no_results(local_db):
    seed(local_db)
    client = TestClient(app)
    resp = client.get("/api/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


def test_api_search_no_match_returns_empty_list(local_db):
    seed(local_db)
    client = TestClient(app)
    resp = client.get("/api/search", params={"q": "nonexistent-xyz"})
    assert resp.status_code == 200
    assert resp.json() == {"results": []}
