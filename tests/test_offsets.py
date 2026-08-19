"""What `app/offsets.py` owes its three callers.

`describe_offset` takes the SIGNED stored values and derives before/after
itself -- it deliberately does not accept a `direction` argument, because two
sources for one fact is a way for a caller to disagree with the database.
"""

import pytest

from app.i18n import set_locale
from app.offsets import describe_offset, format_hhmm, parse_hhmm


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0:30", (0, 30)),
        ("00:30", (0, 30)),
        ("3:00", (3, 0)),
        ("3", (3, 0)),        # bare number = hours, matching the select it replaced
        ("", (0, 0)),
        ("  1:05  ", (1, 5)),
    ],
)
def test_parse_hhmm_accepts(text, expected):
    assert parse_hhmm(text) == expected


@pytest.mark.parametrize("text", ["0:75", "24:00", "abc", "1:2:3", "-1:00", "1:-5"])
def test_parse_hhmm_rejects(text):
    """Rejects rather than clamps: a silently-rounded reminder is worse than a 422."""
    with pytest.raises(ValueError):
        parse_hhmm(text)


def test_format_hhmm_round_trips_what_the_box_shows():
    assert format_hhmm(0, 30) == "0:30"
    assert format_hhmm(3, 0) == "3:00"
    assert parse_hhmm(format_hhmm(1, 5)) == (1, 5)


@pytest.mark.parametrize(
    "days,hours,minutes,expected",
    [
        (0, 0, 0, "Same day"),
        (0, 0, -30, "30 minutes before"),
        (0, 0, -1, "1 minute before"),
        (0, -3, 0, "3 hours before"),      # today this renders as "Same day" -- the bug
        (0, -1, -30, "1 hour 30 minutes before"),
        (-3, 0, 0, "3 days before"),
        (-3, -6, 0, "3 days 6 hours before"),
        (0, 0, 15, "15 minutes after"),
        (1, 0, 0, "1 day after"),
        (-3, -6, -45, "3 days 6 hours before"),
        (2, 1, 30, "2 days 1 hour after"),
    ],
)
def test_describe_offset(days, hours, minutes, expected):
    """The last two cases have three non-zero units, so they pin "the two
    LARGEST units" as an actual behaviour: they kill the mutant that swaps
    `parts[:2]` for `parts[-2:]`, which every earlier (<=2-unit) case cannot
    tell apart from correct code -- that mutant would render (-3, -6, -45) as
    "6 hours 45 minutes before" instead of "3 days 6 hours before"."""
    set_locale("en")
    assert describe_offset(days, hours, minutes) == expected


# ---------------------------------------------------------------------------
# The same function under ja and zh. Everything above runs in English, where
# the direction words are the msgids themselves and no catalogue is consulted
# at all (i18n.py maps "en" to NullTranslations), so nothing above can see a
# fault in a translation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,days,hours,minutes,expected",
    [
        # Direction, one unit. This is the pair the whole block exists for.
        ("ja", 0, 0, -30, "30分前"),
        ("ja", 0, 0, 15, "15分後"),
        ("ja", -3, 0, 0, "3日前"),
        ("ja", 1, 0, 0, "1日後"),
        ("zh", 0, 0, -30, "30分钟前"),
        ("zh", 0, 0, 15, "15分钟后"),
        ("zh", -3, 0, 0, "3天前"),
        ("zh", 1, 0, 0, "1天后"),
        # Two units: the joiner is translatable, and CJK does not space
        # between them.
        ("ja", 0, -1, -30, "1時間30分前"),
        ("ja", -3, -6, 0, "3日6時間前"),
        ("zh", 0, -1, -30, "1小时30分钟前"),
        ("zh", 2, 1, 30, "2天1小时后"),
        # The zero offset takes its own msgid and never reaches either.
        ("ja", 0, 0, 0, "当日"),
        ("zh", 0, 0, 0, "当天"),
    ],
)
def test_describe_offset_translated(locale, days, hours, minutes, expected):
    """`describe_offset` under the two shipped catalogues, asserted on the
    exact rendered string.

    THE MUTATION THIS EXISTS TO KILL: swapping the ja `{quantity} before`
    (前) and `{quantity} after` (後) msgstrs, or the zh pair (前/后). Nothing
    else on the branch notices. test_i18n_catalogues only checks that a
    msgstr is non-empty, its placeholder guard sees `{quantity}` on both
    sides of the swap, and every case above this block runs in English. The
    user-visible result of that swap is a reminder described as firing AFTER
    a deadline when it actually fires before -- in two of three languages,
    with green CI.

    Two more mutations it kills, both silent the same way:
    - Reverting the `{first} {second}` joiner to a hardcoded `" ".join(...)`:
      the two-unit cases would render 「1時間 30分前」 / 「1小时 30分钟前」,
      spaced the English way. CJK does not space between units.
    - Giving zh the bare 分 that the compact `{n}m` countdown msgid uses
      instead of 分钟: 「30分前」 reads as a clock position, not a duration.
    """
    set_locale(locale)
    try:
        assert describe_offset(days, hours, minutes) == expected
    finally:
        # Same discipline as tests/test_i18n.py: the locale is a ContextVar
        # with a process-wide default, so a leaked "ja" would silently
        # re-language every later test in the session.
        set_locale("en")
