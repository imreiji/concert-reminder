"""Migration test: the concert_subscriptions and leg_opt_outs tables.

Same pattern as test_migration_round_outcomes.py / test_migration_user_erasure.py:
run the real alembic path against a scratch SQLite file. These are two brand-new
plain CREATE TABLEs (no legacy-schema fixture needed), so the test focuses on:
the tables and their columns landing on upgrade, downgrade dropping them cleanly
and upgrade restoring them, the (user, concert)/(user, day) unique constraints
rejecting duplicates, and -- with PRAGMA foreign_keys=ON -- deleting a user
cascading away both of that user's override rows.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "ce1a80bb66f4"  # head immediately before these tables
# Pin the target rather than using "head": this file tests ONE migration, and
# `downgrade -1` below must reverse THAT migration. Upgrading to "head" silently
# retargets both as soon as any later revision lands.
MIGRATION_REVISION = "cc82d6500300"  # concert subscriptions + leg opt-outs


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _seed_parents(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, tz_auto, is_editor, "
        "onboarding_step, created_at) VALUES (42, 'reiji', 'Asia/Tokyo', 1, 1, 5, "
        "'2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concerts (id, event_id, title, created_at) "
        "VALUES (1, 'c1', 'Hasunosora 5th', '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concert_days (id, concert_id, label, starts_at_utc, cancelled) "
        "VALUES (1, 1, 'Day 1', '2026-06-01 09:00:00', 0)"
    )
    con.commit()


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "concert_subscriptions" not in _table_names(con)
    assert "leg_opt_outs" not in _table_names(con)
    con.close()

    command.upgrade(cfg, MIGRATION_REVISION)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    _seed_parents(con)
    yield con, cfg, db_path
    con.close()


def test_tables_land_with_the_right_columns(migrated):
    con, _, _ = migrated
    names = _table_names(con)
    assert "concert_subscriptions" in names
    assert "leg_opt_outs" in names
    assert _columns(con, "concert_subscriptions") == {
        "id",
        "user_id",
        "concert_id",
        "state",
        "created_at",
    }
    assert _columns(con, "leg_opt_outs") == {
        "id",
        "user_id",
        "concert_day_id",
        "created_at",
    }


def test_downgrade_drops_them_and_upgrade_restores(migrated):
    con, cfg, db_path = migrated
    con.close()
    command.downgrade(cfg, "-1")

    con = sqlite3.connect(db_path)
    names = _table_names(con)
    assert "concert_subscriptions" not in names
    assert "leg_opt_outs" not in names
    con.close()

    command.upgrade(cfg, MIGRATION_REVISION)
    con = sqlite3.connect(db_path)
    names = _table_names(con)
    assert "concert_subscriptions" in names
    assert "leg_opt_outs" in names
    con.close()


def test_deleting_a_user_cascades_both_override_tables(migrated):
    con, _, _ = migrated
    con.execute(
        "INSERT INTO concert_subscriptions (user_id, concert_id, state, created_at) "
        "VALUES (42, 1, 'opted_out', '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO leg_opt_outs (user_id, concert_day_id, created_at) "
        "VALUES (42, 1, '2026-01-01 00:00:00')"
    )
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM concert_subscriptions").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM leg_opt_outs").fetchone()[0] == 1

    con.execute("DELETE FROM users WHERE discord_id = 42")
    con.commit()

    assert con.execute("SELECT COUNT(*) FROM concert_subscriptions").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM leg_opt_outs").fetchone()[0] == 0
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_unique_constraints_reject_duplicates(migrated):
    con, _, _ = migrated
    con.execute(
        "INSERT INTO concert_subscriptions (user_id, concert_id, state, created_at) "
        "VALUES (42, 1, 'subscribed', '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO leg_opt_outs (user_id, concert_day_id, created_at) "
        "VALUES (42, 1, '2026-01-01 00:00:00')"
    )
    con.commit()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO concert_subscriptions (user_id, concert_id, state, created_at) "
            "VALUES (42, 1, 'opted_out', '2026-01-02 00:00:00')"
        )
    con.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO leg_opt_outs (user_id, concert_day_id, created_at) "
            "VALUES (42, 1, '2026-01-02 00:00:00')"
        )
    con.rollback()
