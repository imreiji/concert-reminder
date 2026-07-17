"""Serialize a concert to YAML, shaped roughly like mting314/event-tracker's
format (slug/kind/series/performances/rounds), for sharing -- this is an
EXPORT only. SQLite via the web UI stays the only way to create or edit
data; there is no importer for this format.

Pure function: no I/O, no ORM imports. The caller (web route) adapts ORM
rows into the plain dataclasses below, exactly like domain/reminders.py
adapts ORM rows into WindowInfo/DayInfo -- keeps this module testable with
plain Python values and free of a database dependency.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from app.domain.timezones import utc_to_jst


@dataclass(frozen=True)
class YamlDay:
    label: str
    starts_at_utc: datetime


@dataclass(frozen=True)
class YamlRound:
    label: str
    kind: str
    applies_to_labels: list[str] = field(default_factory=list)
    opens_at_utc: datetime | None = None
    closes_at_utc: datetime | None = None
    results_at_utc: datetime | None = None
    payment_deadline_at_utc: datetime | None = None
    url: str | None = None


def slugify(title: str) -> str:
    """'Hasunosora 5th Live!' -> 'hasunosora-5th-live'."""
    lowered = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "concert"


def _jst_str(dt: datetime | None) -> str | None:
    return utc_to_jst(dt).strftime("%Y-%m-%d %H:%M") if dt else None


def concert_to_yaml(
    title: str,
    kind: str | None,
    franchises: list[str],
    groups: list[str],
    artists: list[str],
    venues: list[str],
    days: list[YamlDay],
    rounds: list[YamlRound],
    notes: str | None,
) -> str:
    """All timestamps are rendered in JST (the "Datetime contract" boundary
    for this app), formatted the same way forms accept them: 'YYYY-MM-DD HH:MM'.
    """
    data = {
        "slug": slugify(title),
        "title": title,
        "kind": kind,
        "series": {
            "franchises": franchises,
            "groups": groups,
            "artists": artists,
        },
        "venues": venues,
        "performances": [
            {"label": d.label, "starts_at_jst": _jst_str(d.starts_at_utc)} for d in days
        ],
        "rounds": [
            {
                "label": r.label,
                "kind": r.kind,
                "applies_to": r.applies_to_labels,
                "apply_opens_jst": _jst_str(r.opens_at_utc),
                "apply_closes_jst": _jst_str(r.closes_at_utc),
                "results_jst": _jst_str(r.results_at_utc),
                "payment_deadline_jst": _jst_str(r.payment_deadline_at_utc),
                "url": r.url,
            }
            for r in rounds
        ],
        "notes": notes,
    }
    return yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
