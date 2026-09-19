import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hagent import db as hagent_db
from hagent.models import Base
from hagent.tenancy import WorkspaceSession


@pytest.fixture(autouse=True)
def auth_db(monkeypatch):
    """Give every test its own empty accounts database, so the developer's real accounts never
    change test behaviour. Tests that exercise auth add users to it via this fixture."""
    import hagent.auth as auth

    monkeypatch.delenv("HAGENT_REQUIRE_AUTH", raising=False)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "_session", lambda: maker())
    auth.invalidate_caches()
    auth._failures.clear()
    yield maker
    auth.invalidate_caches()
    auth._failures.clear()


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def cli_db(tmp_path, monkeypatch):
    """Point the CLI (and the accounts check) at a throwaway database and workspace config."""
    import hagent.auth as auth

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(hagent_db, "engine", engine)
    monkeypatch.setattr(hagent_db, "SessionLocal", sessionmaker(bind=engine, class_=WorkspaceSession, expire_on_commit=False))
    monkeypatch.setattr(hagent_db, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(auth, "_session", lambda: hagent_db.SessionLocal())
    auth.invalidate_caches()
    return engine
