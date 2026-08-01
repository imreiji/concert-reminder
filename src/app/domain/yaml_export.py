"""Serialize a concert to YAML, shaped roughly like mting314/event-tracker's
format (slug/kind/series/performances/rounds), for sharing.

This vocabulary is TWO-WAY as of 2026-07-23: `domain/yaml_import.py` parses it
back, and `POST /concerts/import/draft` renders a prefilled preview from it. It
is still not a write path -- `import_commit` remains the only one -- but the
older claim here that "there is no importer for this format" has been wrong
since that shipped.

Pure function: no I/O, no ORM imports. The caller (web route) adapts ORM
rows into the plain dataclasses below, exactly like domain/reminders.py
adapts ORM rows into WindowInfo/DayInfo -- keeps this module testable with
plain Python values and free of a database dependency.
"""

from dataclasses import dataclass, field
from datetime import datetime

import yaml

from app.domain.slugs import slug_core
from app.domain.timezones import utc_to_jst


@dataclass(frozen=True)
class YamlDay:
    label: str
    starts_at_utc: datetime
    label_en: str | None = None
    label_zh: str | None = None
    city: str | None = None
    venue: str | None = None
    venue_address: str | None = None
    venue_handle: str | None = None   # the VENUE tag's handle; beats `venue`
    doors_at_utc: datetime | None = None
    # The Eventernote event this leg was imported from, when it has one. Written
    # only when set (see below) -- it is provenance, not something an author
    # fills in, so an empty key on every leg of every export would be noise in a
    # file whose other job is to be read by a person.
    eventernote_event_id: str | None = None


@dataclass(frozen=True)
class YamlRound:
    label: str
    kind: str
    label_en: str | None = None
    label_zh: str | None = None
    applies_to_labels: list[str] = field(default_factory=list)
    opens_at_utc: datetime | None = None
    closes_at_utc: datetime | None = None
    results_at_utc: datetime | None = None
    payment_deadline_at_utc: datetime | None = None
    url: str | None = None
    notes: str | None = None


def slugify(title: str) -> str:
    """'Hasunosora 5th Live!' -> 'hasunosora-5th-live'.

    Thin wrapper over `slug_core` with the concert-specific fallback. Kept as
    the public name because `generate_event_id` and two Content-Disposition
    headers import it from here.
    """
    return slug_core(title) or "concert"


def _jst_str(dt: datetime | None) -> str | None:
    return utc_to_jst(dt).strftime("%Y-%m-%d %H:%M") if dt else None


def concert_to_yaml(
    title: str,
    kind: str | None,
    franchises: list[str],
    groups: list[str],
    # REQUIRED, not defaulted, and deliberately so: this kind was added after
    # the format shipped, and a defaulted-to-empty parameter is exactly how the
    # export came to drop CHARACTER tags without a single test noticing. There
    # is one production caller; make the next kind break it too.
    characters: list[str],
    artists: list[str],
    venues: list[str],
    days: list[YamlDay],
    rounds: list[YamlRound],
    notes: str | None,
    title_en: str | None = None,
    organizer: str | None = None,
    categories: str | None = None,
    eventernote_url: str | None = None,
    official_url: str | None = None,
    source_url: str | None = None,
    performers: list[str] | None = None,
    title_zh: str | None = None,
    notes_en: str | None = None,
    notes_zh: str | None = None,
    event_id: str | None = None,
    series_handles: dict[str, list[str]] | None = None,
) -> str:
    """All timestamps are rendered in JST (the "Datetime contract" boundary
    for this app), formatted the same way forms accept them: 'YYYY-MM-DD HH:MM'.
    """
    data = {
        # The URL handle, so a restore lands on the ORIGINAL address. This
        # replaced a `slug` key that was slugify(title) and unrelated to
        # event_id -- two near-identical keys with different meanings is a trap
        # in a file whose whole job is to be read back later. yaml_import still
        # TOLERATES `slug` so drafts written before this keep parsing.
        "event_id": event_id,
        "title": title,
        "title_en": title_en,
        "title_zh": title_zh,
        "kind": kind,
        "organizer": organizer,
        "categories": categories,
        "series": {
            "franchises": franchises,
            "groups": groups,
            # Between groups and artists, matching the editor's own chip order
            # and the fact that a character sits between the two: she belongs
            # to a group and is played by an artist.
            "characters": characters,
            "artists": artists,
        },
        # The same tags by HANDLE. Both are written on purpose: the names so a
        # person reading a restore file sees 佐藤有紀 rather than
        # yuki-sato-liella, the handles so it binds to exactly that tag. The
        # importer treats the handles as authoritative -- names repeat now, and
        # matching by one is a guess.
        "series_handles": series_handles,
        "venues": venues,
        "performers": performers or [],
        "eventernote_url": eventernote_url,
        "official_url": official_url,
        "source_url": source_url,
        "performances": [
            {
                "label": d.label,
                "label_en": d.label_en,
                "label_zh": d.label_zh,
                "city": d.city,
                "venue": d.venue,
                "venue_address": d.venue_address,
                "venue_handle": d.venue_handle,
                "doors_jst": _jst_str(d.doors_at_utc),
                "starts_at_jst": _jst_str(d.starts_at_utc),
                # The one key here written ONLY when it has a value. Every field
                # above is a thing a human fills in, so a null spells out what
                # is still missing; this one is provenance stamped by the
                # importer, and `eventernote_event_id: null` on every leg of
                # every export would just be a key nobody can act on. Absent and
                # null parse identically (yaml_import reads it through _text),
                # so the round trip is unaffected either way.
                **(
                    {"eventernote_event_id": d.eventernote_event_id}
                    if d.eventernote_event_id else {}
                ),
            }
            for d in days
        ],
        "rounds": [
            {
                "label": r.label,
                "label_en": r.label_en,
                "label_zh": r.label_zh,
                "kind": r.kind,
                "applies_to": r.applies_to_labels,
                "apply_opens_jst": _jst_str(r.opens_at_utc),
                "apply_closes_jst": _jst_str(r.closes_at_utc),
                "results_jst": _jst_str(r.results_at_utc),
                "payment_deadline_jst": _jst_str(r.payment_deadline_at_utc),
                "url": r.url,
                "notes": r.notes,
            }
            for r in rounds
        ],
        "notes": notes,
        "notes_en": notes_en,
        "notes_zh": notes_zh,
    }
    return yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
