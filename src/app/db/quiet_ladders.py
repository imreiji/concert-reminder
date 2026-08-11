"""Round watch: which concerts in the catalogue have a ladder that has gone quiet.

Discovery's sweep answers "what exists that you are not tracking". This answers
"what changed about what you already track" -- a round announced after a concert
was imported is otherwise invisible, and a user who followed the right artist
can still miss the lottery.

Design: docs/superpowers/specs/2026-08-11-round-watch-design.md.

THE PREDICATE, in one place and only here:

    not all_legs_cancelled(days)
    and (a live dated leg is in the future  or  no live leg is dated at all)
    and next_anchor_at(concert, now) is None

Candidates come from one unfiltered `select(Concert)`, and every clause of the
predicate above -- the leg/cancellation checks as much as the anchor clause --
runs in Python against the loaded rows, because `is_round_cancelled` is
Python. The catalogue is small enough that a full scan is cheaper than a
second SQL transliteration of a Python predicate -- and a transliteration
would be free to drift from the original, which is exactly what promoting
`next_anchor_at` was meant to prevent.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.core import _jst_date, _now, all_legs_cancelled, next_anchor_at
from app.db.models import Concert


@dataclass(frozen=True)
class QuietRound:
    """One round the concert DOES carry, so a re-check does not re-propose it."""

    label: str
    kind: str
    opens_at_utc: datetime | None
    closes_at_utc: datetime | None
    results_at_utc: datetime | None
    payment_deadline_at_utc: datetime | None


@dataclass(frozen=True)
class QuietLadder:
    """One row of the worklist."""

    concert_id: int
    event_id: str
    title: str
    title_en: str | None
    leg_dates: tuple[date, ...]
    official_url: str | None
    eventernote_url: str | None
    source_url: str | None
    rounds: tuple[QuietRound, ...]
    quiet_since_utc: datetime | None
    rechecked_at_utc: datetime | None


def _not_yet_performed(concert: Concert, now: datetime) -> bool:
    """Has this concert still got a night ahead of it -- or no nights at all?

    `ConcertDay.starts_at_utc` is DATETIME NOT NULL, so there is no such thing
    as an undated leg: an empty list here means a concert with ZERO legs, which
    is exactly a skeleton import or a `duplicate_concert` clone, and exactly the
    case this feature exists for. The LATEST live leg decides, so a tour whose
    first night has passed and whose last has not is still awaiting a deadline.
    """
    live = [d.starts_at_utc for d in concert.days if not d.cancelled]
    return not live or max(live) > now


def is_quiet(concert: Concert, now: datetime) -> bool:
    """The predicate. `concert` must arrive with `days` and `rounds` loaded."""
    if all_legs_cancelled(concert.days):
        return False
    if not _not_yet_performed(concert, now):
        return False
    return next_anchor_at(concert, now) is None


def _row(concert: Concert) -> QuietLadder:
    return QuietLadder(
        concert_id=concert.id,
        event_id=concert.event_id,
        title=concert.title,
        title_en=concert.title_en,
        leg_dates=tuple(sorted(
            _jst_date(d.starts_at_utc) for d in concert.days if not d.cancelled
        )),
        official_url=concert.official_url,
        eventernote_url=concert.eventernote_url,
        source_url=concert.source_url,
        rounds=tuple(
            QuietRound(
                label=r.label,
                kind=r.kind.value,
                opens_at_utc=r.opens_at_utc,
                closes_at_utc=r.closes_at_utc,
                results_at_utc=r.results_at_utc,
                payment_deadline_at_utc=r.payment_deadline_at_utc,
            )
            for r in concert.rounds
        ),
        quiet_since_utc=concert.quiet_since_utc,
        rechecked_at_utc=concert.ladder_rechecked_at_utc,
    )


async def _all_concerts(session: AsyncSession) -> list[Concert]:
    """Every concert in the catalogue, ORM rows with days/rounds loaded.

    selectinload, not lazy access: ConcertDay.venue_tag is lazy="raise" and the
    surrounding code runs outside a greenlet-friendly context often enough that
    an accidental lazy load is a 500 rather than a slow query.

    Shared by `_quiet_concerts` (which then filters) and
    `reconcile_quiet_ladders` (which needs the NOT-quiet rows too, to clear
    their stamps) so the load itself is written once.

    `populate_existing=True`: without it, a Concert already in this session's
    identity map keeps whatever `days`/`rounds` collection it loaded LAST
    time, even though this is a brand new SELECT -- SQLAlchemy does not
    re-apply eager-loaded collections onto an already-loaded attribute unless
    told to. Defensive, not required by any known production call site: the
    trigger actually observed was a test calling `reconcile_quiet_ladders`
    twice on one session, where the second call read a stale empty `rounds`
    collection for a concert that had just grown a round in between. It
    matters in production too, not only in tests, because `SessionMaker`
    sets `expire_on_commit=False` (`db/session.py`) -- a session that survives
    past a commit keeps its identity map, and with it any stale collection.
    """
    return (await session.execute(
        select(Concert)
        .execution_options(populate_existing=True)
        .options(selectinload(Concert.days), selectinload(Concert.rounds))
    )).scalars().all()


async def _quiet_concerts(session: AsyncSession, now: datetime) -> list[Concert]:
    """Every concert the predicate matches, ORM rows with days/rounds loaded."""
    concerts = await _all_concerts(session)
    return [c for c in concerts if is_quiet(c, now)]


async def quiet_ladder_rows(
    session: AsyncSession, now: datetime | None = None
) -> list[QuietLadder]:
    """The worklist, longest-unattended first.

    Sort: never checked before ever checked, then oldest check, then longest
    quiet. A row is never hidden -- the stamp answers "have I looked at this",
    and hiding would silently promote it to "is this resolved", which it cannot
    answer.

    Derived live on every call, so the page never depends on the scheduler's
    reconcile pass having run.
    """
    now = now or _now()
    rows = [_row(c) for c in await _quiet_concerts(session, now)]
    far_past = datetime.min.replace(tzinfo=now.tzinfo)
    rows.sort(key=lambda r: (
        r.rechecked_at_utc is not None,
        r.rechecked_at_utc or far_past,
        r.quiet_since_utc or far_past,
    ))
    return rows


async def reconcile_quiet_ladders(
    session: AsyncSession, now: datetime | None = None
) -> list[QuietLadder]:
    """Bring the stamps in line with the predicate; return the NEWCOMERS.

    SELF-IDEMPOTENT, and that is what lets this run every tick instead of on
    the sweep's 24-hour clock: a stamped concert is no longer a newcomer, so a
    re-run announces nothing. The sweep needs a clock because its work is 86
    third-party fetches ending in a DM; this is a query and a diff.

    The CALLER commits, and must commit the queued notice in the SAME
    transaction as these stamps -- that pairing is what makes the notice
    exactly-once. Committing the stamp first would drop the DM on a crash;
    committing the notice first would repeat it.

    Do not wrap this call in `session.no_autoflush`: `_all_concerts`' SELECT
    runs with `populate_existing=True`, which overwrites in-memory attributes
    even when dirty, and with autoflush suppressed an unflushed stamp set
    just before this call is exactly what gets discarded.
    """
    now = now or _now()
    concerts = await _all_concerts(session)

    newcomers: list[Concert] = []
    for concert in concerts:
        if is_quiet(concert, now):
            if concert.quiet_since_utc is None:
                concert.quiet_since_utc = now
                newcomers.append(concert)
        elif concert.quiet_since_utc is not None:
            # Both stamps belong to the current quiet spell.
            concert.quiet_since_utc = None
            concert.ladder_rechecked_at_utc = None
    await session.flush()
    return [_row(c) for c in newcomers]


async def record_ladder_checked(
    session: AsyncSession, event_id: str, now: datetime | None = None
) -> bool:
    """Stamp "I have re-checked this one". False when no such concert exists.

    Sorts and dims; never hides. A concert whose ladder you checked in March
    genuinely does grow a 一般発売 in July.
    """
    concert = (await session.execute(
        select(Concert).where(Concert.event_id == event_id)
    )).scalar_one_or_none()
    if concert is None:
        return False
    concert.ladder_rechecked_at_utc = now or _now()
    await session.flush()
    return True
