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
    # Pinned to MIGRATION_REVISION, not "head": this file tests ONE migration
    # against a legacy-shaped DB, and the revision assertion below is about that
    # migration. "head" silently retargets it as soon as a later revision lands.
    command.upgrade(cfg, MIGRATION_REVISION)  # the assertion: this must not raise

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


# ── Tag handles (2026-07-30): add slug, drop the unique on name ───────────
#
# Second migration in this file's remit, and the reason the file's closing
# advice ("the next migration that reaches for drop_constraint on concerts or
# tags will need the same guard") was worth writing down.
#
# The `tags` table exists in the wild at TWO vintages and one implementation
# must satisfy both:
#
#   named      -- what app.db and production look like TODAY. The batch rebuild
#                 in 1384cadd692e reflected the anonymous UNIQUE and re-emitted
#                 it as `CONSTRAINT uq_tags_name UNIQUE (name)`. Confirmed by
#                 reading the local app.db's sqlite_master at aebefef6ca70.
#   anonymous  -- the pre-1384cadd692e shape, and the harder case:
#                 drop_constraint can only find it because naming_convention is
#                 passed into batch_alter_table.
#
# The anonymous variant is therefore deliberately harder than current reality.
# Keep it: it costs one dict entry and it is the shape that broke a deploy once.

TAG_HANDLES_REVISION = "eb4cb4f7927a"
TAG_HANDLES_PARENT = "aebefef6ca70"

_TAGS_COLUMNS = """
  id INTEGER NOT NULL,
  name VARCHAR(100) NOT NULL,
  kind VARCHAR(9) NOT NULL,
  created_by BIGINT,
  created_at DATETIME NOT NULL,
  parent_id INTEGER,
  location_url VARCHAR(500),
  region VARCHAR(100),
  eventernote_url VARCHAR(500),
  name_en VARCHAR(100),
  name_zh VARCHAR(100),
  city VARCHAR(100),
  city_en VARCHAR(100),
  city_zh VARCHAR(100),
  address VARCHAR(300),
  CONSTRAINT pk_tags PRIMARY KEY (id),
  CONSTRAINT fk_tags_parent_id FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL,
  CONSTRAINT fk_tags_created_by_users FOREIGN KEY(created_by)
    REFERENCES users (discord_id) ON DELETE SET NULL
"""

TAGS_UNIQUE_VARIANTS = {
    "named": "CONSTRAINT uq_tags_name UNIQUE (name)",
    "anonymous": "UNIQUE (name)",
}


def _tags_schema(unique_clause: str) -> str:
    # The unique clause goes AFTER the columns: a table constraint cannot
    # precede a column definition (SQLite answers `near "UNIQUE": syntax
    # error`, which is a fixture bug that looks exactly like a migration bug).
    return f"""
CREATE TABLE "users" (
  discord_id BIGINT NOT NULL, username VARCHAR(100) NOT NULL,
  CONSTRAINT pk_users PRIMARY KEY (discord_id));
CREATE TABLE "tags" (
  {_TAGS_COLUMNS},
  {unique_clause});
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
"""


# (id, name, name_en, kind, expected handle) -- exercises every branch of the
# backfill rule in one pass.
TAG_ROWS = [
    (1, "蓮ノ空", "Hasunosora", "group", "hasunosora"),      # English name wins
    (2, "Zepp Haneda", None, "venue", "zepp-haneda"),        # falls back to `name`
    (3, "スクールアイドル", None, "artist", "artist"),          # no ASCII -> the KIND
    (4, "アイドル二号", None, "artist", "artist-2"),            # ...then numbered
    (5, "Yuki Sato", "Yuki Sato", "artist", "yuki-sato"),
    (6, "yuki sato", "Yuki Sato", "artist", "yuki-sato-2"),  # collides, suffixed
]


@pytest.fixture(params=sorted(TAGS_UNIQUE_VARIANTS))
def tag_handles_migrated(request, tmp_path, monkeypatch):
    """A production-shaped tags table at both vintages, run through the real
    tag-handles revision. Returns (connection, variant name)."""
    variant = request.param
    db_path = tmp_path / f"tags-{variant}.db"
    con = sqlite3.connect(db_path)
    con.executescript(_tags_schema(TAGS_UNIQUE_VARIANTS[variant]))
    con.execute("INSERT INTO users (discord_id, username) VALUES (42, 'reiji')")
    con.executemany(
        "INSERT INTO tags (id, name, name_en, kind, created_by, created_at)"
        " VALUES (?, ?, ?, ?, 42, '2026-01-01 00:00:00')",
        [(r[0], r[1], r[2], r[3]) for r in TAG_ROWS],
    )
    con.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (TAG_HANDLES_PARENT,)
    )
    con.commit()
    # Sanity: the fixture really is the vintage it claims, or it proves nothing.
    sql = _table_sql(con, "tags")
    if variant == "anonymous":
        assert "CONSTRAINT uq_tags_name" not in sql
    assert "UNIQUE (name)" in sql
    assert "slug" not in sql
    con.close()

    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, TAG_HANDLES_REVISION)

    con = sqlite3.connect(db_path)
    yield con, variant
    con.close()


def test_the_unique_on_name_is_gone(tag_handles_migrated):
    """Names stop being unique entirely -- two performers may share one. This is
    the drop that dies with "No such constraint" if naming_convention is not
    passed into batch_alter_table."""
    con, _ = tag_handles_migrated
    assert "UNIQUE (name)" not in _table_sql(con, "tags")


def test_every_row_gets_a_distinct_handle(tag_handles_migrated):
    con, _ = tag_handles_migrated
    got = dict(con.execute("SELECT id, slug FROM tags"))
    assert got == {r[0]: r[4] for r in TAG_ROWS}
    assert None not in got.values()
    assert len(set(got.values())) == len(TAG_ROWS)


def test_a_japanese_only_name_does_not_become_concert(tag_handles_migrated):
    """slugify()'s fallback is the literal string "concert", which would be a
    lie on a tag and indistinguishable from a tag really named that. The
    migration must use the kind, matching service.assign_tag_slug."""
    con, _ = tag_handles_migrated
    assert "concert" not in dict(con.execute("SELECT id, slug FROM tags")).values()


def test_the_handle_is_not_null_and_unique(tag_handles_migrated):
    con, _ = tag_handles_migrated
    notnull = next(
        row[3] for row in con.execute("PRAGMA table_info(tags)") if row[1] == "slug"
    )
    assert notnull == 1, "slug must be NOT NULL"
    # sqlite3 raises at execute(), not at commit(): the unique index is checked
    # per statement.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO tags (id, name, kind, created_by, created_at, slug)"
            " VALUES (99, 'Whatever', 'artist', 42, '2026-01-01 00:00:00', 'hasunosora')"
        )
    con.rollback()
    # And NULL is refused too, which is what NOT NULL above buys.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO tags (id, name, kind, created_by, created_at, slug)"
            " VALUES (98, 'Whatever', 'artist', 42, '2026-01-01 00:00:00', NULL)"
        )
    con.rollback()


def test_duplicate_names_are_now_insertable(tag_handles_migrated):
    """The point of the whole migration: a second Yuki Sato, and a venue sharing
    a name with a group."""
    con, _ = tag_handles_migrated
    con.executemany(
        "INSERT INTO tags (id, name, kind, created_by, created_at, slug)"
        " VALUES (?, ?, ?, 42, '2026-01-01 00:00:00', ?)",
        [
            (100, "Yuki Sato", "artist", "yuki-sato-3"),
            (101, "Zepp Haneda", "group", "zepp-haneda-as-a-group"),
        ],
    )
    con.commit()
    assert con.execute(
        "SELECT count(*) FROM tags WHERE name = 'Yuki Sato'"
    ).fetchone()[0] == 2
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_other_tag_columns_survive_the_rebuild(tag_handles_migrated):
    """Two batch rebuilds run here; the venue/locale columns must come through
    them intact, names and all."""
    con, _ = tag_handles_migrated
    sql = _table_sql(con, "tags")
    for column in ("name_en", "name_zh", "city", "city_en", "city_zh",
                   "address", "eventernote_url", "region", "location_url"):
        assert column in sql, column
    assert "CONSTRAINT fk_tags_parent_id" in sql
    assert "CONSTRAINT fk_tags_created_by_users" in sql
    assert con.execute("SELECT name_en FROM tags WHERE id = 1").fetchone() == ("Hasunosora",)
