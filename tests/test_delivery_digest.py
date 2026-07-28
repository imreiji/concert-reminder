"""The digest body: impersonal, failure-first, and bounded."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, DeliveryLog, Notification, User
from app.db.service import queue_delivery_digest
from app.domain.digest import MAX_SENT_GROUPS, DeliveryFact, build_digest
from app.domain.types import Anchor, DeliveryOutcome, DeliverySource

BATCH = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


def _fact(user_id, outcome=DeliveryOutcome.SUCCESS, anchor=Anchor.CLOSES, **kw):
    base = dict(
        source=DeliverySource.REMINDER,
        outcome=outcome,
        user_id=user_id,
        concert_title="Snow Miku 2027",
        leg_label="Day 1",
        round_label="一次先行",
        anchor=anchor,
        note_kind=None,
        concert_id=1,
        round_id=1,
        day_id=1,
    )
    base.update(kw)
    return DeliveryFact(**base)


def test_header_counts_sends_and_distinct_users():
    body = build_digest([_fact(1), _fact(2), _fact(2)], BATCH)
    assert "3 sent" in body
    assert "2 users" in body
    assert "2026-07-28 14:23 UTC" in body


def test_no_warning_marker_when_nothing_failed():
    assert "⚠" not in build_digest([_fact(1)], BATCH)


def test_failures_lead_and_are_marked():
    body = build_digest([_fact(1), _fact(2, outcome=DeliveryOutcome.FORBIDDEN)], BATCH)
    assert "⚠" in body
    assert "1 failed" in body
    assert body.index("FAILED") < body.index("SENT")
    assert "forbidden" in body


def test_sent_rows_group_with_a_recipient_count():
    """The count IS the anomaly detector -- x40 on a three-user app is the
    tell. A per-recipient list would bury it and blow Discord's 2000 chars."""
    body = build_digest([_fact(i) for i in range(1, 6)], BATCH)
    assert "×5" in body
    assert body.count("一次先行") == 1  # one group line, not five


def test_different_anchors_are_different_groups():
    body = build_digest(
        [_fact(1, anchor=Anchor.CLOSES), _fact(2, anchor=Anchor.RESULTS)], BATCH
    )
    assert "closes" in body.lower()
    assert "results" in body.lower()


def test_notification_rows_group_by_kind_not_anchor():
    body = build_digest(
        [
            _fact(
                1,
                source=DeliverySource.NOTIFICATION,
                anchor=None,
                note_kind="new_event",
                leg_label=None,
                round_label=None,
            )
        ],
        BATCH,
    )
    assert "new_event" in body


def test_sent_groups_are_capped_with_a_remainder_line():
    """Distinct groups need distinct round_IDs, not distinct labels -- grouping
    keys on ids, so varying only the label would collapse to a single group."""
    facts = [
        _fact(i, round_id=i, round_label=f"round {i}") for i in range(MAX_SENT_GROUPS + 3)
    ]
    body = build_digest(facts, BATCH)
    assert "+3 more groups" in body


def test_two_languages_of_one_concert_stay_one_group():
    """The reason grouping keys on ids. due_reminders resolves titles and
    labels with loc_field(..., user.language), so the SAME reminder reaching a
    Japanese reader and an English reader arrives with different label text.
    Grouping on that text would report x1 and x1 instead of x2 -- halving the
    recipient count that is the entire anomaly signal. A 40-recipient
    mis-fire split across three languages is the case that matters."""
    facts = [
        _fact(1, concert_title="Snow Miku 2027", round_label="1st lottery"),
        _fact(2, concert_title="スノーミク2027", round_label="一次先行"),
        _fact(3, concert_title="初音未来演唱会2027", round_label="第一轮抽选"),
    ]
    body = build_digest(facts, BATCH)
    assert "×3" in body
    assert "more groups" not in body
    # Exactly one SENT line, whichever language it happens to render in.
    sent_lines = [ln for ln in body.splitlines() if ln.strip().startswith("×")]
    assert len(sent_lines) == 1


def test_never_contains_a_user_id():
    """Counts in the DM, names in the app: identity belongs on
    /admin/deliveries, inside POST /me/delete's reach, not in Discord history
    that no deletion path can touch."""
    body = build_digest([_fact(123456789012345678)], BATCH)
    assert "123456789012345678" not in body


def test_empty_facts_produce_no_digest():
    assert build_digest([], BATCH) == ""


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_users(session):
    session.add_all([User(discord_id=1, username="a"), User(discord_id=2, username="b")])
    await session.flush()


async def _one_success_row(session):
    row = DeliveryLog(
        batch_at_utc=BATCH,
        user_id=1,
        source=DeliverySource.REMINDER,
        outcome=DeliveryOutcome.SUCCESS,
        anchor=Anchor.CLOSES,
        concert_title="Snow Miku 2027",
        leg_label="Day 1",
        round_label="一次先行",
        sent_at_utc=BATCH,
    )
    session.add(row)
    await session.flush()
    return [row]


@pytest.mark.asyncio
async def test_queues_one_notification_per_admin(db, monkeypatch):
    """Same shape as evaluate_and_alert: one Notification per admin id, with
    concert_id=None so it falls through _notification_context to the
    plain-text path and the send code needs no changes."""
    monkeypatch.setattr(settings, "discord_token", "x")  # bot_enabled
    monkeypatch.setattr(settings, "admin_whitelist", "1,2")
    async with db() as s:
        await _seed_users(s)
        rows = await _one_success_row(s)
        n = await queue_delivery_digest(s, BATCH, rows)
        await s.commit()
        assert n == 2
        notes = (await s.execute(select(Notification))).scalars().all()
        assert {x.user_id for x in notes} == {1, 2}
        assert all(x.kind == "delivery_digest" for x in notes)
        assert all(x.concert_id is None for x in notes)
        assert all("1 sent" in x.body for x in notes)


@pytest.mark.asyncio
async def test_queues_nothing_when_the_bot_is_disabled(db, monkeypatch):
    """evaluate_and_alert's reason: without this, every local dev run
    accumulates junk notifications nobody will ever receive."""
    monkeypatch.setattr(settings, "discord_token", "")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with db() as s:
        await _seed_users(s)
        rows = await _one_success_row(s)
        assert await queue_delivery_digest(s, BATCH, rows) == 0
        await s.commit()
        assert (await s.execute(select(Notification))).all() == []


@pytest.mark.asyncio
async def test_queues_nothing_for_an_empty_batch(db, monkeypatch):
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with db() as s:
        await _seed_users(s)
        assert await queue_delivery_digest(s, BATCH, []) == 0
