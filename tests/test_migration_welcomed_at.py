"""Migration test: the welcomed_at column on users.

Backfill rule: every PRE-EXISTING row is stamped as already welcomed (from
its created_at), because at migration time a real pre-wizard web user and a
bot-first bare row are indistinguishable (both onboarding_step 0) and
re-wizarding a long-time user is the worse failure. Only rows created AFTER
this migration can be NULL, which is what makes NULL mean "the wizard has
genuinely never finished for this account".
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "d446e6c0a3e6"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_welcomed_at_column_exists_and_backfills_from_created_at(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    columns_before = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "welcomed_at" not in columns_before
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, tz_auto, is_editor, created_at) "
        "VALUES (1, 'pre-existing', 'UTC', 1, 0, '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "welcomed_at" in columns
    welcomed_at, created_at = con.execute(
        "SELECT welcomed_at, created_at FROM users WHERE discord_id = 1"
    ).fetchone()
    # Grandfathered as already welcomed: non-NULL, and the same instant the
    # row was created (copied from the column the same TypeDecorator wrote).
    assert welcomed_at is not None
    assert welcomed_at == created_at
    con.close()
