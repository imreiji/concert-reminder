"""The round-label-phrase migration.

A brand-new table, so no batch mode and no legacy-shaped fixture is needed --
nothing pre-existing is altered. What IS worth pinning is that the unique
index really spans the whole triple: two phrases sharing a Japanese label but
differing in translation must be able to coexist, which is what lets a
corrected phrase live alongside the typo it replaces until the typo is
deleted.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "a589d82c11b4"  # head immediately before the migration
MIGRATION_REVISION = "14bc590fdb44"  # round label phrases


@pytest.fixture()
def scratch_db(tmp_path):
    path = tmp_path / "scratch.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL,
            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
        );
    """)
    # Without a stamped row, `upgrade` would replay the whole history here.
    conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)", (PRE_MIGRATION_REVISION,)
    )
    conn.commit()
    conn.close()
    return path


def _point_alembic_at(db_path, monkeypatch):
    """alembic/env.py computes its URL from `settings.database_url` at import
    time and never reads the Config we build here, so
    `cfg.set_main_option("sqlalchemy.url", ...)` would be a no-op -- this
    monkeypatch is the ONLY thing keeping the migration off the repo's real
    app.db."""
    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"
    )
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _run_upgrade(db_path, monkeypatch):
    # Pinned to MIGRATION_REVISION rather than "head" so a later revision does
    # not silently retarget this file.
    command.upgrade(_point_alembic_at(db_path, monkeypatch), MIGRATION_REVISION)


def _run_downgrade(db_path, monkeypatch):
    command.downgrade(_point_alembic_at(db_path, monkeypatch), PRE_MIGRATION_REVISION)


def test_upgrade_creates_the_table_and_its_unique_index(scratch_db, monkeypatch):
    _run_upgrade(scratch_db, monkeypatch)
    conn = sqlite3.connect(scratch_db)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(round_label_phrases)")}
    indexes = {row[1]: row for row in conn.execute("PRAGMA index_list(round_label_phrases)")}
    index_cols = [
        row[2] for row in conn.execute("PRAGMA index_info(uq_round_label_phrase)")
    ]
    conn.close()

    assert "round_label_phrases" in tables
    assert set(cols) == {
        "id", "label", "label_en", "label_zh", "used_count", "created_at", "last_used_at",
    }
    for name in ("label", "label_en", "label_zh", "used_count", "created_at", "last_used_at"):
        assert cols[name][3] == 1, f"{name} must be NOT NULL"
    assert "uq_round_label_phrase" in indexes
    assert indexes["uq_round_label_phrase"][2] == 1, "the index must be unique"
    assert index_cols == ["label", "label_en", "label_zh"]


def test_duplicate_triples_are_rejected_but_partial_matches_are_not(
    scratch_db, monkeypatch
):
    _run_upgrade(scratch_db, monkeypatch)
    conn = sqlite3.connect(scratch_db)
    insert = (
        "INSERT INTO round_label_phrases (label, label_en, label_zh, created_at,"
        " last_used_at) VALUES (?, ?, ?, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    )
    conn.execute(insert, ("1次先行抽選", "1st-round lottery", "第一轮先行"))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("1次先行抽選", "1st-round lottery", "第一轮先行"))
    conn.rollback()

    # A corrected phrase differs in only one column and must still be storable
    # alongside the typo it replaces.
    conn.execute(insert, ("1次先行抽選", "1st-round lotery", "第一轮先行"))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM round_label_phrases").fetchone()[0] == 2

    # used_count defaults to 1 at the DB layer too, not only in the model.
    assert conn.execute("SELECT DISTINCT used_count FROM round_label_phrases").fetchall() == [
        (1,)
    ]
    conn.close()


def test_downgrade_removes_the_table_and_the_index(scratch_db, monkeypatch):
    _run_upgrade(scratch_db, monkeypatch)
    _run_downgrade(scratch_db, monkeypatch)

    conn = sqlite3.connect(scratch_db)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    conn.close()

    assert "round_label_phrases" not in tables
    assert "uq_round_label_phrase" not in indexes
