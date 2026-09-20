"""Database engine/session setup and one-time default-workspace bootstrap."""

import os
import json
import time
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

request_workspace = ContextVar("request_workspace", default=None)

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from hagent.models import Base, Workspace
from hagent.skill_icons import choose_skill_emoji
from hagent.tenancy import WorkspaceSession

# A bare relative default ("hagent.db") resolves against whatever directory the
# *caller* happens to be in when they invoke the CLI, not this package's own
# location - so running `hagent` from anywhere other than the repo root created
# (or tried to create) an unrelated hagent.db there instead, failing outright
# in a read-only or unwritable directory. Anchor to this package's own
# checkout instead: for the common case (repo root == cwd) this resolves to
# the exact same file as before, so it doesn't move or duplicate anyone's
# existing database.
DB_PATH = os.environ.get("HAGENT_DB_PATH", str(Path(__file__).resolve().parent.parent / "hagent.db"))
DEFAULT_WORKSPACE_NAME = "default"
CONFIG_PATH = Path(os.environ.get("HAGENT_CONFIG_PATH", str(Path.home() / ".hagent" / "config.json")))

def tune_sqlite_connection(dbapi_connection) -> None:
    """Fast, still crash-safe SQLite settings, applied to every new connection.

    WAL with synchronous=NORMAL makes a commit about 600x cheaper than the default rollback
    journal with a full fsync (6 ms -> 0.01 ms on this machine), and lets readers (the dashboard,
    the CLI) keep working while a run is writing. The trade-off: after an operating-system crash
    or power loss the last few committed transactions can be lost; the database itself stays
    consistent. Set HAGENT_SQLITE_SAFE=1 to keep SQLite's stricter defaults.
    """
    if os.environ.get("HAGENT_SQLITE_SAFE", "").lower() in {"1", "true", "yes"}:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # wait for a competing writer instead of failing at once
    finally:
        cursor.close()


engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
event.listen(engine, "connect", lambda dbapi_connection, _record: tune_sqlite_connection(dbapi_connection))
SessionLocal = sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False)


_RETRYABLE_INIT_ERRORS = ("already exists", "duplicate column", "database is locked")


def init_db() -> None:
    # Two processes starting at once (the server and a CLI command) can both try to create the
    # same table or add the same column; the loser retries and finds the work already done.
    for attempt in range(5):
        try:
            Base.metadata.create_all(engine)
            _ensure_schema()
            break
        except OperationalError as exc:
            if attempt == 4 or not any(message in str(exc).lower() for message in _RETRYABLE_INIT_ERRORS):
                raise
            time.sleep(0.2 * (attempt + 1))
    with SessionLocal() as session:
        workspace = get_or_create_default_workspace(session)
        session.commit()


def _ensure_schema() -> None:
    """Apply additive migrations for databases created by earlier Hagent phases."""
    additions = {
        "runtimes": {
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "health_status": "VARCHAR NOT NULL DEFAULT 'unknown'",
            "health_detail": "TEXT NOT NULL DEFAULT ''",
            "last_health_check_at": "DATETIME",
        },
        "agents": {
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "designation": "VARCHAR NOT NULL DEFAULT ''",
            "description": "TEXT NOT NULL DEFAULT ''",
            "avatar_path": "VARCHAR",
            "env_json": "TEXT NOT NULL DEFAULT '{}'",
            "backup_runtime_id": "VARCHAR",
            "failback_runtime_id": "VARCHAR",
            "terminal_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "terminal_working_directory": "VARCHAR",
            "require_run_approval": "BOOLEAN NOT NULL DEFAULT 0",
            "delegation_limit": "INTEGER NOT NULL DEFAULT 8",
            "verifier_agent_id": "VARCHAR",
            "sandbox_image": "VARCHAR",
            "max_concurrent_tasks": "INTEGER NOT NULL DEFAULT 1",
        },
        "runs": {
            "token_estimate": "INTEGER NOT NULL DEFAULT 0",
            "transcript_json": "TEXT NOT NULL DEFAULT '{}'",
            "session_id": "VARCHAR",
        },
        "skills": {
            "source_url": "VARCHAR",
            "emoji": "VARCHAR NOT NULL DEFAULT ''",
            "last_used_at": "DATETIME",
            "lessons_json": "TEXT NOT NULL DEFAULT '[]'",
            "improvement_count": "INTEGER NOT NULL DEFAULT 0",
            "improved_at": "DATETIME",
        },
        "autopilot_triggers": {
            "enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "type": "VARCHAR NOT NULL DEFAULT 'CRON'",
            "webhook_token": "VARCHAR",
            "last_run_at": "DATETIME",
            "timezone": "VARCHAR",
            "label": "VARCHAR NOT NULL DEFAULT ''",
        },
        "repos": {"local_path": "VARCHAR"},
        "projects": {"repo_id": "VARCHAR"},
        "squads": {"archived": "BOOLEAN NOT NULL DEFAULT 0"},
        "issues": {
            "priority": "VARCHAR NOT NULL DEFAULT 'none'",
            "start_date": "VARCHAR",
            "due_date": "VARCHAR",
            "stage": "INTEGER",
            "number": "INTEGER",
        },
        "workspaces": {"issue_prefix": "VARCHAR NOT NULL DEFAULT ''"},
        "comments": {"parent_comment_id": "VARCHAR", "resolved": "BOOLEAN NOT NULL DEFAULT 0"},
        "autopilots": {
            "mode": "VARCHAR NOT NULL DEFAULT 'filter'",
            "description": "TEXT NOT NULL DEFAULT ''",
            "issue_title_template": "VARCHAR NOT NULL DEFAULT ''",
        },
        # chat_threads/chat_messages predate the general chat feature (an earlier,
        # unwired phase left them behind with title/author/body columns and real
        # conversation history) - add the new columns and backfill from the old
        # ones below rather than losing that history.
        "chat_threads": {
            "session_id": "VARCHAR",
            "unread": "BOOLEAN NOT NULL DEFAULT 0",
            "updated_at": "DATETIME",
        },
        "chat_messages": {
            "role": "VARCHAR",
            "content": "TEXT",
        },
    }
    additions['runs'].update({'input_tokens': 'INTEGER', 'output_tokens': 'INTEGER', 'owner_pid': 'INTEGER', 'owner_started': 'FLOAT', 'resumed_from': 'VARCHAR'})
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))
        skill_rows = connection.execute(text('SELECT id, workspace_id, name, description, content, emoji FROM skills')).all()
        used_by_workspace = {}
        for skill_id, workspace_id, name, description, content, emoji in skill_rows:
            used = used_by_workspace.setdefault(workspace_id, set())
            if not emoji or emoji in used:
                chosen = choose_skill_emoji(name, description, content, used)
                connection.execute(text('UPDATE skills SET emoji=:emoji WHERE id=:id'), {'emoji': chosen, 'id': skill_id})
                emoji = chosen
            used.add(emoji)
        # Phase 2 required cron_expression. SQLite needs a table rebuild to
        # relax that constraint; retain every trigger ID and timestamp.
        cron = next(c for c in inspector.get_columns("autopilot_triggers") if c["name"] == "cron_expression")
        if not cron["nullable"]:
            connection.execute(text("""CREATE TABLE autopilot_triggers_new (
                id VARCHAR PRIMARY KEY, autopilot_id VARCHAR NOT NULL REFERENCES autopilots(id),
                cron_expression VARCHAR, type VARCHAR NOT NULL DEFAULT 'CRON',
                webhook_token VARCHAR UNIQUE, last_run_at DATETIME,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                timezone VARCHAR, label VARCHAR NOT NULL DEFAULT '')"""))
            connection.execute(text("""INSERT INTO autopilot_triggers_new
                (id, autopilot_id, cron_expression, type, webhook_token, last_run_at, enabled, timezone, label)
                SELECT id, autopilot_id, cron_expression, type, webhook_token, last_run_at, enabled, timezone, label
                FROM autopilot_triggers"""))
            connection.execute(text("DROP TABLE autopilot_triggers"))
            connection.execute(text("ALTER TABLE autopilot_triggers_new RENAME TO autopilot_triggers"))
        connection.execute(text("UPDATE attachments SET issue_id=(SELECT issue_id FROM comments WHERE comments.id=attachments.comment_id) WHERE issue_id IS NULL AND comment_id IS NOT NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_webhook ON autopilot_triggers(webhook_token)"))
        for index, table, column in (
            ("ix_runs_status", "runs", "status"),
            ("ix_runs_issue_id", "runs", "issue_id"),
            ("ix_runs_agent_id", "runs", "agent_id"),
            ("ix_issues_project_id", "issues", "project_id"),
            ("ix_issues_parent_issue_id", "issues", "parent_issue_id"),
            ("ix_timeline_issue_id", "timeline_events", "issue_id"),
            ("ix_comments_issue_id", "comments", "issue_id"),
        ):
            connection.execute(text(f"CREATE INDEX IF NOT EXISTS {index} ON {table}({column})"))
        connection.execute(text("""UPDATE issues SET number = (
            SELECT n FROM (SELECT i.id AS id, ROW_NUMBER() OVER (PARTITION BY p.workspace_id ORDER BY i.created_at, i.id) AS n
                           FROM issues i JOIN projects p ON p.id = i.project_id) ranked
            WHERE ranked.id = issues.id) WHERE number IS NULL"""))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_scope ON memories(workspace_id, user_id, project_id, status)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_hash ON memories(workspace_id, user_id, normalized_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_memory_sessions_scope ON memory_sessions(workspace_id, user_id, project_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_settings_scope ON memory_settings(workspace_id, user_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_routing_policy_scope ON routing_policies(workspace_id, project_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_routing_policy_workspace ON routing_policies(workspace_id) WHERE project_id IS NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_routing_decisions_scope ON routing_decisions(workspace_id, project_id, created_at)"))
        # Both tables still carry their old NOT NULL title/author/body columns
        # (from the earlier, unwired chat phase); SQLite can't drop a column's
        # NOT NULL in place, so backfill role/content from author/body and
        # rebuild onto the current model shape. Guarded so re-runs after the
        # rebuild (once the legacy columns are gone) are no-ops.
        if "title" in {c["name"] for c in inspector.get_columns("chat_threads")}:
            connection.execute(text("UPDATE chat_threads SET updated_at = created_at WHERE updated_at IS NULL"))
            connection.execute(text("""CREATE TABLE chat_threads_new (
                id VARCHAR PRIMARY KEY, workspace_id VARCHAR NOT NULL REFERENCES workspaces(id),
                agent_id VARCHAR NOT NULL REFERENCES agents(id), session_id VARCHAR,
                unread BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"""))
            connection.execute(text("""INSERT INTO chat_threads_new
                (id, workspace_id, agent_id, session_id, unread, created_at, updated_at)
                SELECT id, workspace_id, agent_id, session_id, unread, created_at, updated_at FROM chat_threads"""))
            connection.execute(text("DROP TABLE chat_threads"))
            connection.execute(text("ALTER TABLE chat_threads_new RENAME TO chat_threads"))
        if "author" in {c["name"] for c in inspector.get_columns("chat_messages")}:
            connection.execute(text("UPDATE chat_messages SET role = (CASE WHEN author = 'you' THEN 'user' ELSE 'agent' END), content = body WHERE role IS NULL"))
            connection.execute(text("""CREATE TABLE chat_messages_new (
                id VARCHAR PRIMARY KEY, thread_id VARCHAR NOT NULL REFERENCES chat_threads(id),
                role VARCHAR NOT NULL, content TEXT NOT NULL, created_at DATETIME NOT NULL)"""))
            connection.execute(text("""INSERT INTO chat_messages_new (id, thread_id, role, content, created_at)
                SELECT id, thread_id, role, content, created_at FROM chat_messages"""))
            connection.execute(text("DROP TABLE chat_messages"))
            connection.execute(text("ALTER TABLE chat_messages_new RENAME TO chat_messages"))

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
