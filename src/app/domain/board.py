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
