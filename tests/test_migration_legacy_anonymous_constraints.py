"""Migration test: the user-erasure migration must survive a LEGACY schema.

WHY THIS FILE EXISTS (read before deleting or "simplifying" it)
--------------------------------------------------------------
Every other DB in this project -- every test fixture, and the DB you get by
running `alembic upgrade head` on an empty file today -- is shaped by
`Base.metadata`, which carries NAMING_CONVENTION. So every constraint has a
name, and `batch_alter_table(...).drop_constraint('fk_concerts_created_by_users')`
finds what it is looking for.

The production database does NOT. It was built up through older migrations
that emitted anonymous constraints:

    FOREIGN KEY(created_by) REFERENCES users (discord_id)   -- concerts, tags
    UNIQUE (name)                                           -- tags

SQLite batch mode reflects the REAL table, not the metadata. Against the live
schema it found no constraint by the conventional name and aborted mid-deploy
with `ValueError: No such constraint: 'fk_concerts_created_by_users'`. The
entire existing suite stayed green through that, because a metadata-built test
DB cannot express the divergence.

So this test does not build its database from metadata. It hand-writes the
exact DDL captured from the live server (anonymous constraints and all), stamps
it to the revision before the migration, and runs the real migration against
it. That is the only shape that can catch legacy-schema drift, and the next
migration that reaches for `drop_constraint` on `concerts` or `tags` will need
the same guard.

Note the asymmetry in the fixture DDL: `concert_audit`'s FK IS named while
`concerts`/`tags` are anonymous -- `concert_audit` is a newer table, created
after NAMING_CONVENTION landed. That is why the production failure surfaced on
the second batch block rather than the first. Do not "tidy" the DDL below; its
inconsistency is the whole point.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "e8a1c9d2f7b5"  # head immediately before the migration
MIGRATION_REVISION = "1384cadd692e"  # user erasure: author FKs -> SET NULL

AUTHORED = {
    "concerts": ("created_by", "fk_concerts_created_by_users"),
    "tags": ("created_by", "fk_tags_created_by_users"),
    "concert_audit": ("edited_by", "fk_concert_audit_edited_by_users"),
}

# Verbatim from the live server (sqlite_master), trimmed to the tables this
# migration touches. Anonymous FKs on concerts/tags preserved deliberately.
LEGACY_SCHEMA = """
CREATE TABLE users (discord_id BIGINT NOT NULL, username VARCHAR(100) NOT NULL,
  PRIMARY KEY (discord_id));
CREATE TABLE "concerts" (
  id INTEGER NOT NULL, title VARCHAR(200) NOT NULL, franchise VARCHAR(100), venue VARCHAR(200),
  notes TEXT, created_by BIGINT NOT NULL, created_at DATETIME NOT NULL, kind VARCHAR(11),
  title_en VARCHAR(200), organizer VARCHAR(200), categories VARCHAR(300),
  eventernote_url VARCHAR(500), official_url VARCHAR(500), source_url VARCHAR(500),
  performers_text TEXT, event_id VARCHAR(100) NOT NULL,
  PRIMARY KEY (id), CONSTRAINT uq_concerts_event_id UNIQUE (event_id),
  FOREIGN KEY(created_by) REFERENCES users (discord_id));
CREATE TABLE "tags" (
  id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, kind VARCHAR(9) NOT NULL,
  created_by BIGINT NOT NULL, created_at DATETIME NOT NULL,
  parent_id INTEGER, location_url VARCHAR(500), region VARCHAR(100),
  PRIMARY KEY (id),
  CONSTRAINT fk_tags_parent_id FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL,
  UNIQUE (name), FOREIGN KEY(created_by) REFERENCES users (discord_id));
CREATE TABLE concert_audit (
  id INTEGER NOT NULL, concert_id INTEGER NOT NULL, edited_by BIGINT NOT NULL,
  edited_at_utc DATETIME NOT NULL, changes JSON NOT NULL,
  CONSTRAINT pk_concert_audit PRIMARY KEY (id),
  CONSTRAINT fk_concert_audit_concert_id_concerts FOREIGN KEY(concert_id)
    REFERENCES concerts (id) ON DELETE CASCADE,
  CONSTRAINT fk_concert_audit_edited_by_users FOREIGN KEY(edited_by)
    REFERENCES users (discord_id));
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
"""


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    """Point alembic at the scratch file.

    env.py resolves the URL from app settings, so overriding the setting is
    what actually guarantees the migration cannot touch the repo's app.db.
    """
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _author_fk(con: sqlite3.Connection, table: str) -> tuple[str, int]:
    """(on_delete, notnull) for the table's author column."""
    column = AUTHORED[table][0]
    on_delete = next(
        row[6] for row in con.execute(f"PRAGMA foreign_key_list({table})") if row[3] == column
    )
    notnull = next(
        row[3] for row in con.execute(f"PRAGMA table_info({table})") if row[1] == column
    )
    return on_delete, notnull


def _table_sql(con: sqlite3.Connection, table: str) -> str:
    return con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]


@pytest.fixture()
def legacy_migrated(tmp_path, monkeypatch):
    """A production-shaped DB, upgraded through the real migration."""
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (PRE_MIGRATION_REVISION,))
    con.execute(
        "INSERT INTO users (discord_id, username) VALUES (42, 'reiji')"
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
    # Sanity: the fixture really is legacy-shaped, or the test proves nothing.
    assert "CONSTRAINT fk_concerts_created_by_users" not in _table_sql(con, "concerts")
    assert "CONSTRAINT fk_tags_created_by_users" not in _table_sql(con, "tags")
    con.close()

    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")  # the assertion: this must not raise

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    yield con, db_path
    con.close()


def test_upgrade_lands_on_the_expected_revision(legacy_migrated):
    con, _ = legacy_migrated
    assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [
        (MIGRATION_REVISION,)
    ]


def test_anonymous_author_fks_are_now_named_and_set_null(legacy_migrated):
    con, _ = legacy_migrated
    for table, (_, fk_name) in AUTHORED.items():
        assert f"CONSTRAINT {fk_name}" in _table_sql(con, table), table
        assert _author_fk(con, table) == ("SET NULL", 0), table


def test_pre_existing_rows_survive_the_table_rebuild(legacy_migrated):
    con, _ = legacy_migrated
    for table, (column, _) in AUTHORED.items():
        assert con.execute(f"SELECT {column} FROM {table}").fetchall() == [(42,)], table


def test_erasure_works_against_a_legacy_built_database(legacy_migrated):
    con, _ = legacy_migrated
    con.execute("DELETE FROM users")
    con.commit()
    for table, (column, _) in AUTHORED.items():
        assert con.execute(f"SELECT {column} FROM {table}").fetchall() == [(None,)], table
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
