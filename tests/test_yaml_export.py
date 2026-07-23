"""concert_to_yaml: pure-function serialization tests.

Round-trip through yaml.safe_load rather than string-matching, so these
don't break on cosmetic formatting changes.
"""

from datetime import UTC, datetime

import yaml

from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml, slugify


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def test_slugify():
    assert slugify("Hasunosora 5th Live!") == "hasunosora-5th-live"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert slugify("日本語タイトル") == "concert"  # no ASCII survives -> fallback


def test_concert_to_yaml_full_shape():
    text = concert_to_yaml(
        title="Hasunosora 5th Live",
        kind="concert",
        franchises=["Hasunosora"],
        groups=["Kaho's Class"],
        artists=["Kaho", "Sayaka"],
        venues=["Yokohama Arena"],
        days=[
            YamlDay(
                label="Day 1", starts_at_utc=dt(8, 1, 9),
                city="Kanagawa", venue="K Arena Yokohama", venue_address="Yokohama, Japan",
                doors_at_utc=dt(8, 1, 8),
            ),
            YamlDay(label="Day 2", starts_at_utc=dt(8, 2, 9)),
        ],
        rounds=[
            YamlRound(
                label="Day 1 Lottery #1", label_en="Day 1 lottery round 1", kind="lottery_round",
                applies_to_labels=["Day 1"],
                opens_at_utc=dt(6, 10), closes_at_utc=dt(6, 25),
                results_at_utc=dt(7, 1), payment_deadline_at_utc=dt(7, 8),
                url="https://example.com/ticket", notes="CD-code lottery",
            ),
        ],
        notes="Some notes",
        title_en="Hasunosora 5th Live (EN)",
        organizer="LustQueen",
        categories="concert, tour",
        eventernote_url="https://eventernote.com/x",
        official_url="https://official.example/x",
        source_url="https://ramen.events/x",
        performers=["Kaho", "Sayaka"],
    )
    data = yaml.safe_load(text)

    assert data["slug"] == "hasunosora-5th-live"
    assert data["title"] == "Hasunosora 5th Live"
    assert data["title_en"] == "Hasunosora 5th Live (EN)"
    assert data["kind"] == "concert"
    assert data["organizer"] == "LustQueen"
    assert data["categories"] == "concert, tour"
    assert data["series"] == {
        "franchises": ["Hasunosora"], "groups": ["Kaho's Class"], "artists": ["Kaho", "Sayaka"],
    }
    assert data["venues"] == ["Yokohama Arena"]
    assert data["performers"] == ["Kaho", "Sayaka"]
    assert data["eventernote_url"] == "https://eventernote.com/x"
    assert data["official_url"] == "https://official.example/x"
    assert data["source_url"] == "https://ramen.events/x"
    assert data["notes"] == "Some notes"

    assert len(data["performances"]) == 2
    assert data["performances"][0] == {
        "label": "Day 1", "label_en": None, "label_zh": None,
        "city": "Kanagawa", "venue": "K Arena Yokohama",
        "venue_address": "Yokohama, Japan", "doors_jst": "2026-08-01 17:00",
        "starts_at_jst": "2026-08-01 18:00",
    }
    assert data["performances"][1] == {
        "label": "Day 2", "label_en": None, "label_zh": None,
        "city": None, "venue": None, "venue_address": None,
        "doors_jst": None, "starts_at_jst": "2026-08-02 18:00",
    }

    (round_,) = data["rounds"]
    assert round_["label"] == "Day 1 Lottery #1"
    assert round_["label_en"] == "Day 1 lottery round 1"
    assert round_["kind"] == "lottery_round"
    assert round_["applies_to"] == ["Day 1"]
    assert round_["apply_opens_jst"] == "2026-06-10 21:00"  # UTC 12:00 + 9h
    assert round_["apply_closes_jst"] == "2026-06-25 21:00"
    assert round_["results_jst"] == "2026-07-01 21:00"
    assert round_["payment_deadline_jst"] == "2026-07-08 21:00"
    assert round_["url"] == "https://example.com/ticket"
    assert round_["notes"] == "CD-code lottery"


def test_concert_to_yaml_handles_missing_optional_fields():
    text = concert_to_yaml(
        title="Bare Concert",
        kind=None,
        franchises=[], groups=[], artists=[], venues=[],
        days=[],
        rounds=[YamlRound(label="TBD round", kind="other")],
        notes=None,
    )
    data = yaml.safe_load(text)
    assert data["kind"] is None
    assert data["title_en"] is None
    assert data["organizer"] is None
    assert data["categories"] is None
    assert data["performers"] == []
    assert data["eventernote_url"] is None
    assert data["performances"] == []
    (round_,) = data["rounds"]
    assert round_["label_en"] is None
    assert round_["applies_to"] == []
    assert round_["apply_opens_jst"] is None
    assert round_["url"] is None
    assert round_["notes"] is None
    assert data["notes"] is None


def test_yaml_export_carries_every_label_variant():
    """YamlDay gains label_en/label_zh; YamlRound gains label_zh beside its
    existing label_en. An export is data, not a viewer-facing render, so
    every variant is emitted -- no locale resolution happens here."""
    text = concert_to_yaml(
        title="T", kind="tour", franchises=[], groups=[], artists=[], venues=[],
        days=[
            YamlDay(
                label="2日目", label_en="Day 2", label_zh="第二天",
                starts_at_utc=dt(8, 1),
            ),
        ],
        rounds=[
            YamlRound(
                label="抽選#1", label_en="Lottery #1", label_zh="抽签#1",
                kind="lottery_round",
            ),
        ],
        notes=None,
    )
    data = yaml.safe_load(text)

    (day,) = data["performances"]
    assert day["label"] == "2日目"
    assert day["label_en"] == "Day 2"
    assert day["label_zh"] == "第二天"

    (round_,) = data["rounds"]
    assert round_["label"] == "抽選#1"
    assert round_["label_en"] == "Lottery #1"
    assert round_["label_zh"] == "抽签#1"


def test_title_zh_and_notes_variants_export():
    text = concert_to_yaml(
        title="T", kind=None, franchises=[], groups=[], artists=[], venues=[],
        days=[], rounds=[], notes="メモ",
        title_zh="T中文", notes_en="note", notes_zh="笔记",
    )
    data = yaml.safe_load(text)
    assert data["title_zh"] == "T中文"
    assert data["notes_en"] == "note"
    assert data["notes_zh"] == "笔记"


def test_concert_to_yaml_is_deterministic():
    kwargs = dict(
        title="C", kind="tour", franchises=[], groups=[], artists=[], venues=[],
        days=[YamlDay(label="Day 1", starts_at_utc=dt(8, 1))],
        rounds=[YamlRound(label="R1", kind="lottery_round")],
        notes=None,
    )
    assert concert_to_yaml(**kwargs) == concert_to_yaml(**kwargs)
