"""Which board column a concert belongs in.

Pure: takes the outcomes already recorded for one concert's rounds plus whether
any of its rounds is currently open, and returns the single column it shows in.
No I/O, no sqlalchemy -- service.py gathers the inputs.

Precedence is deliberate. A concert where you won round 2 and never applied to
round 3 belongs in "Won -- pay", not "Open now": the money you owe outranks the
round you could still enter, and a missed payment loses a ticket you already
have.
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


def column_for(
    outcomes: list[LotteryOutcome], has_open_round: bool
) -> Column | None:
    """The one column this concert shows in, or None to leave it off the board."""
    ranked = [_RANK[o] for o in outcomes if o in _RANK]
    if ranked:
        return max(ranked, key=lambda pair: pair[0])[1]
    return Column.OPEN if has_open_round else None
