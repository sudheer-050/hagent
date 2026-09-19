from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import hagent.web as web
from hagent.memory import MemoryScope, MemoryService
from hagent.models import Base, Project, UserProfile, Workspace


def test_memory_api_rejects_cross_project_mutation(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    workspace = Workspace(name="Workspace")
    profile = UserProfile(name="Owner")
    session.add_all([workspace, profile])
    session.flush()
    project_a = Project(workspace_id=workspace.id, name="A")
    project_b = Project(workspace_id=workspace.id, name="B")
    session.add_all([project_a, project_b])
    session.commit()
    memory = MemoryService(
        session,
        MemoryScope(workspace.id, profile.id, project_b.id, provider="web"),
    ).remember("Project B decision", category="decision")

    @contextmanager
    def test_session(*_args, **_kwargs):
        yield session

    monkeypatch.setattr(web, "get_session", test_session)
    monkeypatch.setattr(web, "get_active_workspace", lambda _session: workspace)
    client = TestClient(web.app)
    response = client.post(
        f"/memory/{memory['id']}",
        data={
            "project_id": project_a.id,
            "content": "unauthorized correction",
            "category": "decision",
            "verification_status": "verified",
            "sensitivity": "normal",
        },
        follow_redirects=False,
    )
    assert response.status_code == 404

    listing = client.get("/api/memory", params={"project_id": project_a.id})
    assert listing.status_code == 200
    assert listing.json()["memories"] == []
    session.close()
