import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hagent.models import Base, Issue, Project, Workspace
from hagent.tenancy import WorkspaceSession


def _seed(engine):
    Session = sessionmaker(bind=engine, class_=WorkspaceSession)
    with Session() as s:
        ids = {}
        for name in ("a", "b"):
            ws = Workspace(name=name)
            s.add(ws)
            s.flush()
            project = Project(workspace_id=ws.id, name=f"proj-{name}")
            s.add(project)
            s.flush()
            s.add(Issue(project_id=project.id, title=f"issue-{name}"))
            ids[name] = ws.id
        s.commit()
    return Session, ids


def _titles(Session, workspace_id):
    with Session() as s:
        s.info["workspace_id"] = workspace_id
        return sorted(s.scalars(select(Issue.title)).all())


def test_reads_are_scoped_to_the_active_workspace_across_repeated_queries():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session, ids = _seed(engine)

    # Alternate workspaces so any cached scoping options are reused both ways.
    for _ in range(3):
        assert _titles(Session, ids["a"]) == ["issue-a"]
        assert _titles(Session, ids["b"]) == ["issue-b"]


def test_unscoped_session_still_sees_everything():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session, _ = _seed(engine)

    with Session() as s:
        assert sorted(s.scalars(select(Issue.title)).all()) == ["issue-a", "issue-b"]


# --- SQLite tuning (lives here to avoid another file for two tiny tests) ---

def test_sqlite_connections_get_wal_and_a_busy_timeout(tmp_path, monkeypatch):
    import sqlite3

    from hagent.db import tune_sqlite_connection

    monkeypatch.delenv("HAGENT_SQLITE_SAFE", raising=False)
    connection = sqlite3.connect(tmp_path / "t.db")
    tune_sqlite_connection(connection)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_sqlite_tuning_can_be_switched_off(tmp_path, monkeypatch):
    import sqlite3

    from hagent.db import tune_sqlite_connection

    monkeypatch.setenv("HAGENT_SQLITE_SAFE", "1")
    connection = sqlite3.connect(tmp_path / "t.db")
    tune_sqlite_connection(connection)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


# --- concurrent start-up ---

def test_init_db_survives_another_process_creating_tables_first(mocker):
    from sqlalchemy.exc import OperationalError

    from hagent import db

    real = db.Base.metadata.create_all
    calls = {"n": 0}

    def flaky(bind, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("CREATE TABLE users (...)", {}, Exception("table users already exists"))
        return real(bind, *a, **kw)

    mocker.patch.object(db.Base.metadata, "create_all", side_effect=flaky)
    mocker.patch("hagent.db.time.sleep")
    mocker.patch("hagent.db._ensure_schema")
    mocker.patch("hagent.db.get_or_create_default_workspace")

    db.init_db()

    assert calls["n"] == 2  # retried once, then succeeded


def test_init_db_does_not_hide_real_errors(mocker):
    from sqlalchemy.exc import OperationalError

    from hagent import db

    mocker.patch.object(db.Base.metadata, "create_all", side_effect=OperationalError("x", {}, Exception("disk I/O error")))

    with pytest.raises(OperationalError):
        db.init_db()
