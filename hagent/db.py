"""Database engine/session setup and one-time default-workspace bootstrap."""

import os
import json
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

request_workspace = ContextVar("request_workspace", default=None)

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from hagent.models import Base, Workspace
from hagent.tenancy import WorkspaceSession

DB_PATH = os.environ.get("HAGENT_DB_PATH", "hagent.db")
DEFAULT_WORKSPACE_NAME = "default"
CONFIG_PATH = Path(os.environ.get("HAGENT_CONFIG_PATH", str(Path.home() / ".hagent" / "config.json")))

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_schema()
    with SessionLocal() as session:
        workspace = get_or_create_default_workspace(session)
        session.execute(text("UPDATE chat_threads SET workspace_id=:ws WHERE workspace_id IS NULL"), {"ws": workspace.id})
        session.commit()


def _ensure_schema() -> None:
    """Apply additive migrations for databases created by earlier Hagent phases."""
    additions = {
        "runtimes": {"archived": "BOOLEAN NOT NULL DEFAULT 0"},
        "agents": {
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "description": "TEXT NOT NULL DEFAULT ''",
            "avatar_path": "VARCHAR",
            "env_json": "TEXT NOT NULL DEFAULT '{}'",
            "backup_runtime_id": "VARCHAR",
            "terminal_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "terminal_working_directory": "VARCHAR",
        },
        "runs": {
            "token_estimate": "INTEGER NOT NULL DEFAULT 0",
            "transcript_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "skills": {"source_url": "VARCHAR"},
        "autopilot_triggers": {
            "enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "type": "VARCHAR NOT NULL DEFAULT 'CRON'",
            "webhook_token": "VARCHAR",
            "last_run_at": "DATETIME",
        },
        "chat_threads": {
            "workspace_id": "VARCHAR REFERENCES workspaces(id)",
            "agent_id": "VARCHAR REFERENCES agents(id)",
        },
        "repos": {"local_path": "VARCHAR"},
        "squads": {"archived": "BOOLEAN NOT NULL DEFAULT 0"},
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))
        # Phase 2 required cron_expression. SQLite needs a table rebuild to
        # relax that constraint; retain every trigger ID and timestamp.
        cron = next(c for c in inspector.get_columns("autopilot_triggers") if c["name"] == "cron_expression")
        if not cron["nullable"]:
            connection.execute(text("""CREATE TABLE autopilot_triggers_new (
                id VARCHAR PRIMARY KEY, autopilot_id VARCHAR NOT NULL REFERENCES autopilots(id),
                cron_expression VARCHAR, type VARCHAR NOT NULL DEFAULT 'CRON',
                webhook_token VARCHAR UNIQUE, last_run_at DATETIME,
                enabled BOOLEAN NOT NULL DEFAULT 1)"""))
            connection.execute(text("""INSERT INTO autopilot_triggers_new
                SELECT id, autopilot_id, cron_expression, type, webhook_token, last_run_at, enabled
                FROM autopilot_triggers"""))
            connection.execute(text("DROP TABLE autopilot_triggers"))
            connection.execute(text("ALTER TABLE autopilot_triggers_new RENAME TO autopilot_triggers"))
        connection.execute(text("UPDATE attachments SET issue_id=(SELECT issue_id FROM comments WHERE comments.id=attachments.comment_id) WHERE issue_id IS NULL AND comment_id IS NOT NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_webhook ON autopilot_triggers(webhook_token)"))

def get_or_create_default_workspace(session: Session) -> Workspace:
    workspace = session.scalar(select(Workspace).where(Workspace.name == DEFAULT_WORKSPACE_NAME))
    if workspace is None:
        workspace = Workspace(name=DEFAULT_WORKSPACE_NAME)
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
    return workspace


def get_active_workspace(session: Session) -> Workspace:
    """Return the selected workspace, falling back to the default workspace."""
    if session.info.get("workspace_id"):
        return session.get(Workspace, session.info["workspace_id"])
    workspace_id = None
    try:
        if CONFIG_PATH.exists():
            workspace_id = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("workspace_id")
    except (OSError, ValueError):
        workspace_id = None
    workspace = session.get(Workspace, workspace_id) if workspace_id else None
    return workspace or get_or_create_default_workspace(session)


def set_active_workspace(workspace_id: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"workspace_id": workspace_id}, indent=2), encoding="utf-8")


@contextmanager
def get_session(*, scoped=True, workspace_id=None):
    session = SessionLocal()
    try:
        if scoped:
            session.info["workspace_id"] = workspace_id or request_workspace.get() or get_active_workspace(session).id
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
