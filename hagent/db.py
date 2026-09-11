"""Database engine/session setup and one-time default-workspace bootstrap."""

import os
import json
from pathlib import Path
from contextlib import contextmanager

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
            "avatar_path": "VARCHAR",
            "env_json": "TEXT NOT NULL DEFAULT '{}'",
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
        "chat_threads": {"workspace_id": "VARCHAR REFERENCES workspaces(id)"},
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
            session.info["workspace_id"] = workspace_id or get_active_workspace(session).id
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
