"""build_calendar: pure-function .ics generation tests."""

from datetime import UTC, datetime

import pytest

from app.domain.ics_export import build_calendar


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def test_build_calendar_one_vcalendar_multiple_vevents():
    text = build_calendar([
        ("Concert A — R1", dt(6, 25), None, None),
        ("Concert B — R1", dt(7, 1), "https://example.com", "notes"),
    ])
    assert text.count("BEGIN:VCALENDAR") == 1
    assert text.count("END:VCALENDAR") == 1
    assert text.count("BEGIN:VEVENT") == 2
    assert text.count("END:VEVENT") == 2
    assert "SUMMARY:Concert A — R1\r\n" in text
    assert "SUMMARY:Concert B — R1\r\n" in text
    assert "URL:https://example.com\r\n" in text
    assert "DESCRIPTION:notes\r\n" in text


def test_build_calendar_empty_list_is_still_a_valid_shell():
    text = build_calendar([])
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "BEGIN:VEVENT" not in text


def test_build_calendar_shares_one_dtstamp_across_events():
    text = build_calendar(
        [("A", dt(6, 25), None, None), ("B", dt(7, 1), None, None)],
        now_utc=dt(1, 1),
    )
    stamps = [line for line in text.split("\r\n") if line.startswith("DTSTAMP:")]
    assert stamps == ["DTSTAMP:20260101T120000Z", "DTSTAMP:20260101T120000Z"]


def test_build_calendar_minimal_shape():
    text = build_calendar([("Day 1 Lottery #1 closes", dt(6, 25, 15), None, None)])
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0\r\n" in text
    assert "BEGIN:VEVENT\r\n" in text
    assert "END:VEVENT\r\n" in text
    assert "DTSTART:20260625T150000Z\r\n" in text
    assert "SUMMARY:Day 1 Lottery #1 closes\r\n" in text
    assert "UID:" in text
    assert "DTSTAMP:" in text


def test_build_calendar_omits_optional_fields_when_absent():
    text = build_calendar([("Round closes", dt(6, 25), None, None)])
    assert "URL:" not in text
    assert "DESCRIPTION:" not in text


def test_build_calendar_escapes_special_characters():
    text = build_calendar([("Kaho, Sayaka; Live\nNight", dt(6, 25), None, None)])
    assert "SUMMARY:Kaho\\, Sayaka\\; Live\\nNight\r\n" in text


def test_build_calendar_rejects_naive_datetime():
    with pytest.raises(ValueError):
        build_calendar([("Round closes", datetime(2026, 6, 25), None, None)])


def test_build_calendar_uid_is_stable_for_same_inputs():
    a = build_calendar([("Round closes", dt(6, 25), None, None)], now_utc=dt(1, 1))
    b = build_calendar([("Round closes", dt(6, 25), None, None)], now_utc=dt(1, 2))
    uid_a = next(line for line in a.split("\r\n") if line.startswith("UID:"))
    uid_b = next(line for line in b.split("\r\n") if line.startswith("UID:"))
    assert uid_a == uid_b
