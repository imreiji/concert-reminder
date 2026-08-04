"""Migration test: clearing stale unsent day-anchored reminders on opted-out legs.

sync_rule now plans no day rows for a leg its user opted out of, but the queue
is a materialized outbox: rows planned BEFORE that fix stay queued until some
unrelated write resyncs the rule, and the scheduler delivers them meanwhile.
This migration deletes exactly those rows: UNSENT, day-anchored, where the
rule's own user holds a LegOptOut on that day. Sent rows are history and stay;
other users' rows and other days' rows stay; round-anchored rows were never
stale (the round pass ran at write time since per-leg opt-outs shipped).
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "aba3e97e4467"  # head immediately before this migration


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_stale_unsent_day_rows_on_opted_out_legs_are_deleted(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)  # plain connection: FKs off, no parent rows needed
    con.execute(
        "INSERT INTO reminder_rules (id, user_id, concert_id, round_id, anchor, "
        "offset_days, offset_hours, channel, created_at) "
        "VALUES (1, 42, NULL, NULL, 'event_start', 0, 0, 'dm', '2026-06-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO reminder_rules (id, user_id, concert_id, round_id, anchor, "
        "offset_days, offset_hours, channel, created_at) "
        "VALUES (2, 99, NULL, NULL, 'event_start', 0, 0, 'dm', '2026-06-01 00:00:00')"
    )
    # User 42 opted out of day 501; user 99 did not.
    con.execute(
        "INSERT INTO leg_opt_outs (id, user_id, concert_day_id, created_at) "
        "VALUES (1, 42, 501, '2026-06-01 00:00:00')"
    )
    rows = [
        # (id, rule_id, round_id, day_id, anchor, sent_at) -- expected fate in comment
        # row 3 uses a different anchor than row 1 solely to dodge
        # uq_reminder_queue_dedupe (rule_id, day_id, anchor) -- both are
        # otherwise the same rule/day, one unsent and one already sent.
        (1, 1, None, 501, "event_start", None),                  # DELETED: unsent, opted-out day
        (2, 1, None, 502, "event_start", None),                  # kept: other day
        (3, 1, None, 501, "opens", "2026-05-01 00:00:00"),       # kept: already sent (history)
        (4, 2, None, 501, "event_start", None),                  # kept: other user's rule
        (5, 1, 900, None, "event_start", None),                  # kept: round-anchored, day_id NULL
    ]
    for id_, rule_id, round_id, day_id, anchor, sent in rows:
        con.execute(
            "INSERT INTO reminder_queue (id, rule_id, round_id, day_id, anchor, "
            "fire_at_utc, sent_at_utc) VALUES (?, ?, ?, ?, ?, "
            "'2026-08-01 09:00:00', ?)",
            (id_, rule_id, round_id, day_id, anchor, sent),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    remaining = {r[0] for r in con.execute("SELECT id FROM reminder_queue").fetchall()}
    assert remaining == {2, 3, 4, 5}
    con.close()
