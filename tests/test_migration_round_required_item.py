"""Migration test: rounds.required_item_round_id (f846bca262ad).

Same pattern as test_migration_round_qualifiers.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the column and its
index exist after upgrading, that SET NULL fires through the MIGRATED
schema (not just Base.metadata, which every other test builds from), and
that the downgrade -- which carries a deliberate drop_index, see the
migration's own comment -- round-trips cleanly.

This migration ALTERs an existing table (`rounds`) but only ADDS a column,
index and foreign key; it never calls drop_constraint, so it needs no
naming_convention/legacy-DDL treatment the way
test_migration_legacy_anonymous_constraints.py's siblings do.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "0a0f05ea8746"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")  # cascades silently don't fire without this
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA index_list({table})").fetchall()}


def _seed_concert_and_rounds(con: sqlite3.Connection) -> tuple[int, int]:
    """Insert one concert, one item-sale round and one round that requires
    it. Returns (item_id, requiring_id)."""
    con.execute(
        "INSERT INTO concerts (id, event_id, title, created_at) "
        "VALUES (1, 'evt', 'T', '2026-07-19 00:00:00')"
    )
    con.execute(
        "INSERT INTO rounds (id, concert_id, kind, label) VALUES (10, 1, 'goods_sale', 'Goods')"
    )
    con.execute(
        "INSERT INTO rounds (id, concert_id, kind, label, required_item_round_id) "
        "VALUES (11, 1, 'lottery_round', 'Lottery', 10)"
    )
    return 10, 11


def test_required_item_round_id_column_and_index_added_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "required_item_round_id" not in _columns(con, "rounds")
    con.close()

    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "required_item_round_id" in _columns(con, "rounds")
    assert "ix_rounds_required_item_round_id" in _indexes(con, "rounds")
    con.close()


def test_required_item_round_downgrade_round_trips(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "required_item_round_id" in _columns(con, "rounds")
    con.close()

    # Target the fixed revision, not a head-relative "-1": this migration is
    # not guaranteed to stay head once later migrations land on top of it.
    command.downgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "required_item_round_id" not in _columns(con, "rounds")
    assert "ix_rounds_required_item_round_id" not in _indexes(con, "rounds")
    con.close()

    # And it re-applies cleanly (no leftover state) -- this is exactly what
    # the migration's own drop_index exists to make possible; without it,
    # batch mode's rebuild step tries to recreate the index against a table
    # that no longer has the column and raises.
    command.upgrade(cfg, "head")
    con = _connect(db_path)
    assert "required_item_round_id" in _columns(con, "rounds")
    assert "ix_rounds_required_item_round_id" in _indexes(con, "rounds")
    con.close()


def test_set_null_fires_through_migrated_schema_on_target_delete(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    item_id, requiring_id = _seed_concert_and_rounds(con)
    con.commit()

    con.execute("DELETE FROM rounds WHERE id = ?", (item_id,))
    con.commit()

    required = con.execute(
        "SELECT required_item_round_id FROM rounds WHERE id = ?", (requiring_id,)
    ).fetchone()[0]
    assert required is None
    con.close()
