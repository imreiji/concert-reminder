from datetime import UTC, datetime

import pytest

from app.domain.round_evidence import ProposedRound
from app.domain.round_proposals import HeldRound, dedupe_key, new_proposals
from app.domain.timezones import utc_to_jst


@pytest.fixture
def make_proposed():
    """Build a real `ProposedRound` whose `data["apply_opens_jst"]` round-trips
    back to the given UTC instant through `round_proposals._proposed_opens_at_utc`.

    `ProposedRound.data` carries the round's open time as JST TEXT in the
    draft vocabulary's "%Y-%m-%d %H:%M" shape (see round_completion.py), never
    a `datetime` -- so this fixture writes exactly that shape rather than
    stuffing a `datetime` into the mapping directly.
    """

    def _make(label: str, opens_at_utc: datetime | None) -> ProposedRound:
        data: dict = {"label": label}
        if opens_at_utc is not None:
            jst = utc_to_jst(opens_at_utc)
            data["apply_opens_jst"] = jst.strftime("%Y-%m-%d %H:%M")
        return ProposedRound(data=data, evidence={}, label=label)

    return _make


def test_the_key_folds_widths_and_spacing_so_one_round_is_one_row():
    """Mutation: dropping the normalisation. Then a page that renders
    '１次先行' one day and '1次先行 ' the next accumulates a second proposal
    for the same round, every day, forever."""
    a = dedupe_key("１次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
    b = dedupe_key(" 1次先行  ", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
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
    a = dedupe_key("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
    b = dedupe_key("1次先行", datetime(2026, 9, 10, 1, 0, tzinfo=UTC))
    assert a != b


def test_a_round_the_concert_already_holds_is_not_proposed(make_proposed):
    """Mutation: returning `proposed` unchanged. The pass would then re-propose
    every round the concert already has, every day."""
    held = [HeldRound("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    proposed = [make_proposed("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    assert new_proposals(held, proposed) == []


def test_a_genuinely_new_round_survives(make_proposed):
    """Mutation: returning [] unconditionally -- which the previous test alone
    would not catch."""
    held = [HeldRound("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    fresh = make_proposed("2次先行", datetime(2026, 9, 20, 1, 0, tzinfo=UTC))
    assert new_proposals(held, [fresh]) == [fresh]
