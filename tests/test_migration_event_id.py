"""Migration test: Concert.event_id backfill + constraints.

Runs the real alembic upgrade path against a scratch SQLite file seeded at
the pre-migration schema (rows with no event_id column at all), mirroring
the manual verification done before this migration was ever applied to a
real DB: existing concerts must be backfilled to str(id), and the
NOT NULL + UNIQUE constraints must actually be enforced afterward.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "96e348a6310c"  # head immediately before event_id


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_event_id_backfill_and_constraints(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, created_at) "
        "VALUES (1, 'reiji', 'America/Moncton', '2025-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concerts (id, title, created_by, created_at) "
        "VALUES (5, 'Old Concert', 1, '2025-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO concerts (id, title, created_by, created_at) "
        "VALUES (9, 'Another Old', 1, '2025-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT id, event_id FROM concerts ORDER BY id").fetchall()
    assert rows == [(5, "5"), (9, "9")]  # backfilled to the old numeric id

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE concerts SET event_id = '5' WHERE id = 9")
    con.rollback()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO concerts (id, title, created_by, created_at) "
            "VALUES (20, 'No event id', 1, '2025-01-01 00:00:00')"
        )
    con.close()
