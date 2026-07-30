"""The draft-concert vocabulary: what a not-yet-saved concert looks like.

Two producers fill these dataclasses -- domain/ingest.py (the ramen.events
scrape) and domain/yaml_import.py (a pasted agent draft) -- and one consumer
renders them (import_preview.html via routes/imports.py). The ramen parser
fills only the original subset; every field the draft path added defaults to
empty, so a producer never has to know about the other's fields.

Pure data carriers: no I/O, no ORM. Datetimes are naive JST wall-clock,
exactly like a web form's <input type="datetime-local"> -- conversion to UTC
happens at the commit boundary (concerts.parse_jst), never here.

`matched_venue_tag_id`, `leg_keys` and `leg_keys_selected` are resolved at
the ROUTE boundary (they need the DB's tag list / the preview's day_key
scheme) and stamped onto the dataclasses before rendering; the parsers
always leave them empty.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.types import ConcertKind, RoundKind


@dataclass
class ParsedDay:
    label: str
    starts_at_jst: datetime
    label_en: str | None = None
    label_zh: str | None = None
    doors_at_jst: datetime | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    venue_address: str | None = None
    matched_venue_tag_id: int | None = None  # route-resolved, never parsed


@dataclass
class ParsedRound:
    label: str
    kind: RoundKind
    opens_at_jst: datetime | None
    closes_at_jst: datetime | None
    url: str | None
    label_en: str | None = None
    label_zh: str | None = None
    results_at_jst: datetime | None = None
    payment_at_jst: datetime | None = None
    notes: str | None = None
    applies_to_labels: list[str] = field(default_factory=list)
    leg_keys: str = ""                                  # route-resolved
    leg_keys_selected: set[str] = field(default_factory=set)  # route-resolved


@dataclass
class ParsedConcert:
    title: str
    venue_name: str | None
    # The URL handle, when a draft carries one. An export writes it so a
    # restore lands on the original address; an agent-authored draft omits it
    # and import_commit generates one as before.
    event_id: str | None = None
    days: list[ParsedDay] = field(default_factory=list)
    rounds: list[ParsedRound] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title_en: str | None = None
    title_zh: str | None = None
    notes: str | None = None
    notes_en: str | None = None
    notes_zh: str | None = None
    organizer: str | None = None
    categories: str | None = None
    kind: ConcertKind | None = None
    source_url: str | None = None
    official_url: str | None = None
    eventernote_url: str | None = None
    performers_text: str | None = None
    franchise_names: list[str] = field(default_factory=list)
    group_names: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)
