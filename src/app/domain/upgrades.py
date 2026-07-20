"""Upgrade-round eligibility -- who may enter a nested second campaign.

A Japanese "upgrade" round is a lottery only holders of a qualifying round's
ticket may enter. Eligibility is derived, never stored: a user qualifies when
they already hold a secured (WON or PAID) ticket in a qualifying round.

Pure: plain-int inputs only, no I/O, no sqlalchemy. service.py gathers the
inputs from ``round.qualifiers`` and the user's ``RoundOutcome`` rows -- in
particular it decides which rounds count as secured (WON or PAID) when it
builds ``user_secured_round_ids``, so that WON/PAID-vs-everything-else rule
lives at the boundary, not here.

The empty-qualifier convention mirrors ``applies_to``'s empty-means-all-legs:
an upgrade round with no explicit qualifiers means "any secured ticket on this
concert qualifies", which is the common real case (an editor who adds an
upgrade round without picking qualifiers gets a live round, not a dead one).
"""


def is_upgrade_eligible(
    qualifying_round_ids: list[int],
    user_secured_round_ids: set[int],
) -> bool:
    """True if the user may enter this upgrade round.

    ``qualifying_round_ids`` is the upgrade round's qualifier set (may be
    empty). ``user_secured_round_ids`` is the set of rounds on this concert the
    user has WON or PAID.

    With qualifiers listed, the user is eligible when they secured at least one
    of them. With no qualifiers listed, any secured ticket on the concert makes
    them eligible.
    """
    if not qualifying_round_ids:
        return bool(user_secured_round_ids)
    return not user_secured_round_ids.isdisjoint(qualifying_round_ids)
