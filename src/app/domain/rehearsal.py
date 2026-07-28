"""What the harness EXPECTS to see, as a pure function.

`bot/messages.py:build_reminder_message` decides which buttons a reminder DM
carries from its anchor, the viewer's current outcome and how many legs the
round covers. Restating that here is deliberate duplication: an oracle that
derived its expectation from the code under test would agree with it however
wrong that code became.

So this file is read against `build_reminder_message` by eye, never imported
from it. If the two disagree, that disagreement is the finding -- do not
resolve it by making this call into the bot layer.
"""

from app.domain.types import Anchor, LotteryOutcome

# `bot/views.py:MAX_DAY_BUTTONS`, restated for the same reason the gating is.
# Discord allows 25 components across 5 rows, so a long tour is asked about a
# batch of legs at a time and the DM carries at most this many per-leg buttons.
MAX_DAY_BUTTONS = 4


def expected_buttons(
    anchor: Anchor, outcome: LotteryOutcome | None, covered_legs: int = 1
) -> tuple[str, ...]:
    """The custom_id stems a correct DM should carry for this row, in order.

    Link buttons (the ticket page, "Open on dekimasen.app") are omitted: they
    carry a URL and no custom_id, so there is no stem to name and nothing about
    them is gated.

    `covered_legs` is how many LIVE legs the round covers -- the same count
    `DueReminder.covered_days` carries. At two or more, a results DM asks leg
    by leg instead of offering the flat Won/Lost pair, because one round can
    come back won on one night and lost on another.
    """
    stems: list[str] = []
    if anchor is Anchor.CLOSES and outcome is None:
        stems += ["applied", "notapplied"]
    elif anchor is Anchor.RESULTS and outcome in (None, LotteryOutcome.APPLIED):
        if covered_legs >= 2:
            stems.append("wonall")
            stems += ["wonday"] * min(covered_legs, MAX_DAY_BUTTONS)
            stems.append("lostall")
        else:
            stems += ["won", "lost"]
    elif anchor is Anchor.PAYMENT and outcome is LotteryOutcome.WON:
        stems.append("paid")
    # Every reminder ends with one of these two, whatever came above it: a
    # CLOSES row asks for a day count, everything else snoozes by a day.
    stems.append("remindlater" if anchor is Anchor.CLOSES else "snooze")
    return tuple(stems)
