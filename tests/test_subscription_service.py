"""Per-concert subscription overrides: the predicate and the writers.

A ConcertSubscription row is an *override* on the tag-derived default --
no row means "follow the tag default", an OPTED_OUT row prunes a
tag-matched concert, a SUBSCRIBED row forces one on with no matching tag.
`tracked_concert_ids` is the single place that override is applied, and
the concert-level writers re-sync the user's reminder rules so a pruned
concert stops reminding (invariant 2).
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertTag,
    ReminderQueue,
    ReminderRule,
    Round,
    Tag,
    TagSubscription,
)
from app.db.service import (
    clear_concert_subscription,
    concert_subscription_states,
    ensure_user,
    set_concert_subscription,
    set_leg_opt_out,
    sync_rule,
    tracked_concert_ids,
)
from app.domain.types import Anchor, RoundKind, SubscriptionState, TagKind

USER = 42
OTHER = 99
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


async def make_tag(s, name: str, *, user: int | None = None) -> Tag:
    tag = Tag(name=name, kind=TagKind.ARTIST)
    s.add(tag)
    await s.flush()
    if user is not None:
        s.add(TagSubscription(user_id=user, tag_id=tag.id))
        await s.flush()
    return tag


async def make_concert(s, event_id: str, tag: Tag | None = None) -> Concert:
    concert = Concert(title=event_id, event_id=event_id, created_by=USER)
    s.add(concert)
    await s.flush()
    if tag is not None:
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        await s.flush()
    return concert


# ── tracked_concert_ids: the override predicate ──────────────────────────


async def test_no_row_tracked_iff_tag_matches(session):
    """Unchanged baseline behaviour -- pin it so a later change can't
    silently break the "no override = follow the tag default" rule."""
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", user=USER)
    ignored = await make_tag(session, "Muse")
    mine = await make_concert(session, "aqours-live", followed)
    await make_concert(session, "muse-live", ignored)
    await make_concert(session, "untagged-live", None)

    assert await tracked_concert_ids(session, USER) == {mine.id}


async def test_opted_out_row_not_tracked_despite_tag(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-live", followed)

    await set_concert_subscription(session, USER, concert.id, SubscriptionState.OPTED_OUT)

    assert await tracked_concert_ids(session, USER) == set()


async def test_subscribed_row_tracked_without_any_tag(session):
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session, "untagged-live", None)

    await set_concert_subscription(session, USER, concert.id, SubscriptionState.SUBSCRIBED)

    assert await tracked_concert_ids(session, USER) == {concert.id}


async def test_clear_override_restores_tag_default(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-live", followed)
    await set_concert_subscription(session, USER, concert.id, SubscriptionState.OPTED_OUT)
    assert await tracked_concert_ids(session, USER) == set()

    await clear_concert_subscription(session, USER, concert.id)

    assert await tracked_concert_ids(session, USER) == {concert.id}


# ── invariant 2: opting out re-syncs the reminder queue ──────────────────


async def test_opt_out_resyncs_and_drops_queue_row(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-live", followed)
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Round 1",
        closes_at_utc=dt(6, 25),
    )
    session.add(round_)
    await session.flush()
    rule = ReminderRule(user_id=USER, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    # A reminder row exists for the concert before the opt-out.
    before = list((await session.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars())
    assert before != []

    await set_concert_subscription(session, USER, concert.id, SubscriptionState.OPTED_OUT)

    after = list((await session.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars())
    assert after == []


# ── concert_subscription_states: the read surface ────────────────────────


async def test_states_returns_exactly_the_users_overrides(session):
    await ensure_user(session, USER, "reiji")
    a = await make_concert(session, "a-live", None)
    b = await make_concert(session, "b-live", None)
    await make_concert(session, "c-live", None)  # no override -> absent

    await set_concert_subscription(session, USER, a.id, SubscriptionState.OPTED_OUT)
    await set_concert_subscription(session, USER, b.id, SubscriptionState.SUBSCRIBED)

    assert await concert_subscription_states(session, USER) == {
        a.id: SubscriptionState.OPTED_OUT,
        b.id: SubscriptionState.SUBSCRIBED,
    }


async def test_two_users_overrides_are_independent(session):
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session, "shared-live", None)

    await set_concert_subscription(session, USER, concert.id, SubscriptionState.SUBSCRIBED)
    await set_concert_subscription(session, OTHER, concert.id, SubscriptionState.OPTED_OUT)

    assert await concert_subscription_states(session, USER) == {
        concert.id: SubscriptionState.SUBSCRIBED
    }
    assert await concert_subscription_states(session, OTHER) == {
        concert.id: SubscriptionState.OPTED_OUT
    }
    assert await tracked_concert_ids(session, USER) == {concert.id}
    assert await tracked_concert_ids(session, OTHER) == set()


async def test_set_leg_opt_out_toggles_row_presence(session):
    """set_leg_opt_out is a presence toggle. (Suppression itself is Task 3 and
    the invariant-8 resync the writer runs is pinned in
    tests/test_leg_opt_out_suppression.py; here we only pin the add/delete.)"""
    from app.db.models import ConcertDay, LegOptOut

    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session, "aqours-live", None)
    day = ConcertDay(concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1, 9))
    session.add(day)
    await session.flush()

    async def opt_out_rows() -> list[LegOptOut]:
        return list((await session.execute(
            select(LegOptOut).where(LegOptOut.user_id == USER)
        )).scalars())

    await set_leg_opt_out(session, USER, day.id, True)
    assert len(await opt_out_rows()) == 1

    await set_leg_opt_out(session, USER, day.id, True)  # idempotent
    assert len(await opt_out_rows()) == 1

    await set_leg_opt_out(session, USER, day.id, False)
    assert await opt_out_rows() == []
