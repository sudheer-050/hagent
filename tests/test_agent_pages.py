import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.models import Agent, ChatThread, Runtime, RuntimeType, Skill
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
    assert "New runtime" not in response.text


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
        assert agent.terminal_working_directory == str(tmp_path.resolve())


def test_skills_library_assigns_reusable_skill_to_any_agent(local_db):
    agent_id, _, skill_id = seed_agent()
    client = TestClient(app)

    library = client.get("/skills")
    assert library.status_code == 200
    assert "Skills library" in library.text
    assert "Available to agents" in library.text
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


def test_runtimes_have_their_own_page(local_db):
    seed_agent()
    response = TestClient(app).get("/runtimes")
    assert response.status_code == 200
    assert "Local Ollama" in response.text
    assert "Add runtime" in response.text


def test_runtime_form_saves_api_credentials_without_echoing_them(local_db):
    client = TestClient(app)
    response = client.post(
        "/runtimes",
        data={
            "name": "OpenAI Primary",
            "type": "openai",
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
            "provider_id": "openai",
            "api_key": "test-secret-key",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://example.test/v1",
        }

    page = client.get("/runtimes")
    assert page.status_code == 200
    assert "API key saved" in page.text
    assert "test-secret-key" not in page.text

def test_grok_provider_maps_to_openai_compatible_runtime(local_db):
    client = TestClient(app)
    response = client.post(
        "/runtimes",
        data={"name": "Grok", "type": "xai", "model": "grok-4.6", "api_key": "xai-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db.get_session(scoped=False) as session:
        runtime = session.scalar(select(Runtime).where(Runtime.name == "Grok"))
        config = json.loads(runtime.config_json)
        assert runtime.type == RuntimeType.OPENAI_COMPATIBLE
        assert runtime.model == "grok-4.6"
        assert config["provider_id"] == "xai"
        assert config["base_url"] == "https://api.x.ai/v1"
        assert config["api_key_env"] == "XAI_API_KEY"


def test_runtime_page_has_scrollable_guided_dialog(local_db):
    response = TestClient(app).get("/runtimes")

    assert response.status_code == 200
    assert "xAI Grok API" in response.text
    assert "Codex CLI" in response.text
    assert "Claude Code" in response.text
    assert 'id="runtime-model-info"' in response.text
    assert 'id="runtime-hardware"' in response.text

def test_runtime_can_be_edited_without_revealing_or_erasing_saved_key(local_db):
    client = TestClient(app)
    client.post(
        "/runtimes",
        data={"name": "Grok", "type": "xai", "model": "grok-4.6", "api_key": "saved-secret"},
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
            "type": "xai",
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


def test_chat_page_renders_conversation_and_agent_picker(local_db):
    agent_id, _, _ = seed_agent()
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        thread = ChatThread(workspace_id=agent.workspace_id, agent_id=agent.id, title="Release planning")
        session.add(thread)
        session.commit()
        thread_id = thread.id
    response = TestClient(app).get(f"/chat?thread_id={thread_id}")
    assert response.status_code == 200
    assert "Release planning" in response.text
    assert "Scout" in response.text
    assert "Message your agent" in response.text


def test_knowledge_upload_retrieval_and_agent_context(local_db, mocker):
    from hagent.adapters.base import RuntimeResult
    from hagent.engine import execute_agent

    agent_id, _, _ = seed_agent()
    mocker.patch("hagent.rag.embed_texts", side_effect=lambda base, texts, **kw: [[1.0, 0.0] for _ in texts])
    client = TestClient(app)
    created = client.post("/knowledge", data={"name": "Support guide", "embedding_provider": "ollama"}, follow_redirects=False)
    assert created.status_code == 303
    base_id = created.headers["location"].rsplit("/", 1)[-1]
    detail = client.get(f"/knowledge/{base_id}")
    assert detail.status_code == 200
    assert "Agent access" in detail.text
    assert "Test retrieval" in detail.text
    uploaded = client.post(
        f"/knowledge/{base_id}/documents",
        files={"file": ("passwords.txt", b"Password reset: open Settings, choose Security, then choose Reset password.", "text/plain")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    client.post(f"/knowledge/{base_id}/agents", data={"agent_ids": [agent_id]}, follow_redirects=False)
    results = client.get(f"/api/knowledge/{base_id}/search", params={"q": "How do I reset my password?"})
    assert results.status_code == 200
    assert results.json()["results"][0]["name"] == "passwords.txt"

    run = mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", return_value=RuntimeResult(output="Use Settings > Security."))
    with db.get_session(scoped=False) as session:
        agent = session.get(Agent, agent_id)
        execute_agent(agent, "How do I reset my password?")
    prompt = run.call_args.kwargs["prompt"]
    assert "Retrieved knowledge" in prompt
    assert "[S1] passwords.txt" in prompt


def test_rag_chunking_and_keyword_fallback(local_db, mocker):
    from hagent.rag import chunk_text, ingest_document, search_knowledge
    from hagent.models import KnowledgeBase

    assert chunk_text(" ") == []
    pieces = chunk_text("Sentence. " * 500, size=160, overlap=30)
    assert len(pieces) > 1
    assert all(len(piece) <= 161 for piece in pieces)

    mocker.patch("hagent.rag.embed_texts", side_effect=RuntimeError("offline"))
    with db.get_session(scoped=False) as session:
        from hagent.models import Workspace
        workspace = session.scalar(select(Workspace))
        base = KnowledgeBase(workspace_id=workspace.id, name="Fallback", embedding_provider="ollama", embedding_model="embeddinggemma")
        session.add(base)
        session.flush()
        document = ingest_document(session, base, "guide.txt", b"Rotate credentials every quarter using the security console.")
        assert document.status == "keyword_only"
        session.flush()
        results = search_knowledge(session, [base.id], "How should credentials rotate?", top_k=3)
        assert results
        assert results[0]["name"] == "guide.txt"


def test_local_folder_is_indexed_and_rescanned(local_db, mocker, tmp_path):
    from hagent.models import KnowledgeDocument, KnowledgeFolder
    from hagent.local_watch import sync_local_folder_once

    mocker.patch("hagent.rag.embed_texts", side_effect=lambda base, texts, **kw: [[1.0, 0.0] for _ in texts])
    folder = tmp_path / "work-notes"
    folder.mkdir()
    (folder / "plan.md").write_text("Launch checklist: prepare customer onboarding materials.", encoding="utf-8")
    hidden = folder / ".git"
    hidden.mkdir()
    (hidden / "private.txt").write_text("This should not be indexed.", encoding="utf-8")

    client = TestClient(app)
    created = client.post("/knowledge", data={"name": "Local files", "embedding_provider": "ollama"}, follow_redirects=False)
    assert created.status_code == 303
    base_id = created.headers["location"].rsplit("/", 1)[-1]
    first = client.post(f"/knowledge/{base_id}/folder", data={"folder": str(folder)}, follow_redirects=False)
    assert first.status_code == 303
    assert "folder_indexed=1" in first.headers["location"]
    detail = client.get(f"/knowledge/{base_id}")
    assert "Automatic folder watches" in detail.text
    assert "Watch and index folder" in detail.text
    results = client.get(f"/api/knowledge/{base_id}/search", params={"q": "customer onboarding"})
    assert results.json()["results"][0]["name"] == "plan.md"

    with db.get_session(scoped=False) as session:
        watch = session.scalar(select(KnowledgeFolder).where(KnowledgeFolder.knowledge_base_id == base_id))
        watch_id = watch.id
    (folder / "plan.md").rename(folder / "renamed.md")
    renamed = sync_local_folder_once(watch_id)
    assert renamed["indexed"] == 1 and renamed["removed"] == 1
    (folder / "renamed.md").write_text("Launch checklist: publish the updated security guide.", encoding="utf-8")
    changed = sync_local_folder_once(watch_id)
    assert changed["updated"] == 1
    (folder / "followup.txt").write_text("Follow-up: schedule a security review.", encoding="utf-8")
    added = sync_local_folder_once(watch_id)
    assert added["indexed"] == 1
    (folder / "renamed.md").unlink()
    deleted = sync_local_folder_once(watch_id)
    assert deleted["removed"] == 1
    with db.get_session(scoped=False) as session:
        docs = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == base_id)).all()
        assert len(docs) == 1
        assert docs[0].source_type == "local_file"
        assert docs[0].source_uri.endswith("followup.txt")
        watch = session.scalar(select(KnowledgeFolder).where(KnowledgeFolder.knowledge_base_id == base_id))
        watch_id = watch.id
    stopped = client.post(f"/knowledge/{base_id}/folders/{watch_id}/stop", follow_redirects=False)
    assert stopped.status_code == 303
    with db.get_session(scoped=False) as session:
        assert session.get(KnowledgeFolder, watch_id) is None


def test_background_folder_watcher_indexes_new_files(local_db, mocker, tmp_path):
    import time
    from hagent.local_watch import start_local_folder_watcher, stop_local_folder_watcher
    from hagent.models import KnowledgeDocument

    mocker.patch("hagent.rag.embed_texts", side_effect=lambda base, texts, **kw: [[1.0, 0.0] for _ in texts])
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "existing.txt").write_text("Original note.", encoding="utf-8")
    client = TestClient(app)
    created = client.post("/knowledge", data={"name": "Watched files", "embedding_provider": "ollama"}, follow_redirects=False)
    base_id = created.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/knowledge/{base_id}/folder", data={"folder": str(folder)}, follow_redirects=False)

    start_local_folder_watcher()
    try:
        (folder / "new.txt").write_text("A new file with the unique phrase watcher catches.", encoding="utf-8")
        deadline = time.monotonic() + 7
        indexed = False
        while time.monotonic() < deadline:
            with db.get_session(scoped=False) as session:
                found = session.scalar(select(KnowledgeDocument).where(
                    KnowledgeDocument.knowledge_base_id == base_id,
                    KnowledgeDocument.name == "new.txt",
                ))
                indexed = found is not None
            if indexed:
                break
            time.sleep(0.1)
        assert indexed, "background watcher did not index the new file"
    finally:
        stop_local_folder_watcher()
