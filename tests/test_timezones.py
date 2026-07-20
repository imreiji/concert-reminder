"""JST/UTC/local conversions — the app's #1 bug source, so tested from day one."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.timezones import (
    UTC,
    fmt_dual,
    fmt_dual_lines,
    jst_to_utc,
    utc_to_jst,
    utc_to_local,
)


def test_jst_to_utc_is_minus_nine():
    # Ticket sale announced as 2026-08-01 19:00 JST
    utc = jst_to_utc(datetime(2026, 8, 1, 19, 0))
    assert utc == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_roundtrip():
    naive = datetime(2026, 12, 31, 23, 59)
    assert utc_to_jst(jst_to_utc(naive)).replace(tzinfo=None) == naive


def test_display_in_atlantic_time():
    # 19:00 JST in summer = 07:00 in Moncton (ADT, UTC-3)
    utc = jst_to_utc(datetime(2026, 8, 1, 19, 0))
    local = utc_to_local(utc, "America/Moncton")
    assert (local.hour, local.minute) == (7, 0)


def test_rejects_aware_input_to_jst_parser():
    with pytest.raises(ValueError):
        jst_to_utc(datetime(2026, 8, 1, 19, 0, tzinfo=ZoneInfo("Asia/Tokyo")))


def test_rejects_naive_stored_datetime():
    with pytest.raises(ValueError):
        utc_to_local(datetime(2026, 8, 1, 10, 0), "America/Moncton")


def test_fmt_dual_shows_both_zones():
    utc = jst_to_utc(datetime(2026, 8, 1, 19, 0))
    s = fmt_dual(utc, "America/Moncton")
    assert "19:00 JST" in s and "07:00" in s


def test_fmt_dual_lines_two_line_shape():
    # The demo's two-line render: a bold weekday+day+month line, then a
    # HH:MM JST · HH:MM <user-zone> time line below it.
    utc = jst_to_utc(datetime(2026, 8, 1, 19, 0))
    date_line, time_line = fmt_dual_lines(utc, "America/Moncton")

    # 2026-08-01 is a Saturday; the day carries no leading zero.
    assert date_line == "Sat 1 Aug"

    # Invariant 1: both zones ALWAYS present, JST first.
    assert "19:00 JST" in time_line
    assert "07:00" in time_line  # America/Moncton in summer is UTC-3
    assert time_line.index("JST") < time_line.index("07:00")
