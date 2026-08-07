"""Per-leg opt-out suppression: the read-side planner pass.

A LegOptOut row prunes one ConcertDay for one user. Its rounds drop out of
that user's planner candidate list -- but only when EVERY leg a round
covers is opted out, the per-user analogue of the fully-cancelled-leg rule
(is_round_cancelled). A round covering two legs where the user opted out of
only one is still planned; an all-legs round (empty/None applies_to) is
tied to no specific leg, so no leg opt-out can suppress it. This is a
read-side filter folded into _apply_outcome_suppression -- no write path.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    ReminderQueue,
    ReminderRule,
    Round,
    Tag,
    TagSubscription,
)
from app.db.service import (
    RoundRow,
    board_cards,
    concert_next_moment,
    concert_round_rows,
    ensure_user,
    my_deadline_blocks,
    my_deadline_rows,
    set_concert_subscription,
    set_leg_opt_out,
    setup_application_rows,
    setup_tallies,
    sync_rule,
    user_calendar_events,
)
from app.domain.board import Column
from app.domain.types import (
    Anchor,
    LotteryOutcome,
    RoundKind,
    SubscriptionState,
    TagKind,
)

USER = 42
OTHER = 99
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


async def make_concert(s) -> Concert:
    concert = Concert(title="two-leg", event_id="two-leg", created_by=USER)
    s.add(concert)
    await s.flush()
    return concert


async def make_day(s, concert: Concert, label: str) -> ConcertDay:
    day = ConcertDay(concert_id=concert.id, label=label, starts_at_utc=dt(8, 1, 9))
    s.add(day)
    await s.flush()
    return day


async def make_round(s, concert: Concert, applies_to) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Round 1",
        closes_at_utc=dt(6, 25), applies_to=applies_to,
    )
    s.add(round_)
    await s.flush()
    return round_


async def make_rule(s, user: int, round_: Round) -> ReminderRule:
    rule = ReminderRule(user_id=user, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0)
    s.add(rule)
    await s.flush()
    return rule


async def queue_rows(s, rule: ReminderRule) -> list[ReminderQueue]:
    return list((await s.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars())


async def test_partial_opt_out_does_not_suppress(session):
    """Opt out of only ONE leg of a two-leg round -> the round is still
    planned. Same rule as cancellation: partial opt-out does not kill it."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)  # only one of two legs
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) != []


async def test_opt_out_only_leg_suppresses(session):
    """Opt out of the ONLY leg a round covers -> the round is suppressed."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_suppression_is_per_user(session):
    """A different user who did not opt out still gets the round planned."""
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    mine = await make_rule(session, USER, round_)
    theirs = await make_rule(session, OTHER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert await queue_rows(session, theirs) != []


async def test_all_legs_round_never_suppressed(session):
    """An all-legs round (empty applies_to) is tied to no specific leg, so
    no set of leg opt-outs can cover it -- mirrors is_round_cancelled
    leaving empty applies_to alone."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, None)  # "all legs" / General
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) != []


async def test_opting_out_every_leg_clears_already_queued_reminders(session):
    """Invariant 8: the WRITE re-syncs, it does not merely change what a later
    sync would decide.

    Suppression is a read-side pass folded into `sync_rule`, so before this the
    queue kept whatever it had been armed with: opt out of every leg through
    the dialog and the round's already-queued reminders still went out. The
    resync lives in `set_leg_opt_out` rather than in its callers, so both write
    surfaces (the day-result route and the leg opt-out route) get it.

    The partial step in the middle is the other half of the contract: the
    every-leg rule must not over-fire and clear a round the reader is still
    going to."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session, rule) != []  # armed

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert await queue_rows(session, rule) != []  # one leg to go: still armed

    await set_leg_opt_out(session, USER, b.id, True, now=NOW)
    assert await queue_rows(session, rule) == []


async def test_opting_back_in_re_arms_the_queue(session):
    """The same resync in the other direction -- an opt-out is reversible, and
    a reader who changes their mind is owed the reminder back without waiting
    for some unrelated edit to re-plan the round."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    rule = await make_rule(session, USER, round_)
    await sync_rule(session, rule, NOW)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert await queue_rows(session, rule) == []

    await set_leg_opt_out(session, USER, a.id, False, now=NOW)
    assert await queue_rows(session, rule) != []


async def test_opting_out_a_leg_that_does_not_exist_stays_a_silent_no_op(session):
    """The resync needs the day's concert, and a day id that names nothing has
    none. That stays forgiving rather than raising: ids reach these writers
    from form posts and Discord custom_ids."""
    await ensure_user(session, USER, "reiji")
    await set_leg_opt_out(session, USER, 987654, False, now=NOW)


async def test_all_legs_opted_out_suppresses(session):
    """Two legs, both opted out -> every leg the round covers is pruned, so
    it is suppressed."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await set_leg_opt_out(session, USER, b.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def make_event_rule(s, user: int, concert: Concert) -> ReminderRule:
    """A concert-wide 'remind me at show start' rule -- what plans DAY rows."""
    rule = ReminderRule(
        user_id=user, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=0
    )
    s.add(rule)
    await s.flush()
    return rule


async def test_day_rows_not_planned_for_opted_out_leg(session):
    """An event_start rule plans a show-start row per live leg -- but not for
    a leg this user opted out of. The other leg's row survives (partial
    opt-out never widens)."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    await sync_rule(session, rule, NOW)

    day_ids = {row.day_id for row in await queue_rows(session, rule)}
    assert day_ids == {b.id}


async def test_opting_out_clears_already_queued_day_rows(session):
    """Invariant 8's write-owns-the-resync, now covering DAY rows: the queue is
    a materialized outbox, so the set_leg_opt_out write itself must clear the
    show-start row -- before this, its own resync faithfully re-planned it."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {b.id}

    await set_leg_opt_out(session, USER, a.id, False, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}


async def test_day_row_suppression_is_per_user(session):
    """Another user who did not opt out keeps their show-start row."""
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    mine = await make_event_rule(session, USER, concert)
    theirs = await make_event_rule(session, OTHER, concert)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert {row.day_id for row in await queue_rows(session, theirs)} == {a.id}


async def test_calendar_feed_omits_opted_out_leg(session):
    """The owner's original report was 'shows up on feed'. The .ics feed now
    derives from standing over tracked concerts rather than reading
    reminder_queue back out, so it skips the opted-out leg directly (the same
    user_opted_out_day_ids every other surface asks) -- the claim is
    unchanged, only the mechanism behind it is."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    await set_concert_subscription(session, USER, concert.id, SubscriptionState.SUBSCRIBED)
    a = await make_day(session, concert, "Leg A")
    await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    labels = {e.label for e in await user_calendar_events(session, USER, now=NOW)}
    assert "Leg A" not in labels
    assert "Leg B" in labels


async def test_home_drops_the_show_row_for_an_opted_out_leg(session):
    """Coming up's EVENT_START rows (the show itself) skip a leg this reader
    opted out of; the other leg's row survives."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_day(session, concert, "Leg B")

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    labels = {r.deadline.label for r in rows}
    assert "Leg A" not in labels
    assert "Leg B" in labels


async def test_home_drops_a_round_whose_every_leg_is_opted_out(session):
    """A single-leg round on an opted-out leg must not reach Up next / Coming
    up with live capture buttons -- recording APPLIED there is irreversible."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    assert all(r.deadline.round_id is None for r in rows)


async def test_home_keeps_a_round_with_one_of_two_legs_opted_out(session):
    """The partial case survives BY DESIGN, mirroring the cancellation rule."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    assert round_.id in {r.deadline.round_id for r in rows}


async def test_home_blocks_vanish_when_everything_is_opted_out(session):
    """Fully opted out of the only leg: no round row, no show row, so the
    concert contributes no block at all -- Up next reads from these same
    rows, so this is also what keeps it off Up next."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    blocks = await my_deadline_blocks(session, USER, now=NOW, concert_ids={concert.id})
    assert blocks == []


async def test_board_drops_an_open_card_whose_only_leg_is_opted_out(session):
    """An open round on a fully opted-out leg must not keep a card in Open
    now. With no standing left either, the card leaves the board -- the same
    behavior a round cancelled by its legs already has."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])  # closes 6/25: open at NOW

    columns, open_total = await board_cards(
        session, USER, now=NOW, concert_ids={concert.id}
    )
    assert len(columns[Column.OPEN]) == 1  # sanity: it was on the board

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    columns, open_total = await board_cards(
        session, USER, now=NOW, concert_ids={concert.id}
    )
    assert columns[Column.OPEN] == []
    assert open_total == 0


async def test_board_keeps_a_card_with_one_of_two_legs_opted_out(session):
    """Partial opt-out: the round survives, so the card stays in Open now."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    columns, _ = await board_cards(session, USER, now=NOW, concert_ids={concert.id})
    assert len(columns[Column.OPEN]) == 1


async def test_next_for_you_skips_a_fully_opted_out_round(session):
    """The concert page's 'Next for you' pick must not lead with a round on a
    leg the reader said they are skipping. The row itself still renders (the
    page shows the whole campaign, and it is where you opt back in)."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert concert_next_moment(rows, now=NOW) is not None  # sanity: open round leads

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert rows != []  # the row still renders under its leg
    assert all(r.opted_out for r in rows)
    assert concert_next_moment(rows, now=NOW) is None


async def test_next_for_you_survives_a_partial_opt_out(session):
    """One of two legs opted out: the round still wants you (you are still
    going to the other night), so the pick stands."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert concert_next_moment(rows, now=NOW) is not None


async def test_catch_up_dialog_skips_an_opted_out_round(session):
    """pending_capture_row must not open the catch-up dialog for a round whose
    every leg the reader opted out of -- 'how did this round go?' about a show
    they are skipping is noise with an irreversible answer behind it."""
    from app.web.routes.concerts import pending_capture_row

    round_ = Round(
        concert_id=1, kind=RoundKind.LOTTERY_ROUND, label="R1",
        closes_at_utc=dt(5, 25), results_at_utc=dt(5, 26), applies_to=[1],
    )
    row = RoundRow(
        round_=round_, outcome=LotteryOutcome.APPLIED,
        can_capture=True, can_report_result=True, opted_out=True,
    )
    assert pending_capture_row({"leg_groups": [], "all_legs_rows": [row]}) is None


async def follow_concert(s, user: int, concert: Concert) -> None:
    """Make `concert` tracked for `user` the way production does: a followed
    tag attached to it (setup reads _tracked_upcoming_concerts)."""
    tag = Tag(name="g", kind=TagKind.GROUP)
    s.add(tag)
    await s.flush()
    s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
    s.add(TagSubscription(user_id=user, tag_id=tag.id))
    await s.flush()


async def test_setup_stops_asking_about_a_fully_opted_out_round(session):
    """Screen 2 must not offer 'did you apply?' -- an irreversible APPLIED
    behind it -- on a round whose every leg the reader opted out of."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    await follow_concert(session, USER, concert)

    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id in {r.round_.id for r in rows}  # sanity: asked before

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id not in {r.round_.id for r in rows}


async def test_setup_still_asks_on_a_partial_opt_out(session):
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    await follow_concert(session, USER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id in {r.round_.id for r in rows}


async def test_setup_tallies_exclude_a_fully_opted_out_round(session):
    """The reveal screen's numbers count the same round set screen 2 asks
    about: a skipped show is not a deadline you are waiting on."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])
    await follow_concert(session, USER, concert)

    tallies = await setup_tallies(session, USER, NOW)
    assert tallies.next_deadline_utc is not None  # sanity: counted before

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    tallies = await setup_tallies(session, USER, NOW)
    assert tallies.next_deadline_utc is None


async def test_round_fully_opted_out_predicate_ignores_all_legs_rounds():
    """The predicate reads RAW applies_to: empty/None (the all-legs / General
    convention) never suppresses, whatever the opt-out set holds."""
    from app.db.service import _round_fully_opted_out

    general = Round(concert_id=1, kind=RoundKind.LOTTERY_ROUND, label="G")
    assert not _round_fully_opted_out(general, {1, 2, 3})
    general.applies_to = []
    assert not _round_fully_opted_out(general, {1, 2, 3})
    general.applies_to = [2]
    assert _round_fully_opted_out(general, {1, 2, 3})
    assert not _round_fully_opted_out(general, {1, 3})
