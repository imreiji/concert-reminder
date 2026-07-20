"""Pure upgrade-round eligibility.

A user may enter an upgrade round only if they already hold a secured
(WON or PAID) ticket in a qualifying round. The qualifying rounds are the
upgrade round's explicit qualifier set; an empty qualifier set means "any
secured ticket on this concert qualifies" (mirroring applies_to's
empty-means-all-legs).

The WON/PAID-vs-everything-else rule lives in how the caller builds
user_secured_round_ids: only WON and PAID rounds go in it. These tests build
that set the same way the caller (service.py) does, so the distinction is
visible here.
"""

from app.domain.types import LotteryOutcome as LO
from app.domain.upgrades import is_upgrade_eligible

# WON and PAID secure a ticket; APPLIED/LOST/NOT_APPLIED do not.
_SECURED = {LO.WON, LO.PAID}


def _secured_ids(outcome_by_round):
    """Mirror the caller: keep only the rounds the user WON or PAID."""
    return {rid for rid, o in outcome_by_round.items() if o in _SECURED}


def test_non_empty_qualifiers_secured_one_of_them_is_eligible():
    secured = _secured_ids({1: LO.WON, 2: LO.NOT_APPLIED})
    assert is_upgrade_eligible([1, 2], secured) is True


def test_non_empty_qualifiers_paid_a_qualifier_is_eligible():
    secured = _secured_ids({1: LO.PAID})
    assert is_upgrade_eligible([1, 2], secured) is True


def test_non_empty_qualifiers_secured_round_outside_the_set_is_not_eligible():
    # Won round 9, but only rounds 1 and 2 qualify.
    secured = _secured_ids({9: LO.WON})
    assert is_upgrade_eligible([1, 2], secured) is False


def test_non_empty_qualifiers_only_applied_to_a_qualifier_is_not_eligible():
    # Applied to qualifier 1 but has not won or paid -> not secured.
    secured = _secured_ids({1: LO.APPLIED})
    assert is_upgrade_eligible([1, 2], secured) is False


def test_empty_qualifiers_secured_any_round_is_eligible():
    secured = _secured_ids({7: LO.WON})
    assert is_upgrade_eligible([], secured) is True


def test_empty_qualifiers_secured_nothing_is_not_eligible():
    secured = _secured_ids({7: LO.LOST, 8: LO.NOT_APPLIED})
    assert is_upgrade_eligible([], secured) is False


def test_empty_qualifiers_only_applied_somewhere_is_not_eligible():
    secured = _secured_ids({7: LO.APPLIED})
    assert is_upgrade_eligible([], secured) is False
