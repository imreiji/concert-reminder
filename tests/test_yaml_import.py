"""domain/draft.py + domain/yaml_import.py: the two-way draft vocabulary.

Pure-domain tests -- no DB, no routes (route coverage is
tests/test_draft_import.py). Mirrors test_ingest.py's style.
"""

from datetime import datetime

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import RoundKind


def test_extended_fields_default_empty():
    """The ramen parser fills only the original fields; everything the draft
    path adds must default to empty so ingest.py needs no changes beyond its
    import line."""
    day = ParsedDay(label="Day 1", starts_at_jst=datetime(2026, 11, 7, 17, 0))
    assert day.label_en is None and day.label_zh is None
    assert day.doors_at_jst is None and day.venue_name is None
    assert day.venue_city is None and day.venue_address is None
    assert day.matched_venue_tag_id is None

    rnd = ParsedRound(
        label="1次先行", kind=RoundKind.LOTTERY_ROUND,
        opens_at_jst=None, closes_at_jst=None, url=None,
    )
    assert rnd.label_en is None and rnd.label_zh is None
    assert rnd.results_at_jst is None and rnd.payment_at_jst is None
    assert rnd.notes is None and rnd.applies_to_labels == []
    assert rnd.leg_keys == "" and rnd.leg_keys_selected == set()

    parsed = ParsedConcert(title="T", venue_name=None)
    assert parsed.title_en is None and parsed.title_zh is None
    assert parsed.notes is None and parsed.notes_en is None and parsed.notes_zh is None
    assert parsed.organizer is None and parsed.categories is None
    assert parsed.kind is None
    assert parsed.source_url is None and parsed.official_url is None
    assert parsed.eventernote_url is None
    assert parsed.performers_text is None
    assert parsed.franchise_names == [] and parsed.group_names == []
    assert parsed.artist_names == []


def test_ingest_reexports_the_shared_types():
    from app.domain import ingest
    assert ingest.ParsedConcert is ParsedConcert
    assert ingest.ParsedDay is ParsedDay
    assert ingest.ParsedRound is ParsedRound
