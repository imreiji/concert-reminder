"""Migration test: discovered_events.eventernote_event_id -> source_event_id.

Same alembic-chain pattern as test_migration_round_required_item.py: a scratch
SQLite file, the real `alembic.command.upgrade`, raw SQL.

THE assertion here is data preservation. This migration RENAMES a column, and
`alembic revision --autogenerate` reads a rename as drop+add -- which passes any
schema-shaped check while silently destroying every existing lead's external id,
the one column the whole discovery diff keys on. So the tests below insert a real
row at the previous head and follow its value across the upgrade, the downgrade
and the re-upgrade.

`discovered_events` post-dates the NAMING_CONVENTION (it was created whole by
`48cd59cae5d7` with named constraints), so batch-mode reflection needs no
legacy-DDL fixture the way test_migration_legacy_anonymous_constraints.py's
siblings do -- and this migration calls no drop_constraint anyway.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "f846bca262ad"  # head immediately before the rename


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


def _unique_column_sets(con: sqlite3.Connection, table: str) -> list[set[str]]:
    """Every UNIQUE index on `table`, as its set of column names.

    A named UNIQUE *constraint* is an sqlite_autoindex in PRAGMA index_list, so
    asking for the columns rather than the index name is what actually answers
    "is this column still unique?".
    """
    out = []
    for row in con.execute(f"PRAGMA index_list({table})").fetchall():
        name, is_unique = row[1], row[2]
        if is_unique:
            cols = {r[2] for r in con.execute(f"PRAGMA index_info({name})").fetchall()}
            out.append(cols)
    return out


def _table_sql(con: sqlite3.Connection, table: str) -> str:
    return con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]


def _seed_lead(con: sqlite3.Connection, event_id: str = "464372") -> None:
    """One discovered_events row in the PRE-migration shape."""
    con.execute(
        "INSERT INTO discovered_events "
        "(id, eventernote_event_id, title, event_date, venue, "
        " first_seen_at, last_seen_at) "
        "VALUES (1, ?, 'Anniversary Day 2', '2026-09-01', 'Zepp Haneda', "
        "        '2026-08-01 00:00:00', '2026-08-01 00:00:00')",
        (event_id,),
    )
    con.commit()


def test_rename_preserves_every_lead_id(tmp_path, monkeypatch):
    """The one that matters: a lead written before the migration keeps its id."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    assert "eventernote_event_id" in _columns(con, "discovered_events")
    assert "source_event_id" not in _columns(con, "discovered_events")
    _seed_lead(con)
    con.close()

    command.upgrade(cfg, "head")

    con = _connect(db_path)
    cols = _columns(con, "discovered_events")
    assert "source_event_id" in cols
    assert "eventernote_event_id" not in cols
    row = con.execute(
        "SELECT source_event_id, source, date_is_deadline, title "
        "FROM discovered_events WHERE id = 1"
    ).fetchone()
    assert row is not None, "the rename dropped the row -- drop+add, not a rename"
    assert row[0] == "464372"
    assert row[1] == "eventernote"
    assert row[2] == 0
    assert row[3] == "Anniversary Day 2"
    con.close()


def test_source_event_id_is_still_unique_after_the_rebuild(tmp_path, monkeypatch):
    """Batch mode rebuilds the table; the UNIQUE must ride the rename over."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert {"source_event_id"} in _unique_column_sets(con, "discovered_events")

    # The property, not the proxy: a second row with the same id is refused.
    con.execute(
        "INSERT INTO discovered_events "
        "(id, source_event_id, title, event_date, venue, source, date_is_deadline,"
        " first_seen_at, last_seen_at) "
        "VALUES (1, 'dup', 't', '2026-09-01', '', 'eventernote', 0, "
        "        '2026-08-01 00:00:00', '2026-08-01 00:00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO discovered_events "
            "(id, source_event_id, title, event_date, venue, source, date_is_deadline,"
            " first_seen_at, last_seen_at) "
            "VALUES (2, 'dup', 't', '2026-09-02', '', 'eventernote', 0, "
            "        '2026-08-01 00:00:00', '2026-08-01 00:00:00')"
        )
    con.close()


def test_unique_constraint_is_renamed_too_not_just_the_column(tmp_path, monkeypatch):
    """Batch mode carries the reflected UNIQUE over but keeps its OLD name, so
    the migration renames it explicitly.

    Left alone, the server would carry `uq_discovered_events_eventernote_event
    _id UNIQUE (source_event_id)` while Base.metadata -- which every other test
    DB is built from -- says `uq_discovered_events_source_event_id`. That is
    invisible to the whole suite and would surface as a future migration's
    `ValueError: No such constraint` against the live DB only.
    """
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    sql = _table_sql(con, "discovered_events")
    assert "uq_discovered_events_source_event_id" in sql
    assert "uq_discovered_events_eventernote_event_id" not in sql
    con.close()


def test_new_columns_default_for_rows_written_by_the_app(tmp_path, monkeypatch):
    """A server_default, not just a Python default: an INSERT that names
    neither column must still land 'eventernote'/False, which is what makes
    the migration safe for the rows already in production."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")

    con = _connect(db_path)
    con.execute(
        "INSERT INTO discovered_events "
        "(id, source_event_id, title, event_date, venue, first_seen_at, last_seen_at) "
        "VALUES (7, '999', 't', '2026-09-01', '', "
        "        '2026-08-01 00:00:00', '2026-08-01 00:00:00')"
    )
    con.commit()
    assert con.execute(
        "SELECT source, date_is_deadline FROM discovered_events WHERE id = 7"
    ).fetchone() == ("eventernote", 0)
    con.close()


def test_downgrade_round_trips_with_data_intact(tmp_path, monkeypatch):
    """Down and back up, following the same row's id the whole way."""
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    _seed_lead(con)
    con.close()

    command.upgrade(cfg, "head")
    # Target the fixed revision, not a head-relative "-1": this migration is
    # not guaranteed to stay head once later migrations land on top of it.
    command.downgrade(cfg, PRE_MIGRATION_REVISION)

    con = _connect(db_path)
    cols = _columns(con, "discovered_events")
    assert "eventernote_event_id" in cols
    assert "source_event_id" not in cols
    assert "source" not in cols
    assert "date_is_deadline" not in cols
    assert con.execute(
        "SELECT eventernote_event_id FROM discovered_events WHERE id = 1"
    ).fetchone() == ("464372",)
    assert {"eventernote_event_id"} in _unique_column_sets(con, "discovered_events")
    assert "uq_discovered_events_eventernote_event_id" in _table_sql(
        con, "discovered_events"
    )
    con.close()

    command.upgrade(cfg, "head")

    con = _connect(db_path)
    assert "source_event_id" in _columns(con, "discovered_events")
    assert con.execute(
        "SELECT source_event_id, source, date_is_deadline FROM discovered_events WHERE id = 1"
    ).fetchone() == ("464372", "eventernote", 0)
    con.close()
