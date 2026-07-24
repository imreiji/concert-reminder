"""The sentence-slot splitter: pure pattern splitting for the reminder-rule
builders (domain/sentence.py). Locale patterns reorder the {name} slots, so the
splitter is what lets the renderer drop each <select> where the translator put
it. Tested directly, no Jinja involved."""

import pytest

from app.domain.sentence import split_slots

KNOWN = {"offset", "direction", "anchor", "days", "hours"}


def test_plain_pattern_splits_text_and_slots_in_order():
    # The English welcome pattern: text runs interleave with three slots.
    parts = split_slots("Remind me {offset} {direction} {anchor}.", KNOWN)
    assert parts == [
        ("text", "Remind me "),
        ("slot", "offset"),
        ("text", " "),
        ("slot", "direction"),
        ("text", " "),
        ("slot", "anchor"),
        ("text", "."),
    ]


def test_adjacent_slots_produce_no_text_between():
    # CJK patterns pack slots with no spaces: "{anchor}{direction}{offset}".
    parts = split_slots("{anchor}{direction}{offset}", KNOWN)
    assert parts == [("slot", "anchor"), ("slot", "direction"), ("slot", "offset")]


def test_cjk_text_around_slots_is_preserved_verbatim():
    parts = split_slots("{anchor}の{offset}に通知。", KNOWN)
    assert parts == [
        ("slot", "anchor"),
        ("text", "の"),
        ("slot", "offset"),
        ("text", "に通知。"),
    ]


def test_unknown_slot_raises_value_error():
    # A translator typo must fail loudly (in the catalogue tests) rather than
    # silently rendering a blank where a control belongs.
    with pytest.raises(ValueError, match="unknown slot 'ofset'"):
        split_slots("Remind me {ofset}.", KNOWN)


def test_repeated_slot_emits_the_slot_each_time():
    parts = split_slots("{anchor} … {anchor}", KNOWN)
    assert parts == [("slot", "anchor"), ("text", " … "), ("slot", "anchor")]


def test_pattern_with_no_slots_is_a_single_text_part():
    assert split_slots("just some words", KNOWN) == [("text", "just some words")]


def test_empty_string_yields_no_parts():
    assert split_slots("", KNOWN) == []


def test_leading_and_trailing_slots_have_no_empty_text_parts():
    # No spurious ("text", "") at the ends.
    parts = split_slots("{offset} then {anchor}", KNOWN)
    assert parts == [("slot", "offset"), ("text", " then "), ("slot", "anchor")]
