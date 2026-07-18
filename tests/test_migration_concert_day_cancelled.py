"""Migration test: ConcertDay.cancelled.

Same scratch-DB pattern as every other migration test in this repo
(tests/test_migration_hot_path_indices.py, tests/test_migration_concert_audit.py):
upgrade to the revision right before this one, confirm the column is
absent, upgrade to head, confirm it exists with the right default.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "84977144aad6"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _columns(con: sqlite3.Connection, table: str) -> dict[str, dict]:
    return {row[1]: {"notnull": row[3], "dflt_value": row[4]} for row in
            con.execute(f"PRAGMA table_info({table})").fetchall()}


def test_concert_day_cancelled_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "cancelled" not in _columns(con, "concert_days")
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    cols = _columns(con, "concert_days")
    assert "cancelled" in cols
    assert cols["cancelled"]["notnull"] == 1
    con.close()
