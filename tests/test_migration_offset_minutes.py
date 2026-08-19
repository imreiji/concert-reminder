"""Migration test: offset_minutes on reminder_rules and preset_items.

Same pattern as test_migration_opt_out_day_rows.py: upgrade to the revision
immediately BEFORE this migration, insert rows with the OLD schema (no
offset_minutes column at all), upgrade the rest of the way to head, and
read the rows back. SQLite's batch_alter_table (used because SQLite can't
ALTER a column in place) RECREATES the table and copies every existing row
across -- so the real guarantee is not "the new column's metadata says
default 0" (a plain `pragma table_info` on a freshly-created, row-less
table would pass that even if the copy step silently dropped the
server_default and left the column NULL). The guarantee is that a row
written before this migration ran reads back with a real 0, not NULL.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "fc4a98ad678a"  # head immediately before this migration


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_offset_minutes_column_shape_after_upgrade(tmp_path, monkeypatch):
    """NOT NULL integer, defaulting to 0 -- pins the column's declared shape
    so a mutant flipping nullable=False->True or Integer()->String() fails."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        for table in ("reminder_rules", "preset_items"):
            cols = {row[1]: row for row in conn.execute(f"pragma table_info({table})")}
            assert "offset_minutes" in cols, f"{table} did not gain the column"
            # pragma table_info row shape: (cid, name, type, notnull, dflt_value, pk)
            _, _, col_type, notnull, default, _ = cols["offset_minutes"]
            assert col_type.upper() == "INTEGER", f"{table}.offset_minutes is {col_type}"
            assert notnull == 1, f"{table}.offset_minutes is nullable"
            assert default == "'0'", f"{table}.offset_minutes default is not 0"


def test_a_rule_written_by_the_old_schema_reads_back_as_zero_minutes(tmp_path, monkeypatch):
    """A rule/preset-item row written before this migration ran must read
    back as a zero-minute row, not NULL -- the planner adds offset_minutes
    to a timedelta without checking. batch_alter_table rebuilds the table
    and copies existing rows, so this is the guarantee that copy actually
    backfills the server_default rather than leaving the new column NULL."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)  # plain connection: FKs off, no parent rows needed
    con.execute(
        "INSERT INTO reminder_rules (id, user_id, concert_id, round_id, anchor, "
        "offset_days, offset_hours, channel, created_at) "
        "VALUES (1, 42, NULL, NULL, 'event_start', -1, 0, 'dm', '2026-06-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO preset_items (id, preset_id, anchor, offset_days, offset_hours) "
        "VALUES (1, 1, 'opens', -3, 0)"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    rule_offset = con.execute(
        "SELECT offset_minutes FROM reminder_rules WHERE id = 1"
    ).fetchone()[0]
    preset_offset = con.execute(
        "SELECT offset_minutes FROM preset_items WHERE id = 1"
    ).fetchone()[0]
    con.close()

    assert rule_offset == 0
    assert preset_offset == 0
