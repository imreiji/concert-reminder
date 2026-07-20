"""The migration is a plain CREATE TABLE, so this only has to prove the table
lands with the right columns, round-trips, and reverses cleanly.

Note the settings monkeypatch: alembic/env.py resolves the URL from app
settings, NOT from cfg's `sqlalchemy.url`, so overriding the setting is what
actually guarantees the migration cannot touch the repo's app.db. Same shape as
tests/test_migration_legacy_anonymous_constraints.py.
"""
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
# Pin the target rather than using "head": this file tests ONE migration, and
# `downgrade -1` below must reverse THAT migration. Once a later revision lands
# on top (concert subscriptions did), "head" retargets and `downgrade -1` would
# reverse the wrong migration. Same lesson as test_migration_user_erasure.py.
MIGRATION_REVISION = "ce1a80bb66f4"  # ops_check_state: plain CREATE TABLE


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def test_ops_check_state_table_is_created(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    cfg = _alembic_config(monkeypatch, db)
    command.upgrade(cfg, MIGRATION_REVISION)

    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_check_state)")}
        assert cols == {
            "name", "ok", "changed_at", "last_notified_at", "pending_ok", "pending_since"
        }
        conn.execute("INSERT INTO ops_check_state (name, ok) VALUES ('backup', 0)")
        conn.commit()
        assert conn.execute("SELECT ok FROM ops_check_state").fetchone()[0] == 0
    finally:
        conn.close()


def test_downgrade_drops_the_table(tmp_path, monkeypatch):
    """The deploy runbook's rollback path depends on this reversing cleanly."""
    db = tmp_path / "t.db"
    cfg = _alembic_config(monkeypatch, db)
    command.upgrade(cfg, MIGRATION_REVISION)
    command.downgrade(cfg, "-1")

    conn = sqlite3.connect(db)
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "ops_check_state" not in tables
    finally:
        conn.close()
