"""Migration test: the onboarding_step column on users.

Same pattern as test_migration_round_outcomes.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the column exists
and that pre-existing rows backfill to 0 via server_default.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "5ea945b713c4"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_onboarding_step_column_exists_and_backfills_to_zero(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    columns_before = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "onboarding_step" not in columns_before
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, tz_auto, is_editor, created_at) "
        "VALUES (1, 'pre-existing', 'UTC', 1, 0, '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "onboarding_step" in columns
    value = con.execute("SELECT onboarding_step FROM users WHERE discord_id = 1").fetchone()[0]
    assert value == 0
    con.close()
