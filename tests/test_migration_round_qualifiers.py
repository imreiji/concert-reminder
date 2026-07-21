"""Migration test: the round_qualifiers association table.

Same pattern as test_migration_round_outcomes.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the table, its
columns, its uniqueness on the pair, and its two ON DELETE CASCADE foreign
keys behave. Downgrade round-trips too.

This table is a plain CREATE TABLE (a brand-new table, not an ALTER of an
existing one), so it needs no batch/naming-convention treatment.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "08aa8b07ad1b"  # head immediately before this table


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


def _link(con: sqlite3.Connection, upgrade_id: int, qualifying_id: int) -> None:
    con.execute(
        "INSERT INTO round_qualifiers (upgrade_round_id, qualifying_round_id) VALUES (?, ?)",
        (upgrade_id, qualifying_id),
    )


def _seed_concert_and_rounds(con: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert one concert and three rounds (an upgrade + two qualifiers).

    Returns (upgrade_id, q1_id, q2_id)."""
    con.execute(
        "INSERT INTO concerts (id, event_id, title, created_at) "
        "VALUES (1, 'evt', 'T', '2026-07-19 00:00:00')"
    )
    rounds = [(10, "upgrade", "Up"), (11, "lottery_round", "R1"), (12, "lottery_round", "R2")]
    for rid, kind, label in rounds:
        con.execute(
            "INSERT INTO rounds (id, concert_id, kind, label) VALUES (?, 1, ?, ?)",
            (rid, kind, label),
        )
    return 10, 11, 12


def test_round_qualifiers_table_shape_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "round_qualifiers" not in _table_names(con)
    con.close()

    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "round_qualifiers" in _table_names(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(round_qualifiers)").fetchall()}
    assert columns == {"upgrade_round_id", "qualifying_round_id"}
    con.close()


def test_round_qualifiers_downgrade_round_trips(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "round_qualifiers" in _table_names(con)
    con.close()

    # Target the fixed revision, not a head-relative "-1": this migration
    # is no longer guaranteed to be head once later migrations land on top
    # of it (e.g. users.language), and "-1" from head would undo those
    # instead.
    command.downgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "round_qualifiers" not in _table_names(con)
    con.close()

    # And it re-applies cleanly (no leftover state).
    command.upgrade(cfg, "head")
    con = _connect(db_path)
    assert "round_qualifiers" in _table_names(con)
    con.close()


def test_round_qualifiers_rejects_duplicate_pair(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    _seed_concert_and_rounds(con)
    ins = "INSERT INTO round_qualifiers (upgrade_round_id, qualifying_round_id) VALUES (10, 11)"
    con.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(ins)
    con.close()


def test_deleting_qualifying_round_cascades_away_its_links(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    up, q1, q2 = _seed_concert_and_rounds(con)
    _link(con, up, q1)
    _link(con, up, q2)
    con.commit()

    con.execute("DELETE FROM rounds WHERE id = ?", (q1,))
    con.commit()
    remaining = con.execute(
        "SELECT qualifying_round_id FROM round_qualifiers"
    ).fetchall()
    assert remaining == [(q2,)]  # only the q1 link cascaded away
    con.close()


def test_deleting_upgrade_round_cascades_away_its_links(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    up, q1, q2 = _seed_concert_and_rounds(con)
    _link(con, up, q1)
    _link(con, up, q2)
    con.commit()

    con.execute("DELETE FROM rounds WHERE id = ?", (up,))
    con.commit()
    count = con.execute("SELECT count(*) FROM round_qualifiers").fetchone()[0]
    assert count == 0  # every link off the deleted upgrade round is gone
    con.close()
