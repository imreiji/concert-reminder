"""Migration test: adding tags.eventernote_url must survive a LEGACY schema.

See test_migration_legacy_anonymous_constraints.py for the full rationale. The
short version: the production `tags` table predates NAMING_CONVENTION, so its
UNIQUE(name) and created_by FK are anonymous on disk. This migration ALTERs
`tags` in batch (table-rebuild) mode, which reflects the REAL table and would
choke on those anonymous constraints unless handed the naming convention -- so
the migration passes `naming_convention=NAMING_CONVENTION` into
`batch_alter_table`, and this test is the only shape that can prove it works,
because a metadata-built DB carries names the production one lacks.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "ce1a80bb66f4"  # head immediately before the migration
MIGRATION_REVISION = "08aa8b07ad1b"  # tags.eventernote_url

# Verbatim-shaped from the live server: anonymous UNIQUE(name) and anonymous
# created_by FK on `tags`, the named parent_id FK alongside them. Do not "tidy"
# the inconsistency -- it is the whole point.
LEGACY_SCHEMA = """
CREATE TABLE users (discord_id BIGINT NOT NULL, username VARCHAR(100) NOT NULL,
  PRIMARY KEY (discord_id));
CREATE TABLE "tags" (
  id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, kind VARCHAR(9) NOT NULL,
  created_by BIGINT NOT NULL, created_at DATETIME NOT NULL,
  parent_id INTEGER, location_url VARCHAR(500), region VARCHAR(100),
  PRIMARY KEY (id),
  CONSTRAINT fk_tags_parent_id FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL,
  UNIQUE (name), FOREIGN KEY(created_by) REFERENCES users (discord_id));
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
"""


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


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
    con.execute("INSERT INTO users (discord_id, username) VALUES (42, 'reiji')")
    con.execute(
        "INSERT INTO tags (id, name, kind, created_by, created_at) "
        "VALUES (1, 'Hasunosora', 'group', 42, '2026-01-01 00:00:00')"
    )
    con.commit()
    # Sanity: the fixture really is legacy-shaped, or the test proves nothing.
    assert "CONSTRAINT fk_tags_created_by_users" not in _table_sql(con, "tags")
    assert "eventernote_url" not in _table_sql(con, "tags")
    con.close()

    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, MIGRATION_REVISION)  # the assertion: this must not raise

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    yield con, cfg, db_path
    con.close()


def test_upgrade_lands_on_the_expected_revision(legacy_migrated):
    con, _, _ = legacy_migrated
    assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [
        (MIGRATION_REVISION,)
    ]


def test_eventernote_url_column_added_and_row_survives(legacy_migrated):
    con, _, _ = legacy_migrated
    assert "eventernote_url" in _table_sql(con, "tags")
    # the pre-existing row survived the table rebuild, new column defaults NULL
    assert con.execute("SELECT name, eventernote_url FROM tags").fetchall() == [
        ("Hasunosora", None)
    ]


def test_downgrade_reverses_the_column(legacy_migrated):
    con, cfg, db_path = legacy_migrated
    con.close()
    command.downgrade(cfg, PRE_MIGRATION_REVISION)  # must not raise
    con2 = sqlite3.connect(db_path)
    assert "eventernote_url" not in _table_sql(con2, "tags")
    assert con2.execute("SELECT name FROM tags").fetchall() == [("Hasunosora",)]
    con2.close()
