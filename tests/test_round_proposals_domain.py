from datetime import UTC, datetime

import pytest

from app.domain.round_evidence import ProposedRound
from app.domain.round_proposals import (
    CLOSES_AT_FIELD,
    OPENS_AT_FIELD,
    PAYMENT_AT_FIELD,
    RESULTS_AT_FIELD,
    HeldRound,
    classify_proposals,
    dedupe_key,
)
from app.domain.timezones import utc_to_jst

OPENS = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
CLOSES = datetime(2026, 9, 10, 1, 0, tzinfo=UTC)
RESULTS = datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
PAYMENT = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)

_FIELD_BY_NAME = {
    OPENS_AT_FIELD: "apply_opens_jst",
    CLOSES_AT_FIELD: "apply_closes_jst",
    RESULTS_AT_FIELD: "results_jst",
    PAYMENT_AT_FIELD: "payment_deadline_jst",
}


@pytest.fixture
def make_proposed():
    """Build a real `ProposedRound` whose four JST-text fields round-trip back
    to the given UTC instants through `round_proposals.proposed_stamp_utc`.

    `ProposedRound.data` carries each anchor as JST TEXT in the draft
    vocabulary's "%Y-%m-%d %H:%M" shape (see round_completion.py), never a
    `datetime` -- so this fixture writes exactly that shape rather than
    stuffing a `datetime` into the mapping directly. Any anchor left `None`
    is simply absent from `data`, matching a page that never mentioned it.
    """

    def _make(
        label: str,
        *,
        opens_at_utc: datetime | None = None,
        closes_at_utc: datetime | None = None,
        results_at_utc: datetime | None = None,
        payment_deadline_at_utc: datetime | None = None,
    ) -> ProposedRound:
        data: dict = {"label": label}
        for field, value in (
            (OPENS_AT_FIELD, opens_at_utc),
            (CLOSES_AT_FIELD, closes_at_utc),
            (RESULTS_AT_FIELD, results_at_utc),
            (PAYMENT_AT_FIELD, payment_deadline_at_utc),
        ):
            if value is not None:
                jst = utc_to_jst(value)
                data[field] = jst.strftime("%Y-%m-%d %H:%M")
        return ProposedRound(data=data, evidence={}, label=label)

    return _make


def test_the_key_folds_widths_and_spacing_so_one_round_is_one_row():
    """Mutation: dropping the normalisation. Then a page that renders
    '１次先行' one day and '1次先行 ' the next accumulates a second proposal
    for the same round, every day, forever."""
    a = dedupe_key("１次先行", OPENS)
    b = dedupe_key(" 1次先行  ", OPENS)
    assert a == b


def test_a_round_with_no_open_time_still_dedupes_on_its_label():
    """Mutation: making the key None/empty when opens_at is None -- every poll
    then adds another copy of the same undated round."""
    a = dedupe_key("一般発売", None)
    b = dedupe_key("一般発売", None)
    assert a == b and a != ""


def test_a_moved_open_time_is_a_DIFFERENT_key():
    """Deliberate, and the spec's reasoning: dismissing 'opens Sept 3' is not
    a judgement on 'opens Sept 10'. Mutation: keying on the label alone, which
    would let one dismissal swallow a corrected deadline."""
    a = dedupe_key("1次先行", OPENS)
    b = dedupe_key("1次先行", datetime(2026, 9, 10, 1, 0, tzinfo=UTC))
    assert a != b


def test_a_held_round_with_seconds_still_dedupes_against_a_proposed_one_without(
    make_proposed,
):
    """CRITICAL (review round 1, phase 1): `HeldRound.opens_at_utc` can carry
    seconds -- it comes off a bare `Round.opens_at_utc` column, and
    `yaml_import._dt` accepts a YAML timestamp WITH seconds verbatim, so a
    hand- or AI-authored draft can seed a live round with one. The proposed
    side always yields ':00' seconds/microseconds, since it parses
    "%Y-%m-%d %H:%M" text. Mutation: dropping `dedupe_key`'s
    `.replace(second=0, microsecond=0)` truncation. Then the same real-world
    round produces two different keys depending on which side of the diff
    it's read from, and is re-proposed every poll, forever."""
    held = [HeldRound("1次先行", opens_at_utc=datetime(2026, 9, 3, 1, 0, 30, tzinfo=UTC))]
    proposed = [
        make_proposed("1次先行", opens_at_utc=datetime(2026, 9, 3, 1, 0, 0, tzinfo=UTC))
    ]
    result = classify_proposals(held, proposed)
    assert result.fresh == [] and result.changed == []


# --- classify_proposals: the three-way split ---------------------------


def test_an_identical_round_is_neither_fresh_nor_changed(make_proposed):
    """Mutation: returning everything as fresh. The pass would re-propose
    every round the concert already holds, every day."""
    held = [
        HeldRound(
            "1次先行",
            opens_at_utc=OPENS,
            closes_at_utc=CLOSES,
            results_at_utc=RESULTS,
            payment_deadline_at_utc=PAYMENT,
        )
    ]
    proposed = [
        make_proposed(
            "1次先行",
            opens_at_utc=OPENS,
            closes_at_utc=CLOSES,
            results_at_utc=RESULTS,
            payment_deadline_at_utc=PAYMENT,
        )
    ]
    result = classify_proposals(held, proposed)
    assert result.fresh == []
    assert result.changed == []


def test_a_moved_closing_date_is_CHANGED_not_dropped(make_proposed):
    """The case the phase exists for: a concert is quiet precisely because its
    stored closes is past. Mutation: comparing only opens -- which is exactly
    what `dedupe_key` does, and what shipped in phase 1. Every other field
    (results, payment) is seeded IDENTICAL so only the closes comparison can
    make this pass."""
    held = [
        HeldRound(
            "1次先行",
            opens_at_utc=OPENS,
            closes_at_utc=CLOSES,
            results_at_utc=RESULTS,
            payment_deadline_at_utc=PAYMENT,
        )
    ]
    moved_closes = datetime(2026, 9, 12, 1, 0, tzinfo=UTC)
    proposed = make_proposed(
        "1次先行",
        opens_at_utc=OPENS,
        closes_at_utc=moved_closes,
        results_at_utc=RESULTS,
        payment_deadline_at_utc=PAYMENT,
    )
    result = classify_proposals(held, [proposed])
    assert result.fresh == []
    assert result.changed == [proposed]


def test_a_moved_results_date_alone_is_CHANGED(make_proposed):
    """Mutation: comparing closes but not results. Closes (and payment) are
    seeded IDENTICAL so this can only pass by comparing results."""
    held = [
        HeldRound(
            "1次先行",
            opens_at_utc=OPENS,
            closes_at_utc=CLOSES,
            results_at_utc=RESULTS,
            payment_deadline_at_utc=PAYMENT,
        )
    ]
    moved_results = datetime(2026, 9, 17, 1, 0, tzinfo=UTC)
    proposed = make_proposed(
        "1次先行",
        opens_at_utc=OPENS,
        closes_at_utc=CLOSES,
        results_at_utc=moved_results,
        payment_deadline_at_utc=PAYMENT,
    )
    result = classify_proposals(held, [proposed])
    assert result.fresh == []
    assert result.changed == [proposed]


def test_a_moved_payment_deadline_alone_is_CHANGED(make_proposed):
    """Mutation: comparing results but not payment. Same shape: every other
    field (opens, closes, results) is seeded IDENTICAL."""
    held = [
        HeldRound(
            "1次先行",
            opens_at_utc=OPENS,
            closes_at_utc=CLOSES,
            results_at_utc=RESULTS,
            payment_deadline_at_utc=PAYMENT,
        )
    ]
    moved_payment = datetime(2026, 9, 25, 1, 0, tzinfo=UTC)
    proposed = make_proposed(
        "1次先行",
        opens_at_utc=OPENS,
        closes_at_utc=CLOSES,
        results_at_utc=RESULTS,
        payment_deadline_at_utc=moved_payment,
    )
    result = classify_proposals(held, [proposed])
    assert result.fresh == []
    assert result.changed == [proposed]


def test_a_genuinely_new_round_is_FRESH(make_proposed):
    """Mutation: returning [] for fresh unconditionally."""
    held = [HeldRound("1次先行", opens_at_utc=OPENS)]
    fresh = make_proposed("2次先行", opens_at_utc=datetime(2026, 9, 20, 1, 0, tzinfo=UTC))
    result = classify_proposals(held, [fresh])
    assert result.fresh == [fresh]
    assert result.changed == []


def test_order_is_preserved_within_each_bucket(make_proposed):
    """Mutation: building the buckets from a set. The digest and the draft page
    both read these in order, and a set makes that order arbitrary per run."""
    held: list[HeldRound] = []
    third = make_proposed("3次先行", opens_at_utc=OPENS)
    first = make_proposed("1次先行", opens_at_utc=OPENS)
    second = make_proposed("2次先行", opens_at_utc=OPENS)
    result = classify_proposals(held, [third, first, second])
    assert result.fresh == [third, first, second]
