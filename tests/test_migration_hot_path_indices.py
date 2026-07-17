"""Migration test: hot-path indices on FK columns.

Runs the real alembic upgrade path against a scratch SQLite file, same
pattern as test_migration_event_id.py: confirms the 4 new indices actually
exist after upgrading, since these were added purely via model
`index=True` + autogenerate (no data change to verify, but the DDL itself
is worth a regression guard).
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "61b32729019c"  # head immediately before these indices


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _index_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA index_list({table})").fetchall()}


def test_hot_path_indices_exist_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "ix_reminder_rules_concert_id" not in _index_names(con, "reminder_rules")
    assert "ix_rounds_concert_id" not in _index_names(con, "rounds")
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    assert "ix_concert_days_concert_id" in _index_names(con, "concert_days")
    assert "ix_concert_tags_tag_id" in _index_names(con, "concert_tags")
    assert "ix_reminder_rules_concert_id" in _index_names(con, "reminder_rules")
    assert "ix_reminder_rules_round_id" in _index_names(con, "reminder_rules")
    assert "ix_rounds_concert_id" in _index_names(con, "rounds")
    con.close()
