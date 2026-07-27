"""Pure board placement. A concert lands in exactly one column, chosen by its
most advanced outcome across all rounds -- money owed outranks a round you
could still enter, and a won-but-unpaid upgrade outranks a secured base."""
from collections import namedtuple
from datetime import UTC, datetime, timedelta

from app.domain.board import Column, column_for, pill_tone, visible_rungs
from app.domain.types import LotteryOutcome as LO

# A stand-in for db/service.py's Rung, which is ORM-side: importing it here
# would drag sqlalchemy into a pure-domain test. visible_rungs only ever reads
# `.state`, so a namedtuple with the same field names is a faithful double.
R = namedtuple("R", "round_id label state detail is_upgrade", defaults=(False,))


def base(*outcomes):
    """The old shape: every outcome on an ordinary (non-upgrade) round."""
    return [(o, False) for o in outcomes]


def test_no_outcomes_and_an_open_round_is_open():
    assert column_for([], has_open_round=True) is Column.OPEN


def test_no_outcomes_and_nothing_open_is_absent():
    assert column_for([], has_open_round=False) is None


def test_applied_beats_open():
    assert column_for(base(LO.APPLIED), has_open_round=True) is Column.APPLIED


def test_won_beats_applied():
    assert column_for(base(LO.APPLIED, LO.WON), has_open_round=False) is Column.WON


def test_paid_beats_won():
    assert column_for(base(LO.WON, LO.PAID), has_open_round=False) is Column.SECURED


def test_won_beats_a_later_open_round():
    """You won round 2 and never applied to round 3. The payment you owe is
    the salient fact, not the round you could still enter."""
    assert column_for(base(LO.WON), has_open_round=True) is Column.WON


def test_lost_alone_with_an_open_round_is_open():
    """Losing a round is not an end state -- the next round is what matters."""
    assert column_for(base(LO.LOST), has_open_round=True) is Column.OPEN


def test_lost_alone_with_nothing_open_is_absent():
    assert column_for(base(LO.LOST), has_open_round=False) is None


def test_not_applied_everywhere_with_nothing_open_is_absent():
    assert column_for(base(LO.NOT_APPLIED, LO.NOT_APPLIED), has_open_round=False) is None


def test_not_applied_does_not_suppress_a_different_open_round():
    assert column_for(base(LO.NOT_APPLIED), has_open_round=True) is Column.OPEN


# ── Upgrade precedence: a won-but-unpaid upgrade outranks a secured base ─────


def test_base_paid_plus_upgrade_won_lands_in_won_pay():
    """The whole point: a PAID base with a WON (unpaid) upgrade owes money, so
    it belongs in Won -- pay, not Secured."""
    assert column_for(
        [(LO.PAID, False), (LO.WON, True)], has_open_round=False
    ) is Column.WON


def test_upgrade_paid_lands_in_secured():
    """Once the upgrade is PAID too, nothing is owed -- back to Secured."""
    assert column_for(
        [(LO.PAID, False), (LO.PAID, True)], has_open_round=False
    ) is Column.SECURED


def test_upgrade_applied_leaves_a_secured_base_in_secured():
    """An upgrade you only APPLIED to does not demote a secured base."""
    assert column_for(
        [(LO.PAID, False), (LO.APPLIED, True)], has_open_round=False
    ) is Column.SECURED


def test_upgrade_lost_leaves_base_standing_untouched():
    """A lost upgrade places nothing; the base PAID still reads Secured."""
    assert column_for(
        [(LO.PAID, False), (LO.LOST, True)], has_open_round=False
    ) is Column.SECURED


def test_base_paid_plus_upgrade_won_stays_won_even_with_an_open_round():
    """has_open_round never overrides a standing -- the upgrade WON still wins."""
    assert column_for(
        [(LO.PAID, False), (LO.WON, True)], has_open_round=True
    ) is Column.WON


# -- pill_tone: the board card's countdown-pill urgency ----------------------


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_won_column_is_always_danger_regardless_of_days_left():
    """Money owed stays urgent no matter how far off the payment deadline is --
    the same "money owed outranks everything" logic that gives WON its board
    precedence (column_for)."""
    far_off = NOW + timedelta(days=20)
    assert pill_tone(Column.WON, far_off, NOW) == "p-danger"


def test_under_a_day_left_is_danger():
    soon = NOW + timedelta(hours=6)
    assert pill_tone(Column.OPEN, soon, NOW) == "p-danger"


def test_within_a_week_is_off():
    mid = NOW + timedelta(days=4)
    assert pill_tone(Column.OPEN, mid, NOW) == "p-off"


def test_over_a_week_is_quiet():
    far = NOW + timedelta(days=14)
    assert pill_tone(Column.APPLIED, far, NOW) == "p-quiet"


def test_no_next_deadline_is_quiet():
    assert pill_tone(Column.APPLIED, None, NOW) == "p-quiet"


# -- visible_rungs: the board card's ladder cap -------------------------------


def test_visible_rungs_returns_everything_when_short():
    rungs = [R(1, "1次", "lost", None), R(2, "2次", "live", None)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [1, 2]
    assert hidden == 0


def test_visible_rungs_keeps_the_state_rung_and_the_next_actionable():
    rungs = [
        R(1, "最速", "lost", None), R(2, "1次", "lost", None),
        R(3, "2次", "applied", None), R(4, "一般", "todo", None),
        R(5, "FCFS", "todo", None),
    ]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [3, 4]      # original positions kept
    assert [r.label for _, r in visible] == ["2次", "一般"]
    assert hidden == 3


def test_visible_rungs_all_settled_ladder():
    rungs = [R(1, "1次", "lost", None), R(2, "2次", "lost", None),
             R(3, "一般", "paid", None)]
    visible, hidden = visible_rungs(rungs)
    assert [r.label for _, r in visible] == ["一般"]
    # The single-survivor case: it must still say "3", not renumber to 1.
    assert [p for p, _ in visible] == [3]
    assert hidden == 2


def test_visible_rungs_shows_the_outcome_that_placed_the_card_not_a_later_open_round():
    """The rung that EXPLAINS the column outranks a still-open later round.

    A concert where you won round 2 and never applied to round 3 sits in
    "Won -- pay" (column_for: money owed beats a round you could still enter),
    so the WON rung is the one that must survive the cap. Picking the last
    non-todo rung instead would surface the open round and hide the win --
    the card would name a column nothing on it explains."""
    rungs = [R(1, "1次", "lost", None), R(2, "2次", "won", None),
             R(3, "一般", "live", None)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [2, 3]
    assert [r.state for _, r in visible] == ["won", "live"]
    assert hidden == 1


def test_visible_rungs_applied_outranks_a_later_live_round():
    """The same inversion one rung earlier: APPLIED places the card, so it is
    the state rung, and the live round after it is what is next."""
    rungs = [R(1, "1次", "applied", None), R(2, "2次", "live", None),
             R(3, "一般", "todo", None)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [1, 2]
    assert [r.state for _, r in visible] == ["applied", "live"]
    assert hidden == 1


def test_visible_rungs_won_upgrade_outranks_a_paid_base_ticket():
    """The same inversion in the upgrade corner, where `column_for` ranks
    differently from a plain outcome ladder.

    A won-but-unpaid UPGRADE outranks a PAID base ticket (_UPGRADE_WON_RANK),
    so the card sits in "Won -- pay". Ranking the rungs by outcome alone picks
    the PAID rung and hides the won upgrade, leaving the card naming a column
    nothing on it explains -- exactly the defect the state-rung rule closed."""
    rungs = [R(1, "1次", "paid", None), R(2, "一般", "todo", None),
             R(3, "アップグレード", "won", None, True)]
    # The placement this has to mirror, asserted rather than assumed:
    assert column_for([(LO.PAID, False), (LO.WON, True)], has_open_round=True) is Column.WON

    visible, hidden = visible_rungs(rungs)

    assert [p for p, _ in visible] == [3]
    assert [r.state for _, r in visible] == ["won"]
    assert hidden == 2


def test_visible_rungs_applied_upgrade_does_not_outrank_a_paid_base_ticket():
    """Only a WON upgrade is special (column_for ranks an upgrade APPLIED at 1
    and PAID at 3, exactly as their base twins), so the PAID rung still wins."""
    rungs = [R(1, "1次", "paid", None), R(2, "一般", "todo", None),
             R(3, "アップグレード", "applied", None, True)]
    assert column_for(
        [(LO.PAID, False), (LO.APPLIED, True)], has_open_round=True
    ) is Column.SECURED

    visible, hidden = visible_rungs(rungs)

    assert [p for p, _ in visible] == [1, 2]
    assert hidden == 1


def test_visible_rungs_empty_ladder():
    assert visible_rungs([]) == ([], 0)


def test_visible_rungs_nothing_recorded_yet_keeps_the_head():
    rungs = [R(i, f"r{i}", "todo", None) for i in range(1, 6)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [1, 2]
    assert hidden == 3
