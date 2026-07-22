"""The label-locale-variant migration, against a LEGACY-shaped DB.

Test DBs are built from Base.metadata, so every constraint is named and the
divergence from the live server (anonymous constraints on tables created by
older migrations) is invisible to the rest of the suite. `concert_days` and
`rounds` both predate the naming convention, so this fixture writes the real
server DDL by hand and the batch rebuild is exercised the way production will
exercise it.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "789bbcc95bc3"  # head immediately before the migration
MIGRATION_REVISION = "a589d82c11b4"  # label locale variants

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
    created_by BIGINT,
    PRIMARY KEY (id),
    UNIQUE (event_id)
);
CREATE TABLE concert_days (
    id INTEGER NOT NULL,
    concert_id INTEGER NOT NULL,
    label VARCHAR(100) NOT NULL,
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
CREATE TABLE rounds (
    id INTEGER NOT NULL,
    concert_id INTEGER NOT NULL,
    kind VARCHAR(30) NOT NULL,
    label VARCHAR(200) NOT NULL,
    label_en VARCHAR(200),
    opens_at_utc DATETIME,
    closes_at_utc DATETIME,
    results_at_utc DATETIME,
    payment_deadline_at_utc DATETIME,
    applies_to JSON,
    url VARCHAR(500),
    notes TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(concert_id) REFERENCES concerts (id) ON DELETE CASCADE
);
CREATE INDEX ix_rounds_concert_id ON rounds (concert_id);
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
"""


@pytest.fixture()
def legacy_db(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.executescript("""
        INSERT INTO tags (id, name, kind, created_at) VALUES
            (1, 'Zepp Haneda', 'venue', '2026-01-01 00:00:00');
        INSERT INTO concerts (id, event_id, title) VALUES (1, 'ev1', 'T');
        INSERT INTO concert_days
            (id, concert_id, label, venue_tag_id, starts_at_utc) VALUES
            (1, 1, 'Day 1', 1,    '2026-08-01 09:00:00'),
            (2, 1, 'Day 2', NULL, '2026-08-02 09:00:00');
        INSERT INTO rounds (id, concert_id, kind, label, label_en) VALUES
            (1, 1, 'lottery', '最速先行 Round 1', 'Fastest presale round 1'),
            (2, 1, 'general', '一般販売', NULL);
    """)
    conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (PRE_MIGRATION_REVISION,)
    )
    conn.commit()
    # Sanity: the fixture really is legacy-shaped, or the test proves nothing.
    for table in ("concert_days", "rounds"):
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "CONSTRAINT" not in sql, f"{table} fixture must be anonymous"
    conn.close()
    return path


def _run_upgrade(db_path, monkeypatch):
    """Point alembic at the scratch file and run the one migration.

    env.py resolves the URL from app settings, so overriding the setting is
    what actually guarantees the migration cannot touch the repo's app.db.
    Pinned to MIGRATION_REVISION rather than "head" so a later revision does
    not silently retarget this file.
    """
    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"
    )
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_path).as_posix()}")
    command.upgrade(cfg, MIGRATION_REVISION)


def test_label_variant_columns_land_on_legacy_shaped_tables(legacy_db, monkeypatch):
    """The batch rebuild must succeed against anonymous constraints."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    day_cols = {row[1] for row in conn.execute("PRAGMA table_info(concert_days)")}
    round_cols = {row[1] for row in conn.execute("PRAGMA table_info(rounds)")}
    conn.close()
    assert {"label_en", "label_zh"} <= day_cols
    assert {"label_en", "label_zh"} <= round_cols


def test_migration_preserves_rows_and_existing_english_labels(legacy_db, monkeypatch):
    """There is no backfill: Round.label_en keeps its values, the three new
    columns start NULL and fall through to the original via loc_field."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    rounds = conn.execute(
        "SELECT id, label, label_en, label_zh FROM rounds ORDER BY id"
    ).fetchall()
    days = conn.execute(
        "SELECT id, label, label_en, label_zh FROM concert_days ORDER BY id"
    ).fetchall()
    conn.close()

    assert rounds == [
        (1, "最速先行 Round 1", "Fastest presale round 1", None),
        (2, "一般販売", None, None),
    ]
    assert days == [
        (1, "Day 1", None, None),
        (2, "Day 2", None, None),
    ]


def test_rebuild_keeps_foreign_keys_and_indexes_intact(legacy_db, monkeypatch):
    """Batch mode copies the table; the anonymous FKs and the indexes must
    survive, and no row may be orphaned by the rebuild."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    day_fks = {row[3]: (row[2], row[6]) for row in conn.execute(
        "PRAGMA foreign_key_list(concert_days)"
    )}
    round_fks = {row[3]: (row[2], row[6]) for row in conn.execute(
        "PRAGMA foreign_key_list(rounds)"
    )}
    day_indexes = {row[1] for row in conn.execute("PRAGMA index_list(concert_days)")}
    round_indexes = {row[1] for row in conn.execute("PRAGMA index_list(rounds)")}
    check = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    assert day_fks["concert_id"] == ("concerts", "CASCADE")
    assert day_fks["venue_tag_id"] == ("tags", "SET NULL")
    assert round_fks["concert_id"] == ("concerts", "CASCADE")
    assert "ix_concert_days_venue_tag_id" in day_indexes
    assert "ix_rounds_concert_id" in round_indexes
    assert check == []


def test_downgrade_removes_the_new_columns(legacy_db, monkeypatch):
    _run_upgrade(legacy_db, monkeypatch)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(legacy_db).as_posix()}")
    command.downgrade(cfg, PRE_MIGRATION_REVISION)

    conn = sqlite3.connect(legacy_db)
    day_cols = {row[1] for row in conn.execute("PRAGMA table_info(concert_days)")}
    round_cols = {row[1] for row in conn.execute("PRAGMA table_info(rounds)")}
    labels = conn.execute("SELECT label_en FROM rounds ORDER BY id").fetchall()
    conn.close()

    assert "label_en" not in day_cols
    assert "label_zh" not in day_cols
    assert "label_zh" not in round_cols
    assert "label_en" in round_cols, "the pre-existing round gloss must survive"
    assert labels == [("Fastest presale round 1",), (None,)]
