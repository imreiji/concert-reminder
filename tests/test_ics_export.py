"""build_ics: pure-function .ics generation tests."""

from datetime import UTC, datetime

import pytest

from app.domain.ics_export import build_ics


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def test_build_ics_minimal_shape():
    text = build_ics("Day 1 Lottery #1 closes", dt(6, 25, 15))
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0\r\n" in text
    assert "BEGIN:VEVENT\r\n" in text
    assert "END:VEVENT\r\n" in text
    assert "DTSTART:20260625T150000Z\r\n" in text
    assert "SUMMARY:Day 1 Lottery #1 closes\r\n" in text
    assert "UID:" in text
    assert "DTSTAMP:" in text


def test_build_ics_includes_optional_fields_when_given():
    text = build_ics(
        "Round closes", dt(6, 25),
        url="https://example.com/ticket",
        description="CD-code lottery",
    )
    assert "URL:https://example.com/ticket\r\n" in text
    assert "DESCRIPTION:CD-code lottery\r\n" in text


def test_build_ics_omits_optional_fields_when_absent():
    text = build_ics("Round closes", dt(6, 25))
    assert "URL:" not in text
    assert "DESCRIPTION:" not in text


def test_build_ics_escapes_special_characters():
    text = build_ics("Kaho, Sayaka; Live\nNight", dt(6, 25))
    assert "SUMMARY:Kaho\\, Sayaka\\; Live\\nNight\r\n" in text


def test_build_ics_rejects_naive_datetime():
    with pytest.raises(ValueError):
        build_ics("Round closes", datetime(2026, 6, 25))


def test_build_ics_uid_is_stable_for_same_inputs():
    a = build_ics("Round closes", dt(6, 25), now_utc=dt(1, 1))
    b = build_ics("Round closes", dt(6, 25), now_utc=dt(1, 2))
    uid_a = next(line for line in a.split("\r\n") if line.startswith("UID:"))
    uid_b = next(line for line in b.split("\r\n") if line.startswith("UID:"))
    assert uid_a == uid_b


def test_build_ics_dtstamp_uses_now_when_given():
    text = build_ics("Round closes", dt(6, 25), now_utc=dt(1, 1, 0))
    assert "DTSTAMP:20260101T000000Z\r\n" in text
