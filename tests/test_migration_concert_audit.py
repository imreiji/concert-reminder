"""Migration test: the concert_audit table.

Same pattern as test_migration_hot_path_indices.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the table and its
FK-column index exist after upgrading (pure create_table, no data to
migrate).
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "5238d67b86e0"  # head immediately before this table


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_concert_audit_table_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "concert_audit" not in _table_names(con)
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    assert "concert_audit" in _table_names(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(concert_audit)").fetchall()}
    assert columns == {"id", "concert_id", "edited_by", "edited_at_utc", "changes"}
    indexes = {row[1] for row in con.execute("PRAGMA index_list(concert_audit)").fetchall()}
    assert "ix_concert_audit_concert_id" in indexes
    con.close()
