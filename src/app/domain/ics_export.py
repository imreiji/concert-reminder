"""Build a minimal single-VEVENT .ics file for a round deadline.

Pure function: no I/O, no ORM imports -- same pattern as yaml_export.py.
Callers (web routes) pass in a UTC-aware datetime and plain strings already
pulled off the ORM row.
"""

import re
from datetime import datetime


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
    if at_utc.tzinfo is None:
        raise ValueError("at_utc must be timezone-aware")
    stamp = _stamp(now_utc) if now_utc is not None else _stamp(at_utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dekimasen.app//concert-reminder//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{_uid(summary, at_utc)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_stamp(at_utc)}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if url:
        lines.append(f"URL:{_escape(url)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
