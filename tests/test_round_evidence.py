"""Phase 2's strip_rounds: a round survives only if the app can find the text
the model says it read, IN ONE PLACE.

Phase 1 could guarantee honesty by emitting no rounds at all. Phase 2 emits
them, so every one of these cases is a way a fabricated deadline could reach a
real user as a real reminder. A rejection is never silent -- each one carries a
reason that reaches the preview. A false ACCEPT is the failure that matters
here; a false REJECT only costs a human one round typed in by hand, so several
cases below exist specifically to pin that the stricter reading did not tip
into rejecting things that are actually fine.
"""

from datetime import date

from app.domain.round_evidence import ProposedRound, normalize_numbers, verify_rounds

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
    # A `data` dict carrying its own stray "evidence" key (e.g. a model that
    # echoed the field name back into the round mapping itself) must not
    # survive acceptance -- this is defence in depth, since the draft parser
    # downstream strips it again; a fixture that never had the key in the
    # first place would prove nothing about that stripping actually running.
    r = _round(data={"evidence": "should never reach the committed document"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1
    assert "evidence" not in v.accepted[0].data


# -- Regression tests: false accepts found against this fixture in review ---


def test_an_hour_that_only_matches_the_dates_own_month_digit_is_rejected():
    # The real quote and the real date are both correct; only the claimed
    # HOUR is wrong (it happens to equal the month digit already in the
    # quote). A flat "is every digit present somewhere" test passed this.
    r = _round(
        data={"apply_closes_jst": "2026-01-10 01:00"},
        evidence={"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"},
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "does not carry" in v.rejected[0]


def test_an_hour_that_only_matches_the_dates_own_day_digit_is_rejected():
    r = _round(
        data={"apply_closes_jst": "2026-01-10 10:00"},
        evidence={"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"},
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "does not carry" in v.rejected[0]


def test_a_quote_spanning_two_real_lines_does_not_recombine_into_a_third_stamp():
    # A genuine substring of the page, but it splices one line's DATE with a
    # different line's TIME. Every digit in the claimed stamp is present in
    # the quote "somewhere" -- the cross-product a flat set test cannot see.
    quote = "受付開始 2026年1月5日(月)12:00 申込締切 2026年1月10日(土)23:59"
    assert quote in PAGE  # sanity: this really is one contiguous page substring
    r = _round(
        data={"apply_closes_jst": "2026-01-05 23:59"},
        evidence={"apply_closes_jst": quote},
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted


def test_quoting_the_whole_page_grants_no_stamp_assembled_from_its_digits():
    r = _round(
        data={"apply_closes_jst": "2026-01-20 12:00"},
        evidence={"apply_closes_jst": PAGE},
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted


# -- Item 1: quote length cap and date-to-time proximity --------------------


def test_a_quote_over_200_characters_is_rejected_on_length_alone():
    filler = "とても長い注意事項がここにたくさん書かれています。" * 8
    page = f"申込締切 2026年1月10日(土)23:59 {filler}"
    quote = page  # a real substring of the page -- rejected for its length, not its truth
    assert len(quote) > 200
    r = _round(evidence={"apply_closes_jst": quote})
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted
    assert "characters" in v.rejected[0]
    assert "does not carry" not in v.rejected[0]
    assert "not on the page" not in v.rejected[0]


def test_a_date_and_time_more_than_60_characters_apart_is_rejected():
    filler = "これは注意事項です" * 6  # digit-free padding between date and time
    page = f"申込締切 2026年1月10日 {filler} 23:59"
    r = _round(evidence={"apply_closes_jst": page})
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted


def test_a_close_date_and_time_within_60_characters_is_still_accepted():
    page = "申込締切 2026年1月10日(土)23:59"
    r = _round(evidence={"apply_closes_jst": page})
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert len(v.accepted) == 1


def test_a_nonzero_minute_does_not_get_the_zero_waiver():
    # The real page minute is 30, not 0 -- with a ':' present the waiver must
    # not apply, so the claimed 0 minute is checked for real and fails.
    page = "申込締切 2026年1月10日(土)12:30"
    r = _round(
        data={"apply_closes_jst": "2026-01-10 12:00"},
        evidence={"apply_closes_jst": page},
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted


# -- Item 2: chronological, not lexicographic, ordering ---------------------


def test_a_T_separated_stamp_that_is_genuinely_out_of_order_is_rejected():
    # ' ' sorts below 'T' in plain text, so the OLD raw-string compare read
    # this pair as in order even though the round would close before it opens.
    page = "受付開始 2026年1月10日(土)23:59 申込締切 2026年1月10日(土)12:00"
    r = _round(
        data={"apply_opens_jst": "2026-01-10 23:59", "apply_closes_jst": "2026-01-10T12:00"},
        evidence={
            "apply_opens_jst": "受付開始 2026年1月10日(土)23:59",
            "apply_closes_jst": "申込締切 2026年1月10日(土)12:00",
        },
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted
    assert "out of order" in v.rejected[0]


def test_a_stamp_with_a_leading_prefix_that_is_genuinely_in_order_is_kept():
    # `_stamp_parts` uses `.search()`, so a stray prefix like "受付 " parses
    # fine -- but as a RAW STRING it sorts above every ASCII digit, so the
    # OLD compare misread a genuinely-in-order pair as backwards.
    page = "受付開始 2026年1月10日(土)12:00 申込締切 2026年1月10日(土)23:59"
    r = _round(
        data={"apply_opens_jst": "受付 2026-01-10 12:00", "apply_closes_jst": "2026-01-10 23:59"},
        evidence={
            "apply_opens_jst": "受付開始 2026年1月10日(土)12:00",
            "apply_closes_jst": "申込締切 2026年1月10日(土)23:59",
        },
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert len(v.accepted) == 1


# -- Item 3: an unrecognized applies_to shape is rejected loudly ------------


def test_applies_to_as_a_bare_scalar_is_rejected_not_skipped():
    # A bare YAML scalar ("applies_to: Day 9") is a string, not a list -- an
    # entirely ordinary way for a model to answer wrong. It must not fall
    # through the `isinstance(..., list)` guard unexamined.
    r = _round(data={"applies_to": "Day 9"})
    v = verify_rounds([r], PAGE, ["Day 1", "Day 2"], TODAY)
    assert not v.accepted
    assert "applies_to" in v.rejected[0]
    assert "list" in v.rejected[0]


# -- Item 8: an invalid calendar date is rejected ----------------------------


def test_a_nonexistent_calendar_date_is_rejected():
    page = "申込締切 2026年2月30日(月)23:59"
    r = _round(
        data={"apply_closes_jst": "2026-02-30 23:59"},
        evidence={"apply_closes_jst": "申込締切 2026年2月30日(月)23:59"},
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted
    assert "calendar date" in v.rejected[0]


# -- Unverified evidence keys never survive acceptance -----------------------


def test_an_extra_unverified_evidence_key_is_stripped_from_an_accepted_round():
    # The round only carries apply_closes_jst -- results_jst is not one of its
    # TIMESTAMP_FIELDS, so _reject_reason never looks at (and never verifies)
    # whatever the model wrote for it. That entry must not survive onto the
    # accepted round: rendering it under "Read from the ticket page:" would
    # present an unchecked, possibly fabricated quote as page-sourced fact.
    r = _round(
        evidence={
            "results_jst": "当落発表は2026年2月30日(未定) ← never on the page",
        }
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1
    assert v.accepted[0].evidence == {"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}
    assert "results_jst" not in v.accepted[0].evidence


def test_evidence_for_a_field_the_round_does_not_carry_is_dropped_even_when_the_round_has_none():
    # A round with only apply_closes_jst set, whose evidence mapping ALSO
    # carries an entry for a field (payment_deadline_jst) the round's data
    # doesn't have at all -- not merely blank. That key is not in
    # TIMESTAMP_FIELDS-present-on-this-round, so it must be filtered too.
    r = _round(evidence={"payment_deadline_jst": "made up, not on the page"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1
    assert set(v.accepted[0].evidence) == {"apply_closes_jst"}


# -- Item 6 & 7: full-width folding ------------------------------------------


def test_normalize_numbers_folds_full_width_digits_and_colon():
    assert normalize_numbers("２０２６年１月１０日(土)２３：５９") == [2026, 1, 10, 23, 59]
    assert normalize_numbers("2026-01-10 23:59") == [2026, 1, 10, 23, 59]


def test_a_full_width_page_still_matches_a_half_width_quote():
    # The page (as a real Japanese site might write it) uses zenkaku digits;
    # the model's quote reproduces it in ASCII digits, which is a normal
    # transcription, not a fabrication -- the on-page substring test must
    # fold both sides the same way or this reads as "not on the page".
    page = "申込締切　２０２６年１月１０日(土)２３：５９"
    r = _round(evidence={"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"})
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert len(v.accepted) == 1
