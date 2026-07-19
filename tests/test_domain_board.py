"""Pure board placement. A concert lands in exactly one column, chosen by its
most advanced outcome across all rounds -- money owed outranks a round you
could still enter."""
from app.domain.board import Column, column_for
from app.domain.types import LotteryOutcome as LO


def test_no_outcomes_and_an_open_round_is_open():
    assert column_for([], has_open_round=True) is Column.OPEN


def test_no_outcomes_and_nothing_open_is_absent():
    assert column_for([], has_open_round=False) is None


def test_applied_beats_open():
    assert column_for([LO.APPLIED], has_open_round=True) is Column.APPLIED


def test_won_beats_applied():
    assert column_for([LO.APPLIED, LO.WON], has_open_round=False) is Column.WON


def test_paid_beats_won():
    assert column_for([LO.WON, LO.PAID], has_open_round=False) is Column.SECURED


def test_won_beats_a_later_open_round():
    """You won round 2 and never applied to round 3. The payment you owe is
    the salient fact, not the round you could still enter."""
    assert column_for([LO.WON], has_open_round=True) is Column.WON


def test_lost_alone_with_an_open_round_is_open():
    """Losing a round is not an end state -- the next round is what matters."""
    assert column_for([LO.LOST], has_open_round=True) is Column.OPEN


def test_lost_alone_with_nothing_open_is_absent():
    assert column_for([LO.LOST], has_open_round=False) is None


def test_not_applied_everywhere_with_nothing_open_is_absent():
    assert column_for([LO.NOT_APPLIED, LO.NOT_APPLIED], has_open_round=False) is None


def test_not_applied_does_not_suppress_a_different_open_round():
    assert column_for([LO.NOT_APPLIED], has_open_round=True) is Column.OPEN
