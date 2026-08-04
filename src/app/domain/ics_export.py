"""Build a minimal single-VEVENT .ics file for a round deadline.

Pure function: no I/O, no ORM imports -- same pattern as yaml_export.py.
Callers (web routes) pass in a UTC-aware datetime and plain strings already
pulled off the ORM row.
"""

import re
from datetime import UTC, datetime

from app.domain.types import Anchor

# Canonical qualifiers for a round's moments on the personal feed. Plain
# data, NOT gettext: the feed renders canonical (a URL has no viewer), and
# canonical text is by definition untranslated. Japanese ticketing terms
# because Japanese is this catalogue's source of truth. EVENT_START is
# deliberately absent -- a show date is its own summary and takes no
# qualifier.
CANONICAL_ANCHOR_QUALIFIERS = {
    Anchor.OPENS: "受付開始",
    Anchor.CLOSES: "申込締切",
    Anchor.RESULTS: "当落発表",
    Anchor.PAYMENT: "支払期限",
}


def _escape(text: str) -> str:
    """RFC 5545 TEXT escaping for the characters that matter to us."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _uid(summary: str, at_utc: datetime) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", summary.strip().lower()).strip("-") or "event"
    return f"{slug}-{_stamp(at_utc)}@dekimasen.app"


def _vevent_lines(
    summary: str,
    at_utc: datetime,
    url: str | None,
    description: str | None,
    now_utc: datetime,
) -> list[str]:
    if at_utc.tzinfo is None:
        raise ValueError("at_utc must be timezone-aware")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(summary, at_utc)}",
        f"DTSTAMP:{_stamp(now_utc)}",
        f"DTSTART:{_stamp(at_utc)}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if url:
        lines.append(f"URL:{_escape(url)}")
    lines.append("END:VEVENT")
    return lines


def build_ics(
    summary: str,
    at_utc: datetime,
    url: str | None = None,
    description: str | None = None,
    now_utc: datetime | None = None,
) -> str:
    """`at_utc` (and `now_utc`, if given) must be aware UTC datetimes -- this
    app never stores or compares naive ones. Renders a zero-duration VEVENT
    at `at_utc` (deadlines are a point in time, not a span).
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dekimasen.app//concert-reminder//EN",
        "CALSCALE:GREGORIAN",
        *_vevent_lines(summary, at_utc, url, description, now_utc or at_utc),
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_calendar(
    events: list[tuple[str, datetime, str | None, str | None]],
    now_utc: datetime | None = None,
) -> str:
    """Multiple VEVENTs in one VCALENDAR -- a subscribable personal feed
    rather than a one-off download. Each event is a (summary, at_utc, url,
    description) tuple, same fields as build_ics's single-event form. Every
    VEVENT shares one DTSTAMP (when this feed was generated), not each
    event's own time.
    """
    stamp = now_utc or datetime.now(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dekimasen.app//concert-reminder//EN",
        "CALSCALE:GREGORIAN",
    ]
    for summary, at_utc, url, description in events:
        lines += _vevent_lines(summary, at_utc, url, description, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
