"""Migration test: the three author FKs become nullable + ondelete=SET NULL.

Same pattern as test_migration_concert_audit.py: run the real alembic path
against a scratch SQLite file. This one seeds rows BEFORE upgrading, so it
also proves the batch (table-rebuild) migration preserves existing data --
the classic way a SQLite batch migration goes wrong -- and that deleting a
user afterwards actually nulls the authors instead of raising.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "e8a1c9d2f7b5"  # head immediately before this change
# Pin the target rather than using "head": this file tests ONE migration, and
# `downgrade -1` below must reverse THAT migration. Upgrading to "head" silently
# retargets both as soon as any later revision lands.
MIGRATION_REVISION = "1384cadd692e"  # user erasure: author FKs -> SET NULL

AUTHORED = {
    "concerts": "created_by",
    "tags": "created_by",
    "concert_audit": "edited_by",
}


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _seed(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, tz_auto, is_editor, "
        "onboarding_step, created_at) VALUES (42, 'reiji', 'Asia/Tokyo', 1, 1, 5, "
        "'2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concerts (id, event_id, title, created_by, created_at) "
        "VALUES (1, 'c1', 'Hasunosora 5th', 42, '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO tags (id, name, kind, created_by, created_at) "
        "VALUES (1, 'Hasunosora', 'group', 42, '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concert_audit (id, concert_id, edited_by, edited_at_utc, changes) "
        "VALUES (1, 1, 42, '2026-01-01 00:00:00', '[]')"
    )
    con.commit()


def _author_fk(con: sqlite3.Connection, table: str) -> tuple[str, str]:
    """(on_delete, notnull) for the table's author column."""
    column = AUTHORED[table]
    on_delete = next(
        row[6] for row in con.execute(f"PRAGMA foreign_key_list({table})") if row[3] == column
    )
    notnull = next(
        row[3] for row in con.execute(f"PRAGMA table_info({table})") if row[1] == column
    )
    return on_delete, notnull


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    for table in AUTHORED:
        assert _author_fk(con, table) == ("NO ACTION", 1), f"{table} was already migrated"
    _seed(con)
    con.close()

    command.upgrade(cfg, MIGRATION_REVISION)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    yield con, cfg, db_path
    con.close()


def test_author_fks_are_nullable_set_null_after_upgrade(migrated):
    con, _, _ = migrated
    for table in AUTHORED:
        assert _author_fk(con, table) == ("SET NULL", 0), table


def test_existing_rows_survive_the_table_rebuild(migrated):
    con, _, _ = migrated
    for table, column in AUTHORED.items():
        assert con.execute(f"SELECT {column} FROM {table}").fetchall() == [(42,)], table


def test_deleting_a_user_now_anonymises_instead_of_raising(migrated):
    con, _, _ = migrated
    con.execute("DELETE FROM users WHERE discord_id = 42")
    con.commit()
    for table, column in AUTHORED.items():
        assert con.execute(f"SELECT {column} FROM {table}").fetchall() == [(None,)], table
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_downgrade_restores_not_null_and_keeps_rows(migrated):
    con, cfg, db_path = migrated
    con.close()
    command.downgrade(cfg, "-1")

    con = sqlite3.connect(db_path)
    for table, column in AUTHORED.items():
        assert _author_fk(con, table) == ("NO ACTION", 1), table
        assert con.execute(f"SELECT {column} FROM {table}").fetchall() == [(42,)], table
    con.close()
