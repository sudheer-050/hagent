"""Database engine/session setup and one-time default-workspace bootstrap."""

import os
import json
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from hagent.models import Base, Workspace

DB_PATH = os.environ.get("HAGENT_DB_PATH", "hagent.db")
DEFAULT_WORKSPACE_NAME = "default"
CONFIG_PATH = Path(os.environ.get("HAGENT_CONFIG_PATH", str(Path.home() / ".hagent" / "config.json")))

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        get_or_create_default_workspace(session)


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
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
