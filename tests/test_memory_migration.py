from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import hagent.db as db
from hagent.tenancy import WorkspaceSession


def test_memory_and_routing_schema_bootstrap_is_idempotent(tmp_path, monkeypatch):
    database = tmp_path / "migration.db"
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(
        bind=engine,
        class_=WorkspaceSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "config.json")

    db.init_db()
    db.init_db()

    inspector = inspect(engine)
    assert {
        "memories",
        "memory_revisions",
        "memory_sessions",
        "memory_events",
        "memory_settings",
        "routing_policies",
        "routing_decisions",
    } <= set(inspector.get_table_names())
    memory_indexes = {item["name"] for item in inspector.get_indexes("memories")}
    routing_indexes = {item["name"] for item in inspector.get_indexes("routing_policies")}
    assert {"ix_memories_scope", "ix_memories_hash"} <= memory_indexes
    assert "uq_routing_policy_workspace" in routing_indexes
