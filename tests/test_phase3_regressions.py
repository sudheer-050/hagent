import json
import subprocess
import sys
import types
import zipfile

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, inspect
from sqlalchemy.orm import sessionmaker

from hagent import db
from hagent.cli import cli
from hagent.models import *
from hagent.tenancy import WorkspaceSession, ScopeError
from hagent.engine import run_issue, cancel_issue
from hagent.adapters.base import RuntimeResult
from hagent.skills import read_source


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False))
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")
    db.init_db()
    yield engine
    engine.dispose()


def seed():
    with db.get_session(scoped=False) as s:
        ws = Workspace(name="other")
        s.add(ws); s.flush()
        project = Project(workspace_id=ws.id, name="secret-project")
        runtime = Runtime(workspace_id=ws.id, name="rt", type=RuntimeType.OLLAMA, model="demo")
        s.add_all([project, runtime]); s.flush()
        agent = Agent(workspace_id=ws.id, runtime_id=runtime.id, name="agent")
        issue = Issue(project_id=project.id, title="secret-issue")
        chat = ChatThread(workspace_id=ws.id, title="secret-chat")
        s.add_all([agent, issue, chat]); s.commit()
        return ws.id, project.id, agent.id, issue.id, chat.id


def test_scoped_reads_and_foreign_key_writes(local_db):
    ws, project, agent, issue, chat = seed()
    with db.get_session() as s:
        assert s.get(Issue, issue) is None
        assert s.scalars(select(Project)).all() == []
        assert s.scalars(select(ChatThread)).all() == []
        s.add(Comment(issue_id=issue, body="intrusion"))
        with pytest.raises(ScopeError): s.commit()
    runner = CliRunner()
    for args in (["issue", "search", "secret"], ["chat", "history"], ["project", "list"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "secret" not in result.output
    result = runner.invoke(cli, ["issue", "metadata", "set", issue, "--key", "x", "--value", "bad"])
    assert result.exit_code != 0
    with db.get_session(scoped=False) as s:
        assert s.scalars(select(IssueMetadata)).all() == []


@pytest.mark.parametrize("fail", [False, True])
def test_cancellation_survives_late_model_result(local_db, monkeypatch, fail):
    ws, _, agent_id, issue_id, _ = seed()
    def finish(**kwargs):
        with db.get_session(workspace_id=ws) as other:
            cancel_issue(other, other.get(Issue, issue_id))
        if fail: raise RuntimeError("late failure")
        return RuntimeResult(output="late success")
    monkeypatch.setattr("hagent.adapters.ollama.OllamaRuntime.run", lambda self, **kwargs: finish(**kwargs))
    with db.get_session(workspace_id=ws) as s:
        issue = s.get(Issue, issue_id)
        run = run_issue(s, issue, s.get(Agent, agent_id))
        assert run.status == RunStatus.CANCELLED
        assert run.output is None
        assert run.error == "cancelled by user"
        assert issue.status == IssueStatus.CANCELLED


@pytest.mark.parametrize("name", ["../escape.txt", "C:/escape.txt", "..\\escape.txt", "/escape.txt"])
def test_skill_archive_traversal(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z: z.writestr(name, "unsafe")
    with pytest.raises(Exception, match="Unsafe skill filename"): read_source(str(archive))


def test_local_skill_only_and_refresh_rollback(local_db, tmp_path):
    with pytest.raises(Exception, match="local"): read_source("https://example.com/skill.zip")
    folder = tmp_path / "skill"; folder.mkdir()
    (folder / "SKILL.md").write_text("useful instruction")
    (folder / "helper.txt").write_text("helper content")
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "import", str(folder)])
    assert result.exit_code == 0, result.output
    skill_id = result.output.strip()
    (folder / "helper.txt").write_bytes(b"\xff")
    assert runner.invoke(cli, ["skill", "refresh", skill_id]).exit_code != 0
    with db.get_session() as s:
        skill = s.get(Skill, skill_id)
        assert len(skill.files) == 2
        assert skill.content == "useful instruction"


def test_real_phase2_database_migration(local_db):
    # Use the actual Phase 2 models, not a hand-written approximation.
    Base.metadata.drop_all(local_db)
    source = subprocess.check_output(["git", "show", "589f5e8:hagent/models.py"], text=True)
    legacy = types.ModuleType("phase2_models")
    sys.modules[legacy.__name__] = legacy
    exec(source, legacy.__dict__)
    legacy.Base.metadata.create_all(local_db)
    with sessionmaker(bind=local_db)() as s:
        ws = legacy.Workspace(name="default"); s.add(ws); s.flush()
        rt = legacy.Runtime(workspace_id=ws.id, name="old", type=legacy.RuntimeType.OLLAMA, model="x")
        s.add(rt); s.flush()
        a = legacy.Agent(workspace_id=ws.id, runtime_id=rt.id, name="old-agent"); s.add(a); s.flush()
        ap = legacy.Autopilot(workspace_id=ws.id, agent_id=a.id, name="old-auto"); s.add(ap); s.flush()
        trigger = legacy.AutopilotTrigger(autopilot_id=ap.id, cron_expression="* * * * *")
        s.add(trigger); s.commit(); trigger_id, ap_id = trigger.id, ap.id
    db.init_db(); db.init_db()
    with db.get_session() as s:
        assert s.get(AutopilotTrigger, trigger_id).type == TriggerType.CRON
        s.add(AutopilotTrigger(autopilot_id=ap_id, type=TriggerType.WEBHOOK, webhook_token="new", cron_expression=None))
        s.commit()
    assert next(c for c in inspect(local_db).get_columns("autopilot_triggers") if c["name"] == "cron_expression")["nullable"]
