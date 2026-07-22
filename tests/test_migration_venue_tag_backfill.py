"""The backfill in the venue-tag migration, against a LEGACY-shaped DB.

Test DBs are built from Base.metadata, so every constraint is named and the
divergence from the live server (anonymous constraints on tables created by
older migrations) is invisible to the rest of the suite. This fixture writes
the real server DDL by hand so batch mode is exercised the way production
will exercise it.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "4d5a2d834b3a"  # head immediately before the migration
MIGRATION_REVISION = "789bbcc95bc3"  # venue tag on legs

LEGACY_DDL = """
CREATE TABLE tags (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    name_zh VARCHAR(100),
    kind VARCHAR(9) NOT NULL,
    parent_id INTEGER,
    location_url VARCHAR(500),
    region VARCHAR(100),
    eventernote_url VARCHAR(500),
    created_by BIGINT,
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
    doors_at_utc DATETIME,
    starts_at_utc DATETIME NOT NULL,
    cancelled BOOLEAN DEFAULT '0' NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(concert_id) REFERENCES concerts (id) ON DELETE CASCADE
);
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
            (1, 'Zepp Haneda', 'venue', '2026-01-01 00:00:00'),
            (2, 'K Arena Yokohama', 'venue', '2026-01-01 00:00:00'),
            (3, 'Hasunosora', 'group', '2026-01-01 00:00:00');
        INSERT INTO concerts (id, event_id, title) VALUES (1, 'ev1', 'T');
        INSERT INTO concert_days (id, concert_id, label, venue, starts_at_utc) VALUES
            (1, 1, 'Day 1', 'Zepp Haneda',       '2026-08-01 09:00:00'),
            (2, 1, 'Day 2', '  zepp haneda  ',   '2026-08-02 09:00:00'),
            (3, 1, 'Day 3', 'Nowhere Hall',      '2026-08-03 09:00:00'),
            (4, 1, 'Day 4', NULL,                '2026-08-04 09:00:00'),
            (5, 1, 'Day 5', 'Hasunosora',        '2026-08-05 09:00:00');
    """)
    conn.execute(
        "INSERT INTO concert_days (id, concert_id, label, venue, starts_at_utc) "
        "VALUES (6, 1, 'Day 6', 'Zepp Haneda' || char(12288), '2026-08-06 09:00:00')"
    )
    conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (PRE_MIGRATION_REVISION,)
    )
    conn.commit()
    # Sanity: the fixture really is legacy-shaped, or the test proves nothing.
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tags'"
    ).fetchone()[0]
    assert "CONSTRAINT uq_tags_name" not in sql
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


def test_backfill_matches_case_and_whitespace_insensitively(legacy_db, monkeypatch):
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    rows = dict(conn.execute(
        "SELECT id, venue_tag_id FROM concert_days ORDER BY id"
    ).fetchall())
    conn.close()

    assert rows[1] == 1, "exact match"
    assert rows[2] == 1, "case and whitespace differences must still match"
    assert rows[3] is None, "no such venue tag -- left NULL for reporting"
    assert rows[4] is None, "no venue at all"
    assert rows[5] is None, "a same-named GROUP tag must NOT match a venue"
    assert rows[6] == 1, (
        "a trailing U+3000 IDEOGRAPHIC SPACE must match too -- SQLite's "
        "single-arg trim() strips only U+0020, but Python's str.strip() "
        "(what find_venue_tag uses) is Unicode-aware"
    )


def test_backfill_preserves_free_text_columns(legacy_db, monkeypatch):
    """Phase 1 must not destroy the source data -- phase 5 drops it, after
    the unmatched report has been acted on."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    venue = conn.execute("SELECT venue FROM concert_days WHERE id = 3").fetchone()[0]
    conn.close()
    assert venue == "Nowhere Hall"


def test_tag_columns_and_venue_fk_land_on_a_legacy_shaped_table(legacy_db, monkeypatch):
    """The batch rebuild must succeed against anonymous constraints, and the
    new FK/index must actually exist afterwards."""
    _run_upgrade(legacy_db, monkeypatch)
    conn = sqlite3.connect(legacy_db)
    tag_cols = {row[1] for row in conn.execute("PRAGMA table_info(tags)")}
    assert {"city", "city_en", "city_zh", "address"} <= tag_cols
    fks = {row[3]: (row[2], row[6]) for row in conn.execute(
        "PRAGMA foreign_key_list(concert_days)"
    )}
    assert fks["venue_tag_id"] == ("tags", "SET NULL")
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(concert_days)")}
    assert "ix_concert_days_venue_tag_id" in indexes
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
