"""domain/draft.py + domain/yaml_import.py: the two-way draft vocabulary.

Pure-domain tests -- no DB, no routes (route coverage is
tests/test_draft_import.py). Mirrors test_ingest.py's style.
"""

from datetime import datetime

import pytest

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import ConcertKind, RoundKind
from app.domain.yaml_import import DraftError, parse_draft


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


FULL_DRAFT = """\
title: 蓮ノ空女学院スクールアイドルクラブ 6th ライブ
title_en: Hasunosora 6th Live
title_zh: 莲之空女学院学园偶像社 6th 演唱会
kind: tour
organizer: バンダイナムコ
categories: anime song
series:
  franchises: [Love Live!]
  groups: [蓮ノ空女学院スクールアイドルクラブ]
  artists: [日野下花帆, 村野さやか]
performers: [日野下花帆, 村野さやか]
eventernote_url: https://www.eventernote.com/events/465358
official_url: https://www.lovelive-anime.jp/hasunosora/
source_url: https://www.lovelive-anime.jp/hasunosora/live-event/live_detail.php?p=6th
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Kアリーナ横浜
    city: 横浜
    venue_address: 神奈川県横浜市西区みなとみらい6-2-14
    doors_jst: 2026-11-07 15:30
    starts_at_jst: 2026-11-07 17:00
  - label: Day 2
    label_en: Day 2
    label_zh: 第2天
    venue: Kアリーナ横浜
    starts_at_jst: 2026-11-08 17:00
rounds:
  - label: 最速先行抽選
    label_en: Earliest advance lottery
    label_zh: 最速先行抽选
    kind: lottery_round
    applies_to: [Day 1, Day 2]
    apply_opens_jst: 2026-08-01 12:00
    apply_closes_jst: 2026-08-16 23:59
    results_jst: 2026-08-22 15:00
    payment_deadline_jst: 2026-08-25 23:00
    url: https://eplus.jp/hasu6th/
    notes: CD封入シリアル
notes: 全席指定
notes_en: All seats reserved
notes_zh: 全部为指定席
"""


def test_full_draft_parses_without_warnings():
    p = parse_draft(FULL_DRAFT)
    assert p.warnings == []
    assert p.title == "蓮ノ空女学院スクールアイドルクラブ 6th ライブ"
    assert p.title_en == "Hasunosora 6th Live"
    assert p.title_zh == "莲之空女学院学园偶像社 6th 演唱会"
    assert p.kind is ConcertKind.TOUR
    assert p.organizer == "バンダイナムコ"
    assert p.categories == "anime song"
    assert p.franchise_names == ["Love Live!"]
    assert p.group_names == ["蓮ノ空女学院スクールアイドルクラブ"]
    assert p.artist_names == ["日野下花帆", "村野さやか"]
    assert p.performers_text == "日野下花帆\n村野さやか"
    assert p.eventernote_url == "https://www.eventernote.com/events/465358"
    assert p.source_url is not None and p.official_url is not None
    assert p.notes == "全席指定" and p.notes_en and p.notes_zh

    assert len(p.days) == 2
    d1 = p.days[0]
    assert d1.label == "Day 1" and d1.label_zh == "第1天"
    assert d1.venue_name == "Kアリーナ横浜" and d1.venue_city == "横浜"
    assert d1.venue_address.startswith("神奈川県")
    assert d1.doors_at_jst == datetime(2026, 11, 7, 15, 30)
    assert d1.starts_at_jst == datetime(2026, 11, 7, 17, 0)
    assert p.days[1].doors_at_jst is None

    assert len(p.rounds) == 1
    r = p.rounds[0]
    assert r.label == "最速先行抽選" and r.label_en and r.label_zh
    assert r.kind is RoundKind.LOTTERY_ROUND
    assert r.applies_to_labels == ["Day 1", "Day 2"]
    assert r.opens_at_jst == datetime(2026, 8, 1, 12, 0)
    assert r.closes_at_jst == datetime(2026, 8, 16, 23, 59)
    assert r.results_at_jst == datetime(2026, 8, 22, 15, 0)
    assert r.payment_at_jst == datetime(2026, 8, 25, 23, 0)
    assert r.url == "https://eplus.jp/hasu6th/"
    assert r.notes == "CD封入シリアル"


def test_not_yaml_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("title: [unclosed")


def test_non_mapping_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("- just\n- a\n- list\n")


def test_missing_title_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("kind: tour\n")


def test_unknown_round_kind_falls_back_to_other_with_warning():
    p = parse_draft("title: T\nrounds:\n  - label: X\n    kind: mystery_meat\n")
    assert p.rounds[0].kind is RoundKind.OTHER
    assert any("mystery_meat" in w for w in p.warnings)


def test_round_kind_accepts_enum_name_case_insensitively():
    p = parse_draft("title: T\nrounds:\n  - label: X\n    kind: FCFS_SALE\n")
    assert p.rounds[0].kind is RoundKind.FCFS_SALE
    assert p.warnings == []


def test_unknown_concert_kind_warns_and_clears():
    p = parse_draft("title: T\nkind: hootenanny\n")
    assert p.kind is None
    assert any("hootenanny" in w for w in p.warnings)


def test_malformed_datetime_warns_and_blanks():
    p = parse_draft(
        "title: T\nperformances:\n  - label: Day 1\n    starts_at_jst: sometime soon\n"
    )
    assert p.days[0].starts_at_jst is None
    assert any("sometime soon" in w for w in p.warnings)


def test_t_separator_datetime_accepted():
    p = parse_draft(
        "title: T\nperformances:\n  - label: D\n    starts_at_jst: 2026-11-07T17:00\n"
    )
    assert p.days[0].starts_at_jst == datetime(2026, 11, 7, 17, 0)


def test_unknown_keys_warn_but_do_not_fail():
    p = parse_draft(
        "title: T\nfrobnicator: 9\nperformances:\n  - label: D\n"
        "    starts_at_jst: 2026-11-07 17:00\n    hovercraft: full of eels\n"
    )
    assert p.title == "T"
    assert any("frobnicator" in w for w in p.warnings)
    assert any("hovercraft" in w for w in p.warnings)


def test_slug_and_venues_keys_are_ignored_silently():
    """Both appear in every yaml_export output; neither is draft input (slug is
    derived, concert venues are derived from legs), so round-tripping an export
    must not warn about them."""
    p = parse_draft("title: T\nslug: t\nvenues: [Somewhere]\n")
    assert not p.warnings


def test_deeply_nested_flow_raises_draft_error_not_recursion_error():
    with pytest.raises(DraftError):
        parse_draft("title: " + "[" * 500 + "]" * 500)


def test_anchor_fanout_completes_and_rejects_container_title():
    """A tiny alias-DAG payload must neither hang (str() on shared sub-lists
    is exponential) nor crash: the container title reads as no-title."""
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 12):
        prev = f"*a{i-1}, " * 10
        lines.append(f"a{i}: &a{i} [{prev.rstrip(', ')}]")
    lines.append("title: *a11")
    with pytest.raises(DraftError):
        parse_draft("\n".join(lines))


def test_container_value_for_scalar_field_blanks():
    p = parse_draft(
        "title: T\norganizer: {corp: bandai}\nperformances:\n  - label: D\n"
        "    starts_at_jst: [2026, 11, 7]\n"
    )
    assert p.organizer is None
    assert p.days[0].starts_at_jst is None
    assert any("starts_at_jst" in w for w in p.warnings)
