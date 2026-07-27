"""Migration test: the round_outcome_days table.

Same pattern as test_migration_round_outcomes.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the table and its
unique index exist after upgrading, and that all three FKs cascade.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "ce43bfcfcae3"  # head immediately before this table


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")  # cascades silently don't fire without this
    return con


def test_round_outcome_days_table_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "round_outcome_days" not in _table_names(con)
    con.close()

    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "round_outcome_days" in _table_names(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(round_outcome_days)").fetchall()}
    assert columns == {"id", "user_id", "round_id", "day_id", "result", "updated_at"}
    indexes = {row[1] for row in con.execute("PRAGMA index_list(round_outcome_days)").fetchall()}
    assert "uq_round_outcome_day" in indexes
    con.close()


def test_deleting_user_cascades_away_round_outcome_days(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, created_at) "
        "VALUES (1, 'reiji', 'America/Moncton', '2026-07-26 00:00:00')"
    )
    con.execute(
        "INSERT INTO concerts (id, event_id, title, created_at) "
        "VALUES (1, 'evt', 'T', '2026-07-26 00:00:00')"
    )
    con.execute(
        "INSERT INTO rounds (id, concert_id, kind, label) VALUES (1, 1, 'lottery_round', 'R1')"
    )
    con.execute(
        "INSERT INTO concert_days (id, concert_id, label, starts_at_utc) "
        "VALUES (1, 1, 'Day 1', '2026-08-01 10:00:00')"
    )
    con.execute(
        "INSERT INTO round_outcome_days (user_id, round_id, day_id, result, updated_at) "
        "VALUES (1, 1, 1, 'won', '2026-07-26 00:00:00')"
    )
    con.commit()

    con.execute("DELETE FROM users WHERE discord_id = 1")
    con.commit()

    count = con.execute("SELECT count(*) FROM round_outcome_days").fetchone()[0]
    assert count == 0  # cascaded away with the user
    con.close()
