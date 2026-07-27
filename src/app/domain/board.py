"""Which board column a concert belongs in.

Pure: takes the outcomes already recorded for one concert's rounds plus whether
any of its rounds is currently open, and returns the single column it shows in.
No I/O, no sqlalchemy -- service.py gathers the inputs.

Each outcome is tagged with whether it belongs to an UPGRADE round, because
one placement rule turns on it: a won-but-unpaid upgrade outranks a secured
(PAID) base ticket. Winning an upgrade you must still pay for is money owed,
and money owed is the salient fact -- so such a concert belongs in "Won -- pay",
not "Secured", even though the base ticket is already in hand. The tag is the
ONLY thing that distinguishes an upgrade WON from a base WON here; every other
outcome ranks the same whether it is an upgrade round or not.

Precedence is otherwise deliberate. A concert where you won round 2 and never
applied to round 3 belongs in "Won -- pay", not "Open now": the money you owe
outranks the round you could still enter, and a missed payment loses a ticket
you already have.
"""

import enum
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app.domain.types import LotteryOutcome

# "Open now" is capped so a user following a large franchise does not turn the
# board back into the catalogue this split exists to separate out.
OPEN_COLUMN_LIMIT = 12


class Column(enum.StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    WON = "won"
    SECURED = "secured"


# Only outcomes that place a concert. LOST and NOT_APPLIED deliberately do not:
# neither is an end state, and neither says anything about what happens next.
_RANK: dict[LotteryOutcome, tuple[int, Column]] = {
    LotteryOutcome.APPLIED: (1, Column.APPLIED),
    LotteryOutcome.WON: (2, Column.WON),
    LotteryOutcome.PAID: (3, Column.SECURED),
}

# A won-but-unpaid UPGRADE round ranks ABOVE a PAID base ticket (rank 3): the
# outstanding upgrade payment pulls the whole concert into "Won -- pay". An
# upgrade APPLIED or PAID ranks exactly as its base twin (1 and 3), so it never
# demotes a secured base -- max() keeps the higher standing.
_UPGRADE_WON_RANK: tuple[int, Column] = (4, Column.WON)


def column_for(
    outcomes: list[tuple[LotteryOutcome, bool]], has_open_round: bool
) -> Column | None:
    """The one column this concert shows in, or None to leave it off the board.

    `outcomes` is a list of (outcome, is_upgrade) pairs -- one per round the
    user has recorded an outcome on. `is_upgrade` is True only for UPGRADE
    rounds, and matters for exactly one pair: a won-but-unpaid upgrade, which
    outranks a secured base ticket (see the module docstring).
    """
    ranked = []
    for outcome, is_upgrade in outcomes:
        if is_upgrade and outcome is LotteryOutcome.WON:
            ranked.append(_UPGRADE_WON_RANK)
        elif outcome in _RANK:
            ranked.append(_RANK[outcome])
    if ranked:
        return max(ranked, key=lambda pair: pair[0])[1]
    return Column.OPEN if has_open_round else None


# Countdown-pill urgency breakpoints, in time remaining until a card's next
# deadline. Under the first is "danger" (red), under the second is "off"
# (amber), beyond it fades to "quiet" (grey).
_URGENT_WITHIN = timedelta(days=1)
_SOON_WITHIN = timedelta(days=7)


def pill_tone(column: Column, next_deadline: datetime | None, now: datetime) -> str:
    """The CSS tone ("p-danger" | "p-off" | "p-quiet") for one board card's
    countdown pill.

    WON is pinned to "p-danger" no matter how many days remain: a won-but-
    unpaid round is money you could still lose, the same "money owed
    outranks everything" logic that gives WON its column precedence over a
    round you could still enter (see the module docstring and column_for).
    Every other column is driven purely by time left, so two OPEN cards with
    different deadlines now read differently instead of sharing one fixed
    tone.
    """
    if column is Column.WON:
        return "p-danger"
    if next_deadline is None:
        return "p-quiet"
    remaining = next_deadline - now
    if remaining <= _URGENT_WITHIN:
        return "p-danger"
    if remaining <= _SOON_WITHIN:
        return "p-off"
    return "p-quiet"


# How many ladder rungs one board card shows. Two, because two answer the only
# questions a card is asked: where do I stand, and what is next.
VISIBLE_RUNGS = 2

# Which rung STATES carry a standing, ranked the way column_for ranks the
# outcomes behind them (paid > won > applied). "lost" is absent for the same
# reason LOST places no column: it is not an end state. "live"/"todo" are
# absent because they are timing, not standing.
_RUNG_STANDING: dict[str, int] = {"applied": 1, "won": 2, "paid": 3}


def visible_rungs(
    rungs: Sequence[Any],
) -> tuple[list[tuple[int, Any]], int]:
    """The rungs one board card shows, numbered, plus how many it hid.

    A long campaign has a long ladder, and a card that renders all of it stops
    being scannable -- uniform card height is what makes the four columns read
    as a board. Two rungs carry the whole story: the one that EXPLAINS the
    card's column and the next thing the user could act on after it (the first
    following "live" or "todo"). Everything else is either settled history or
    too far ahead to matter, and the full ladder is one click away on the
    concert page. Nothing here is interactive, so nothing is lost by hiding it
    -- capture actions live on Coming up rows, never on a card.

    The state rung is picked by column_for's OWN precedence -- the highest
    standing recorded anywhere on the ladder (paid > won > applied), latest
    such rung wins a tie -- not by position. Taking the last non-"todo" rung
    instead inverts the card on the ordinary mid-campaign shape: a ladder of
    [lost, won, live] sits in "Won -- pay" because money owed outranks a round
    you could still enter, but position alone would surface the open round and
    hide the win, leaving the card naming a column nothing on it explains.
    Only when NO standing is recorded does position decide: the last non-"todo"
    rung, which picks the "live" one for an Open-now card and a "lost" one
    otherwise, and failing even that (nothing has happened at all) the head of
    the ladder, since there the whole story is what comes first.

    Returns `(pairs, hidden)` where each pair is `(position, rung)` with
    `position` the rung's ORIGINAL 1-based place in the full ladder. That
    matters: the card's mark for a "live"/"todo" rung is its ladder number, so
    filtering must not renumber what survives -- rungs 3 and 4 of five stay
    "3" and "4", not "1" and "2".

    `rungs` is typed loosely on purpose: it is `db.service.Rung`, and importing
    that here would drag the ORM into pure domain code. Only `.state` is read.
    """
    if len(rungs) <= VISIBLE_RUNGS:
        return [(i, r) for i, r in enumerate(rungs, start=1)], 0

    # The rung that explains the column: the highest standing on the ladder,
    # ties going to the later rung (the most advanced one at that standing).
    state_at = None
    best = 0
    for i, r in enumerate(rungs):
        standing = _RUNG_STANDING.get(r.state, 0)
        if standing and standing >= best:
            state_at, best = i, standing
    if state_at is None:
        # No standing recorded: fall back to position -- the last rung that is
        # not "todo", which is the open one on an Open-now card.
        for i, r in enumerate(rungs):
            if r.state != "todo":
                state_at = i

    if state_at is None:
        # Nothing has happened at all, so the head of the ladder is the story.
        keep = list(range(min(VISIBLE_RUNGS, len(rungs))))
    else:
        keep = [state_at]
        # What is next: the first thing after it the user could still act on.
        # Reachable now that the state rung can sit BEFORE a live/todo one.
        for i in range(state_at + 1, len(rungs)):
            if rungs[i].state in ("live", "todo"):
                keep.append(i)
                break

    # Indices are strictly increasing by construction (the second is searched
    # strictly after the first), so no sort or dedupe is needed; the slice is
    # belt and braces, pinning the output to the constant.
    keep = keep[:VISIBLE_RUNGS]
    return [(i + 1, rungs[i]) for i in keep], len(rungs) - len(keep)
