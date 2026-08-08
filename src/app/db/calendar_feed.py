"""The personal `.ics` subscription: token minting and the event landscape.

`user_calendar_events` reads no `reminder_queue` at all -- the feed is the
user's standing-aware LANDSCAPE, not a mirror of their reminder rules (ruling
2026-08-04). Its `locale` parameter is explicit and `None` means canonical.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.core import (
    _eligible_upgrade_ids,
    _now,
    _qualifiers_by_upgrade_round,
    _result_moment,
    _round_fully_opted_out,
    all_legs_cancelled,
    covered_round_ids_by_concert,
    is_round_cancelled,
    tracked_concert_ids,
    user_opted_out_day_ids,
)
from app.db.models import (
    Concert,
    ConcertDay,
    Round,
    RoundOutcome,
    User,
)
from app.db.tokens import hash_token
from app.domain.types import (
    Anchor,
    LotteryOutcome,
    RoundKind,
)
from app.i18n import loc_field

# ── Personal calendar feed ────────────────────────────────────────────────


async def generate_calendar_token(session: AsyncSession, user_id: int) -> str:
    """(Re)generate the user's calendar-feed token. Only the hash is stored
    (same pattern as WebSession.token_hash) -- the raw value is returned
    once, for the caller to hand back in a URL; it can never be recovered
    afterward, only replaced by generating a new one. Callers are always
    behind require_user, so the row already exists (ensure_user ran at
    login) -- fetched plainly here to avoid overwriting the username with
    a blank one."""
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"no such user: {user_id}")
    token = secrets.token_urlsafe(32)
    user.calendar_token_hash = hash_token(token)
    await session.flush()
    return token


async def get_user_by_calendar_token(session: AsyncSession, token: str) -> User | None:
    res = await session.execute(
        select(User).where(User.calendar_token_hash == hash_token(token))
    )
    return res.scalar_one_or_none()


@dataclass(frozen=True)
class CalendarEvent:
    """One entry on a user's personal feed: a show date (EVENT_START) or a
    deadline that still needs them, at its real moment -- never a reminder's
    lead time. `anchor` says WHICH of a round's moments this is, so a round
    contributing both its open and its close stays distinguishable once
    rendered."""

    concert_title: str
    label: str
    at_utc: datetime
    anchor: Anchor
    url: str | None = None
    notes: str | None = None


async def user_calendar_events(
    session: AsyncSession, user_id: int, now: datetime | None = None,
    locale: str | None = None,
) -> list[CalendarEvent]:
    """The user's standing-aware landscape (spec 2026-08-04): every TRACKED
    concert's live show dates, plus each surviving round's next moments
    selected by this user's outcome on it -- no outcome: opens + closes;
    APPLIED: the result moment (_result_moment, results falling back to the
    close); WON: the payment deadline; LOST/NOT_APPLIED/PAID: nothing, and a
    LOST round's auto-armed successor contributes its own opens/closes as an
    ordinary no-outcome round. Future-only throughout.

    Reminder RULES play no part -- they control when Discord DMs fire, and
    this used to read reminder_queue, which made a sparse preset read as a
    broken calendar. Every exclusion is a shared helper other surfaces
    already use (tracked_concert_ids, opt-outs, cancellation, coverage,
    upgrade eligibility); nothing here invents a rule.

    `locale` localizes titles/labels for a locale-aware caller (the
    /mydeadlines cog passes the recipient's language). Left None by the .ics
    feed, which has no viewer locale -- that path keeps the canonical text.
    """
    now = now or _now()

    def _title(concert: Concert | None) -> str:
        if concert is None:
            return "Concert"
        return loc_field(concert, "title", locale) if locale else concert.title

    def _label(obj: Round | ConcertDay) -> str:
        """Same rule as _title: an explicit caller locale localizes, None
        (the .ics feed) keeps the canonical text. Deliberately NOT
        get_locale() -- the feed must stay byte-identical per viewer."""
        return loc_field(obj, "label", locale) if locale else obj.label

    tracked = await tracked_concert_ids(session, user_id)
    if not tracked:
        return []
    concerts = list((await session.execute(
        select(Concert)
        .where(Concert.id.in_(tracked))
        .options(selectinload(Concert.days), selectinload(Concert.rounds))
    )).scalars())

    # Per-user suppression inputs, each ONE batched query over the whole set.
    opted_out = await user_opted_out_day_ids(
        session, user_id, [d.id for c in concerts for d in c.days]
    )
    all_round_ids = [r.id for c in concerts for r in c.rounds]
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome
        for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id,
                RoundOutcome.round_id.in_(all_round_ids),
            )
        )).scalars()
    } if all_round_ids else {}
    # Coverage: only concerts where the user holds a secured ticket can
    # produce a covered round -- same short-circuit my_deadline_rows uses.
    secured_concert_ids = set((await session.execute(
        select(Round.concert_id)
        .join(RoundOutcome, RoundOutcome.round_id == Round.id)
        .where(
            RoundOutcome.user_id == user_id,
            Round.concert_id.in_(tracked),
            RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
        )
    )).scalars())
    covered: set[int] = set()
    for ids in (await covered_round_ids_by_concert(
        session, user_id, secured_concert_ids
    )).values():
        covered |= ids
    qualifiers_by_round = await _qualifiers_by_upgrade_round(
        session,
        [r.id for c in concerts for r in c.rounds if r.kind is RoundKind.UPGRADE],
    )

    events: list[CalendarEvent] = []
    for c in concerts:
        if all_legs_cancelled(c.days):
            continue  # the show is off: nothing on it is a question
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        title = _title(c)

        for d in c.days:
            if d.cancelled or d.id in opted_out or d.starts_at_utc <= now:
                continue
            events.append(CalendarEvent(
                concert_title=title, label=_label(d),
                at_utc=d.starts_at_utc, anchor=Anchor.EVENT_START,
            ))

        # Upgrade eligibility is concert-scoped (a secured ticket elsewhere
        # must not qualify an empty-qualifier upgrade here).
        c_outcomes = {r.id: outcomes[r.id] for r in c.rounds if r.id in outcomes}
        eligible_up = _eligible_upgrade_ids(list(c.rounds), c_outcomes, qualifiers_by_round)
        for r in c.rounds:
            if is_round_cancelled(r, cancelled_day_ids):
                continue
            if _round_fully_opted_out(r, opted_out):
                continue
            if r.id in covered:
                continue
            if r.kind is RoundKind.UPGRADE and r.id not in eligible_up:
                continue
            outcome = outcomes.get(r.id)
            if outcome is None:
                moments = [(Anchor.OPENS, r.opens_at_utc), (Anchor.CLOSES, r.closes_at_utc)]
            elif outcome is LotteryOutcome.APPLIED:
                # The one shared "when does the result become knowable" rule.
                moments = [(Anchor.RESULTS, _result_moment(r))]
            elif outcome is LotteryOutcome.WON:
                moments = [(Anchor.PAYMENT, r.payment_deadline_at_utc)]
            else:  # LOST / NOT_APPLIED / PAID: settled, nothing left to act on
                moments = []
            for anchor, ts in moments:
                if ts is None or ts <= now:
                    continue
                events.append(CalendarEvent(
                    concert_title=title, label=_label(r), at_utc=ts,
                    anchor=anchor, url=r.url, notes=r.notes,
                ))

    events.sort(key=lambda e: e.at_utc)
    return events
