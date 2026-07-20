"""Pure board placement. A concert lands in exactly one column, chosen by its
most advanced outcome across all rounds -- money owed outranks a round you
could still enter, and a won-but-unpaid upgrade outranks a secured base."""
from datetime import UTC, datetime, timedelta

from app.domain.board import Column, column_for, pill_tone
from app.domain.types import LotteryOutcome as LO


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
