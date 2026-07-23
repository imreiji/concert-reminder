"""Dropping the six legacy free-text venue columns, against a LEGACY-shaped DB.

Test DBs are built from Base.metadata, so every constraint is named and the
divergence from the live server (anonymous constraints on tables created by
older migrations) is invisible to the rest of the suite. `concerts` and
`concert_days` predate the naming convention, so this fixture writes their
real anonymous-constraint DDL by hand -- a batch DROP that passes on a
metadata-built (named) DB can still die on the server with "No such
constraint" (has shipped once), and only an anonymous fixture proves it won't.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "14bc590fdb44"  # head immediately before the migration
MIGRATION_REVISION = "ce43bfcfcae3"  # drop legacy venue columns

# A realistic post-backfill, post-label-variant shape: concert_days carries
# label_en/label_zh and venue_tag_id alongside the six columns being dropped.
LEGACY_DDL = """
CREATE TABLE tags (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    kind VARCHAR(9) NOT NULL,
    parent_id INTEGER,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (name),
    FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL
);
CREATE TABLE concerts (
    id INTEGER NOT NULL,
    event_id VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    venue VARCHAR(200),
    venue_en VARCHAR(200),
    venue_zh VARCHAR(200),
    created_by BIGINT,
    PRIMARY KEY (id),
    UNIQUE (event_id)
);
CREATE TABLE concert_days (
    id INTEGER NOT NULL,
    concert_id INTEGER NOT NULL,
    label VARCHAR(100) NOT NULL,
    label_en VARCHAR(100),
    label_zh VARCHAR(100),
    city VARCHAR(100),
    venue VARCHAR(200),
    venue_address VARCHAR(300),
    venue_tag_id INTEGER,
    doors_at_utc DATETIME,
    starts_at_utc DATETIME NOT NULL,
    cancelled BOOLEAN DEFAULT '0' NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(concert_id) REFERENCES concerts (id) ON DELETE CASCADE,
    FOREIGN KEY(venue_tag_id) REFERENCES tags (id) ON DELETE SET NULL
);
CREATE INDEX ix_concert_days_venue_tag_id ON concert_days (venue_tag_id);
CREATE INDEX ix_concert_days_concert_id ON concert_days (concert_id);
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
"""

DROPPED_CONCERT_COLS = {"venue", "venue_en", "venue_zh"}
DROPPED_DAY_COLS = {"city", "venue", "venue_address"}


@pytest.fixture()
def legacy_db(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.executescript("""
        INSERT INTO tags (id, name, kind, created_at) VALUES
            (1, 'Zepp Haneda', 'venue', '2026-01-01 00:00:00');
        INSERT INTO concerts (id, event_id, title, venue, venue_en, venue_zh) VALUES
            (1, 'ev1', 'T', 'Zepp Haneda', 'Zepp Haneda EN', 'Zepp Haneda ZH');
        INSERT INTO concert_days
            (id, concert_id, label, city, venue, venue_address, venue_tag_id, starts_at_utc)
        VALUES
            (1, 1, 'Day 1', 'Tokyo', 'Zepp Haneda', 'Ota, Tokyo', 1, '2026-08-01 09:00:00'),
            (2, 1, 'Day 2', NULL, NULL, NULL, NULL, '2026-08-02 09:00:00');
    """)
    conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (PRE_MIGRATION_REVISION,)
    )
    conn.commit()
    # Sanity: the fixture really is legacy-shaped, or the test proves nothing.
    # A named-constraint fixture would make the "No such constraint" guard a
    # tautology.
    for table in ("concerts", "concert_days"):
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "CONSTRAINT" not in sql, f"{table} fixture must be anonymous"
    conn.close()
    return path


def _run_upgrade(db_path, monkeypatch):
    """Point alembic at the scratch file and run the one migration.

    alembic/env.py computes its URL from `settings.database_url` and never
    reads the Config we build here, so `cfg.set_main_option("sqlalchemy.url",
    ...)` would be a no-op -- the monkeypatch below is the ONLY thing that
    keeps this migration off the repo's real app.db. Pinned to
    MIGRATION_REVISION rather than "head" so a later revision does not
    silently retarget this file.
    """
    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"
    )
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, MIGRATION_REVISION)


def _run_downgrade(db_path, monkeypatch, revision):
    """Point alembic at the scratch file and downgrade to `revision`.

    Same trap as `_run_upgrade`: env.py reads `settings.database_url` and
    ignores this Config's `sqlalchemy.url`, so the monkeypatch is what aims
    the downgrade at the scratch file rather than the repo's real app.db.
    """
    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"
    )
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.downgrade(cfg, revision)


def test_upgrade_drops_the_six_columns_on_a_legacy_shaped_db(legacy_db, monkeypatch):
    """The batch DROP must succeed against anonymous constraints and remove
    all six columns while leaving the venue TAG plumbing intact."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    concert_cols = {row[1] for row in conn.execute("PRAGMA table_info(concerts)")}
    day_cols = {row[1] for row in conn.execute("PRAGMA table_info(concert_days)")}
    check = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    assert DROPPED_CONCERT_COLS.isdisjoint(concert_cols)
    assert DROPPED_DAY_COLS.isdisjoint(day_cols)
    # The current model columns must survive the rebuild.
    assert "venue_tag_id" in day_cols
    assert {"label_en", "label_zh"} <= day_cols
    assert check == []


def test_rebuild_keeps_the_venue_tag_fk_and_indexes(legacy_db, monkeypatch):
    """Batch mode copies the table; the anonymous FKs and the indexes must
    survive the drop, and no row may be orphaned."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    day_fks = {row[3]: (row[2], row[6]) for row in conn.execute(
        "PRAGMA foreign_key_list(concert_days)"
    )}
    day_indexes = {row[1] for row in conn.execute("PRAGMA index_list(concert_days)")}
    conn.close()

    assert day_fks["concert_id"] == ("concerts", "CASCADE")
    assert day_fks["venue_tag_id"] == ("tags", "SET NULL")
    assert "ix_concert_days_venue_tag_id" in day_indexes
    assert "ix_concert_days_concert_id" in day_indexes


def test_downgrade_then_upgrade_round_trips(legacy_db, monkeypatch):
    """downgrade recreates the six columns (empty by design); a second
    upgrade drops them again -- the migration is reversible in both
    directions against the legacy shape."""
    _run_upgrade(legacy_db, monkeypatch)
    _run_downgrade(legacy_db, monkeypatch, PRE_MIGRATION_REVISION)

    conn = sqlite3.connect(legacy_db)
    concert_cols = {row[1] for row in conn.execute("PRAGMA table_info(concerts)")}
    day_cols = {row[1] for row in conn.execute("PRAGMA table_info(concert_days)")}
    conn.close()
    assert DROPPED_CONCERT_COLS <= concert_cols, "downgrade must recreate the columns"
    assert DROPPED_DAY_COLS <= day_cols

    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    concert_cols = {row[1] for row in conn.execute("PRAGMA table_info(concerts)")}
    day_cols = {row[1] for row in conn.execute("PRAGMA table_info(concert_days)")}
    check = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert DROPPED_CONCERT_COLS.isdisjoint(concert_cols)
    assert DROPPED_DAY_COLS.isdisjoint(day_cols)
    assert check == []
