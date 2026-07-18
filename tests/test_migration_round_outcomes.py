"""Migration test: the round_outcomes table.

Same pattern as test_migration_concert_audit.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the table and its
unique index exist after upgrading.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "5ea945b713c4"  # head immediately before this table


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_round_outcomes_table_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "round_outcomes" not in _table_names(con)
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    assert "round_outcomes" in _table_names(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(round_outcomes)").fetchall()}
    assert columns == {"id", "user_id", "round_id", "outcome", "updated_at"}
    indexes = {row[1] for row in con.execute("PRAGMA index_list(round_outcomes)").fetchall()}
    assert "uq_round_outcome" in indexes
    con.close()
