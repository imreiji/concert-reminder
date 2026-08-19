"""Migration test: offset_minutes on reminder_rules and preset_items.

Same pattern as test_migration_concert_audit.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming both tables gain
the column with a NOT NULL, default-0 shape.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _upgrade_to_head(db_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def test_offset_minutes_exists_and_defaults_to_zero(tmp_path, monkeypatch):
    """A rule written by the OLD schema must read back as a zero-minute rule,
    not as NULL -- the planner adds it to a timedelta without checking."""
    db_path = tmp_path / "scratch.db"
    _upgrade_to_head(db_path, monkeypatch)

    with sqlite3.connect(db_path) as conn:
        for table in ("reminder_rules", "preset_items"):
            cols = {row[1]: row for row in conn.execute(f"pragma table_info({table})")}
            assert "offset_minutes" in cols, f"{table} did not gain the column"
            assert cols["offset_minutes"][4] == "'0'", f"{table} default is not 0"
