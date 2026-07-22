import pytest

from app.domain.translations import _SLOTS, SLOT_LABEL, missing_variants


@pytest.mark.parametrize("base,en,zh,expected", [
    ("あ", "a", "a", ()),                 # complete
    ("", "", "", ()),                     # unused entirely
    ("  ", "\t", "", ()),                 # whitespace counts as blank
    ("あ", "", "", ("en", "zh")),          # started, so finish it
    ("", "a", "", ("ja", "zh")),
    ("", "", "a", ("ja", "en")),
    # Exactly one slot missing, in each position: these are what the 422
    # message is built from, so each must name the right single language.
    ("あ", "a", "", ("zh",)),
    ("あ", "", "a", ("en",)),
    ("", "a", "a", ("ja",)),
])
def test_optional_field_is_all_or_nothing(base, en, zh, expected):
    assert missing_variants(base, en, zh) == expected


@pytest.mark.parametrize("base,en,zh,expected", [
    ("あ", "a", "a", ()),
    ("", "", "", ("ja", "en", "zh")),     # mandatory: blank is not an option
    ("あ", "", "", ("en", "zh")),
])
def test_mandatory_field_must_be_complete(base, en, zh, expected):
    assert missing_variants(base, en, zh, mandatory=True) == expected


def test_order_is_stable_so_messages_read_the_same_every_time():
    assert missing_variants("", "", "", mandatory=True) == ("ja", "en", "zh")


def test_slot_label_keys_match_slots_exactly():
    # SLOT_LABEL is the single home for the ja/en/zh display names shared by
    # web/forms.py's 422 and db/service.py's edit-page notice -- if its keys
    # ever drifted from _SLOTS, missing_variants could return a slot with no
    # label to render, a KeyError at the two call sites rather than here.
    assert tuple(SLOT_LABEL) == _SLOTS
