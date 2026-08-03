from datetime import date

import pytest

from app.domain.ics_read import IcsError, parse_ics

BODY = (
    "BEGIN:VCALENDAR\r\n"
    "PRODID:-//Google Inc//Google Calendar 70.9054//EN\r\n"
    "VERSION:2.0\r\n"
    "X-WR-TIMEZONE:Asia/Tokyo\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20260915\r\n"
    "UID:abc123@google.com\r\n"
    "SUMMARY:ライブ：Liella! 6th 東京公演\r\n"
    "LOCATION:有明アリーナ\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20261001T235900\r\n"
    "UID:def456@google.com\r\n"
    "SUMMARY:締切：〇〇ライブ 2次抽選 応募\\, 最終\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_parses_date_and_datetime_dtstart():
    cal = parse_ics(BODY)
    assert [e.uid for e in cal.events] == ["abc123@google.com", "def456@google.com"]
    assert cal.events[0].date == date(2026, 9, 15)
    assert cal.events[0].location == "有明アリーナ"
    # A datetime DTSTART is JST wall time; only the calendar DATE survives.
    assert cal.events[1].date == date(2026, 10, 1)
    assert cal.events[1].location == ""


def test_unfolds_continuation_lines():
    folded = BODY.replace(
        "SUMMARY:ライブ：Liella! 6th 東京公演",
        "SUMMARY:ライブ：Liella! 6th 東\r\n 京公演",
    )
    cal = parse_ics(folded)
    assert cal.events[0].summary == "ライブ：Liella! 6th 東京公演"


def test_unescapes_rfc5545_text():
    cal = parse_ics(BODY)
    assert cal.events[1].summary == "締切：〇〇ライブ 2次抽選 応募, 最終"


def test_missing_field_skips_and_counts():
    # Second VEVENT loses its UID -> skipped, counted, first still parses.
    broken = BODY.replace("UID:def456@google.com\r\n", "")
    cal = parse_ics(broken)
    assert [e.uid for e in cal.events] == ["abc123@google.com"]
    assert cal.skipped == 1


def test_unreadable_body_raises():
    with pytest.raises(IcsError):
        parse_ics("just some text with no calendar in it")
