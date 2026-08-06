"""Phase 2's strip_rounds: a round survives only if the app can find the text
the model says it read.

Phase 1 could guarantee honesty by emitting no rounds at all. Phase 2 emits
them, so every one of these cases is a way a fabricated deadline could reach a
real user as a real reminder. A rejection is never silent -- each one carries a
reason that reaches the preview.
"""

from datetime import date

from app.domain.round_evidence import ProposedRound, verify_rounds

PAGE = (
    "チケット情報 1次先行抽選 受付開始 2026年1月5日(月)12:00 "
    "申込締切 2026年1月10日(土)23:59 当落発表 2026年1月15日(木)18:00 "
    "入金期限 2026年1月20日(火)23:59 2次先行は後日発表"
)
TODAY = date(2025, 12, 1)


def _round(**over):
    data = {"label": "1次先行抽選", "kind": "lottery", "apply_closes_jst": "2026-01-10 23:59"}
    evidence = {"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}
    data.update(over.pop("data", {}))
    evidence.update(over.pop("evidence", {}))
    return ProposedRound(data=data, evidence=evidence, label=data["label"])


def test_a_grounded_round_is_accepted():
    v = verify_rounds([_round()], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1 and not v.rejected


def test_a_round_with_no_quote_for_its_timestamp_is_rejected():
    v = verify_rounds([_round(evidence={"apply_closes_jst": ""})], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "no evidence" in v.rejected[0]


def test_a_quote_that_is_not_on_the_page_is_rejected():
    r = _round(evidence={"apply_closes_jst": "申込締切 2026年2月28日(土)23:59"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "not on the page" in v.rejected[0]


def test_a_real_quote_that_does_not_contain_its_own_timestamp_is_rejected():
    # The nastiest case: the model quotes a line that genuinely exists but says
    # something else. Finding the quote is not enough -- the quote has to be
    # about this timestamp.
    r = _round(evidence={"apply_closes_jst": "当落発表 2026年1月15日(木)18:00"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "does not carry" in v.rejected[0]


def test_loose_spacing_in_a_quote_still_matches():
    r = _round(evidence={"apply_closes_jst": "申込締切　2026年1月10日(土)23:59"})
    assert len(verify_rounds([r], PAGE, ["Day 1"], TODAY).accepted) == 1


def test_a_year_missing_from_the_quote_may_come_from_the_page():
    # Japanese pages routinely put the year in a heading and omit it from the
    # deadline line. Requiring it in the quote would reject the common case.
    page = "2026年 チケット情報 申込締切 1月10日(土)23:59"
    r = _round(evidence={"apply_closes_jst": "申込締切 1月10日(土)23:59"})
    assert len(verify_rounds([r], page, ["Day 1"], TODAY).accepted) == 1


def test_a_year_on_neither_the_quote_nor_the_page_is_rejected():
    page = "申込締切 1月10日(土)23:59"
    r = _round(evidence={"apply_closes_jst": "申込締切 1月10日(土)23:59"})
    assert not verify_rounds([r], page, ["Day 1"], TODAY).accepted


def test_a_zero_minute_written_as_20時_is_accepted():
    page = "申込締切 2026年1月10日(土)20時"
    r = _round(
        data={"apply_closes_jst": "2026-01-10 20:00"},
        evidence={"apply_closes_jst": "申込締切 2026年1月10日(土)20時"},
    )
    assert len(verify_rounds([r], page, ["Day 1"], TODAY).accepted) == 1


def test_out_of_order_anchors_are_rejected():
    r = _round(
        data={"apply_opens_jst": "2026-01-15 18:00", "apply_closes_jst": "2026-01-10 23:59"},
        evidence={
            "apply_opens_jst": "当落発表 2026年1月15日(木)18:00",
            "apply_closes_jst": "申込締切 2026年1月10日(土)23:59",
        },
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "out of order" in v.rejected[0]


def test_an_implausible_year_is_rejected():
    page = "申込締切 2126年1月10日(土)23:59"
    r = _round(
        data={"apply_closes_jst": "2126-01-10 23:59"},
        evidence={"apply_closes_jst": "申込締切 2126年1月10日(土)23:59"},
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted
    assert "implausible" in v.rejected[0]


def test_applies_to_naming_a_leg_the_draft_does_not_have_is_rejected():
    r = _round(data={"applies_to": ["Day 9"]})
    v = verify_rounds([r], PAGE, ["Day 1", "Day 2"], TODAY)
    assert not v.accepted
    assert "Day 9" in v.rejected[0]


def test_applies_to_matching_a_leg_is_kept():
    r = _round(data={"applies_to": ["Day 2"]})
    v = verify_rounds([r], PAGE, ["Day 1", "Day 2"], TODAY)
    assert len(v.accepted) == 1


def test_a_round_with_no_timestamps_at_all_is_rejected():
    # A round with no deadline is not a round -- it is a label. Keeping it
    # would put an empty rung on the ladder for a human to wonder about.
    r = ProposedRound(data={"label": "2次先行", "kind": "lottery"}, evidence={}, label="2次先行")
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "no timestamps" in v.rejected[0]


def test_one_bad_round_does_not_cost_a_good_one():
    good, bad = _round(), _round(evidence={"apply_closes_jst": "存在しない行"})
    v = verify_rounds([good, bad], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1 and len(v.rejected) == 1


def test_evidence_never_rides_into_the_accepted_data():
    v = verify_rounds([_round()], PAGE, ["Day 1"], TODAY)
    assert "evidence" not in v.accepted[0].data
