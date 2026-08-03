"""Read an iCalendar (RFC 5545) body into event rows.

Pure: takes text, returns rows -- no httpx, exactly like domain/eventernote.py.
Hand-rolled rather than a dependency: the feeds are Google Calendar exports,
which are tame, and the app needs four fields per VEVENT.

WARNINGS OVER FAILURES, per parse_draft/parse_tags/parse_actor_events: a
VEVENT missing UID, SUMMARY or a readable DTSTART is skipped and counted,
never raised on. Only a body with no BEGIN:VEVENT structure at all raises --
a feed that rots must degrade to "found nothing", visible on
/admin/discoveries' status line, not crash a scheduler tick daily.

Datetimes: DTSTART may be VALUE=DATE (20260915) or a wall-clock/UTC datetime
(20261001T235900 / ...Z). The feeds this app reads declare Asia/Tokyo, and a
lead carries a JST calendar DATE (invariant 1 -- inventing a midnight instant
would put a fake deadline-shaped value into an aware-UTC schema), so only the
date half is kept. A UTC-suffixed stamp is off by at most one calendar day at
the JST boundary; accepted -- a lead's date is a pointer, not a deadline.
"""

import re
from dataclasses import dataclass, field
from datetime import date


class IcsError(Exception):
    """The text is not an iCalendar body at all."""


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    summary: str
    date: date
    location: str = ""


@dataclass
class ParsedCalendar:
    events: list[IcsEvent] = field(default_factory=list)
    skipped: int = 0


_DTSTART = re.compile(r"^(\d{4})(\d{2})(\d{2})")
_UNESCAPE = {r"\n": "\n", r"\N": "\n", r"\,": ",", r"\;": ";", r"\\": "\\"}


def _unfold(text: str) -> list[str]:
    """RFC 5545 3.1: a line starting with SPACE or TAB continues the previous."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        pair = value[i:i + 2]
        if pair in _UNESCAPE:
            out.append(_UNESCAPE[pair])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_ics(text: str) -> ParsedCalendar:
    lines = _unfold(text)
    if not any(line.strip() == "BEGIN:VEVENT" for line in lines):
        raise IcsError("no VEVENT blocks -- this does not look like an iCalendar body")

    cal = ParsedCalendar()
    fields: dict[str, str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            fields = {}
            continue
        if stripped == "END:VEVENT":
            if fields is None:
                continue
            event = _build_event(fields)
            if event is None:
                cal.skipped += 1
            else:
                cal.events.append(event)
            fields = None
            continue
        if fields is None or ":" not in line:
            continue
        name_part, value = line.split(":", 1)
        # Parameters (DTSTART;VALUE=DATE) hang off the property name.
        name = name_part.split(";", 1)[0].upper()
        fields.setdefault(name, value)
    return cal


def _build_event(fields: dict[str, str]) -> IcsEvent | None:
    uid = fields.get("UID", "").strip()
    summary = _unescape(fields.get("SUMMARY", "")).strip()
    m = _DTSTART.match(fields.get("DTSTART", "").strip())
    if not uid or not summary or m is None:
        return None
    try:
        when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None  # a 20260230 in the wild is a skip, not a crash
    return IcsEvent(
        uid=uid, summary=summary, date=when,
        location=_unescape(fields.get("LOCATION", "")).strip(),
    )
