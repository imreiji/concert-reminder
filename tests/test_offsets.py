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
    ],
)
def test_describe_offset(days, hours, minutes, expected):
    set_locale("en")
    assert describe_offset(days, hours, minutes) == expected
