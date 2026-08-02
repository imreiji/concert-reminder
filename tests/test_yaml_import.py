"""domain/draft.py + domain/yaml_import.py: the two-way draft vocabulary.

Pure-domain tests -- no DB, no routes (route coverage is
tests/test_draft_import.py). Mirrors test_ingest.py's style.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.models import Tag, TagKind
from app.db.service import match_tag_ids_by_name
from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import ConcertKind, RoundKind
from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml
from app.domain.yaml_import import DraftError, parse_draft, parse_drafts, split_documents


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


def test_round_requires_parses():
    parsed = parse_draft(
        "title: t\n"
        "rounds:\n"
        "  - label: グッズ販売\n"
        "    kind: goods_sale\n"
        "  - label: 最速先行\n"
        "    kind: lottery_round\n"
        "    requires: グッズ販売\n"
    )
    assert parsed.rounds[1].requires_label == "グッズ販売"
    assert not [w for w in parsed.warnings if "unknown key" in w]


def test_slug_and_venues_keys_are_ignored_silently():
    """Both appear in every yaml_export output; neither is draft input (slug is
    derived, concert venues are derived from legs), so round-tripping an export
    must not warn about them."""
    p = parse_draft("title: T\nslug: t\nvenues: [Somewhere]\n")
    assert not p.warnings


def test_deeply_nested_flow_raises_draft_error_not_recursion_error():
    with pytest.raises(DraftError):
        parse_draft("title: " + "[" * 500 + "]" * 500)


def test_the_too_deep_message_says_what_the_author_can_do():
    """The type alone is not the contract -- what the author reads in the
    import banner is. Such a draft is often perfectly well-formed YAML that
    merely out-nests PyYAML's recursive-descent parser, so the old
    "that doesn't parse as YAML" was false, and CPython's own "maximum
    recursion depth exceeded" named nothing anyone could act on."""
    with pytest.raises(DraftError) as exc:
        parse_draft("title: " + "[" * 500 + "]" * 500)
    msg = str(exc.value)
    assert "doesn't parse as YAML" not in msg
    assert "recursion" not in msg.lower()
    assert "nests too deeply" in msg
    assert "flatten" in msg


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


def test_a_container_value_warns_instead_of_blanking_silently():
    """_dt warns when it gets a container; _text used to blank silently, so a
    list organizer/notes/label/url left no drift alarm at all. The warning
    names the FIELD and the type -- never the value: str()'ing a container is
    the exponential alias-fan-out cost the DoS fix removed, and re-introducing
    it inside the warning would reopen exactly that hole."""
    p = parse_draft("title: T\norganizer: [bandai, sunrise]\n")
    assert p.organizer is None
    assert any("organizer" in w for w in p.warnings)
    assert not any("bandai" in w for w in p.warnings)


def test_container_value_for_scalar_field_blanks():
    p = parse_draft(
        "title: T\norganizer: {corp: bandai}\nperformances:\n  - label: D\n"
        "    starts_at_jst: [2026, 11, 7]\n"
    )
    assert p.organizer is None
    assert p.days[0].starts_at_jst is None
    assert any("starts_at_jst" in w for w in p.warnings)


def _utc(y, mo, d, h, mi):
    """The export takes aware UTC; 17:00 JST == 08:00 UTC."""
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_export_then_parse_round_trips():
    text = concert_to_yaml(
        title="6thライブ", kind="tour",
        franchises=["Love Live!"], groups=["蓮ノ空"], characters=[], artists=["日野下花帆"],
        venues=["Kアリーナ横浜"],
        days=[YamlDay(
            label="Day 1", label_en="Day 1", label_zh="第1天",
            starts_at_utc=_utc(2026, 11, 7, 8, 0),
            city="横浜", venue="Kアリーナ横浜", venue_address="みなとみらい6-2-14",
            doors_at_utc=_utc(2026, 11, 7, 6, 30),
        )],
        rounds=[YamlRound(
            label="最速先行", label_en="Earliest", label_zh="最速先行(中)",
            kind="lottery_round", applies_to_labels=["Day 1"],
            opens_at_utc=_utc(2026, 8, 1, 3, 0), closes_at_utc=_utc(2026, 8, 16, 14, 59),
            results_at_utc=_utc(2026, 8, 22, 6, 0),
            payment_deadline_at_utc=_utc(2026, 8, 25, 14, 0),
            url="https://eplus.jp/x/", notes="シリアル",
        )],
        notes="全席指定", title_en="6th Live", organizer="バンナム",
        categories="anime", eventernote_url="https://www.eventernote.com/events/1",
        official_url="https://example.jp/", source_url="https://example.jp/t/",
        performers=["日野下花帆"],
        title_zh="6th 演唱会", notes_en="All reserved", notes_zh="全指定席",
    )
    p = parse_draft(text)
    assert p.warnings == []
    assert (p.title, p.title_en, p.title_zh) == ("6thライブ", "6th Live", "6th 演唱会")
    assert (p.notes, p.notes_en, p.notes_zh) == ("全席指定", "All reserved", "全指定席")
    assert p.kind is ConcertKind.TOUR
    assert p.franchise_names == ["Love Live!"] and p.artist_names == ["日野下花帆"]
    assert p.performers_text == "日野下花帆"
    d = p.days[0]
    assert d.starts_at_jst == datetime(2026, 11, 7, 17, 0)   # 08:00 UTC -> 17:00 JST
    assert d.doors_at_jst == datetime(2026, 11, 7, 15, 30)
    assert d.venue_name == "Kアリーナ横浜" and d.venue_city == "横浜"
    r = p.rounds[0]
    assert (r.label, r.label_en, r.label_zh) == ("最速先行", "Earliest", "最速先行(中)")
    assert r.kind is RoundKind.LOTTERY_ROUND
    assert r.applies_to_labels == ["Day 1"]
    assert r.closes_at_jst == datetime(2026, 8, 16, 23, 59)
    assert r.results_at_jst == datetime(2026, 8, 22, 15, 0)
    assert r.payment_at_jst == datetime(2026, 8, 25, 23, 0)
    assert r.url == "https://eplus.jp/x/" and r.notes == "シリアル"


def _tag(id_, name, name_en=None, name_zh=None):
    t = Tag(name=name, kind=TagKind.ARTIST, name_en=name_en, name_zh=name_zh)
    t.id = id_
    return t


def test_match_tag_ids_by_name_across_all_three_columns():
    tags = [
        _tag(1, "日野下花帆", name_en="Kaho Hinoshita"),
        _tag(2, "村野さやか", name_zh="村野沙耶香"),
    ]
    ids, missing = match_tag_ids_by_name(
        ["Kaho Hinoshita", "村野沙耶香", "誰それ"], tags
    )
    assert ids == [1, 2]
    assert missing == ["誰それ"]


def test_match_tag_ids_by_name_trims_and_casefolds():
    tags = [_tag(3, "Liella!", name_en="liella!")]
    ids, missing = match_tag_ids_by_name(["　LIELLA!　"], tags)
    assert ids == [3] and missing == []


def test_match_tag_ids_by_name_dedupes_ids():
    tags = [_tag(4, "Aqours", name_en="Aqours")]
    ids, missing = match_tag_ids_by_name(["Aqours", "aqours"], tags)
    assert ids == [4] and missing == []


# -- The add-concert skill's example draft --------------------------------

SKILL_EXAMPLE = (
    Path(__file__).parent.parent / ".claude" / "skills" / "add-concert"
    / "references" / "example-draft.yaml"
)


def test_skill_example_draft_parses_clean():
    """The example the add-concert skill shows agents MUST parse with zero
    warnings -- a warning here means the skill and parser have drifted."""
    p = parse_draft(SKILL_EXAMPLE.read_text(encoding="utf-8"))
    assert p.warnings == []
    assert p.title and p.title_en and p.title_zh
    assert p.days and p.rounds
    assert all(d.venue_name for d in p.days)
    assert all(r.label_en and r.label_zh for r in p.rounds)


# -- Multi-document paste (split_documents / parse_drafts) -----------------

ONE = "title: One\n"
TWO = "title: Two\n"
THREE = "title: Three\n"


def test_three_documents_parse_into_three_drafts():
    batch = parse_drafts(ONE + "\n---\n" + TWO + "\n---\n" + THREE)
    assert len(batch.drafts) == 3
    assert batch.errors == ()
    assert [d.parsed.title for d in batch.drafts] == ["One", "Two", "Three"]


def test_one_bad_document_does_not_lose_the_others():
    """The whole point at fifty concerts: a typo in draft 2 must not cost
    drafts 1 and 3.

    Asserts the literal `"document 2:"` prefix, not a bare `"2" in
    message` -- PyYAML's own error text for `title: [unclosed` happens to
    contain "line 2, column 8", which would make a bare substring check
    pass even under an off-by-one (0-based) numbering bug. The prefix is
    this module's own text, so only correct 1-based numbering satisfies it."""
    batch = parse_drafts(ONE + "\n---\n" + "title: [unclosed\n" + "\n---\n" + THREE)
    assert len(batch.drafts) == 2
    assert len(batch.errors) == 1
    assert batch.errors[0].startswith("document 2:"), "the error must say WHICH document failed"


def test_a_single_document_still_works():
    """A file with no --- separator is a batch of one, so one paste box can
    serve both cases and nobody has to know which they have."""
    assert len(parse_drafts(ONE).drafts) == 1


def test_each_draft_keeps_its_own_text_verbatim():
    """The row stores the document, not the parse, so a later preview re-parses
    it exactly as if it had been pasted alone. Checked on BOTH documents, not
    just the first -- only the first document is ever separator-free for
    free (nothing precedes it), so a check that stopped at drafts[0] would
    not catch a later draft keeping its own leading `---` marker."""
    batch = parse_drafts(ONE + "\n---\n" + TWO)
    assert batch.drafts[0].text.strip().startswith(ONE.strip()[:20])
    assert "---" not in batch.drafts[0].text
    assert batch.drafts[1].text.strip().startswith(TWO.strip()[:20])
    assert "---" not in batch.drafts[1].text


def test_empty_documents_are_skipped_not_errors():
    """Trailing separators and blank stanzas are formatting, not mistakes --
    `a\n---\n` is one draft, and a stray `---` at the end must not report a
    phantom failure."""
    batch = parse_drafts(ONE + "\n---\n\n---\n" + TWO)
    assert len(batch.drafts) == 2
    assert batch.errors == ()


def test_a_leading_comment_does_not_become_a_phantom_document():
    """`_split_on_scan_boundaries` always seeds a boundary at index 0, so a
    header comment above the first `---` -- a plausible thing for an agent
    summarizing a research sweep to write ("# 12 drafts from the sweep") --
    used to become its own chunk that parsed to `None` and raised as
    "document 1", shifting every REAL document's number by one right along
    with it. A comment-only chunk must be dropped as pure formatting, the
    same as a blank stanza, not reported as a failure."""
    text = "# 12 drafts from the sweep\n---\n" + ONE + "\n---\n" + TWO
    batch = parse_drafts(text)
    assert batch.errors == (), (
        f"a header comment must not become a phantom document: {batch.errors}"
    )
    assert [d.parsed.title for d in batch.drafts] == ["One", "Two"]


def test_a_comment_only_document_between_two_real_ones_is_dropped_not_reported():
    """The same drop applies mid-stream, not just at the very start -- a
    comment used as a separator note is formatting wherever it sits."""
    text = ONE + "\n---\n# just a note\n---\n" + TWO
    batch = parse_drafts(text)
    assert batch.errors == ()
    assert [d.parsed.title for d in batch.drafts] == ["One", "Two"]


def test_a_wholly_empty_paste_is_an_error_not_an_empty_batch():
    for text in ("", "   \n", "---\n---\n"):
        batch = parse_drafts(text)
        assert batch.drafts == ()
        assert batch.errors, "an empty paste must say so, not report success"


def test_safe_load_all_only(monkeypatch):
    """A YAML tag that would construct a Python object must not -- proven by
    the payload's own side effect never firing, not merely by checking that
    an error was reported (which `parse_draft` already guaranteed on its
    own via `yaml.safe_load`, before this module existed). What THIS module
    is responsible for is not routing a document through anything other
    than `parse_draft` on the way there -- `split_documents` only ever
    calls `yaml.scan`, which composes no object regardless of the loader it
    is handed (scanning never invokes a constructor), so there is nothing
    else in this file to pin beyond "the document still reaches
    parse_draft". Monkeypatching `os.system` turns this into a real
    regression guard: if `parse_draft` ever switched to `yaml.load`/
    `full_load`/an unsafe loader, this tag would call `os.system('echo
    hi')` during construction and `calls` would be non-empty."""
    calls: list[str] = []
    monkeypatch.setattr(os, "system", lambda cmd: calls.append(cmd))
    batch = parse_drafts("!!python/object/apply:os.system ['echo hi']\n")
    assert batch.drafts == ()
    assert batch.errors
    assert calls == []


def test_triple_dash_inside_a_block_scalar_does_not_split_the_document():
    """The subtle case this whole module exists for: a `---` that is part of
    a Japanese free-text note (a literal block, `notes: |`) must not be
    mistaken for a document boundary, or the note gets cut in half and the
    second half either becomes a bogus extra document or breaks the first
    document's YAML outright."""
    text = (
        "title: One\n"
        "notes: |\n"
        "  line one\n"
        "  ---\n"
        "  line two\n"
        "---\n"
        "title: Two\n"
    )
    docs = split_documents(text)
    assert len(docs) == 2
    batch = parse_drafts(text)
    assert len(batch.drafts) == 2
    assert batch.errors == ()
    assert batch.drafts[0].parsed.notes == "line one\n---\nline two"
    assert batch.drafts[1].parsed.title == "Two"


# -- Scanner-level breakage (a single character must not defeat the whole
# -- paste): yaml.scan() runs once over the WHOLE raw text before any
# -- per-document isolation happens, so a construct that breaks PyYAML's
# -- *lexer* -- not just its parser -- makes that one scan() call raise for
# -- everything, not just the offending document. split_documents must fall
# -- back to a line-based split in that case (see its docstring), so the
# -- other documents still come back as drafts and only the broken one
# -- becomes a single named error. Three realistic triggers, each plausible
# -- from a browser copy-paste on Windows: a tab-indented line, an
# -- unterminated quoted string, and a bad backslash escape. All three were
# -- confirmed (before the fix) to collapse a 3-document batch into
# -- `drafts=()` and one generic, undated error.


def test_tab_indentation_in_one_document_does_not_lose_the_others():
    bad = "title: Bad\n\tnotes: tabbed\n"
    batch = parse_drafts(ONE + "\n---\n" + bad + "\n---\n" + THREE)
    assert [d.parsed.title for d in batch.drafts] == ["One", "Three"]
    assert len(batch.errors) == 1
    assert batch.errors[0].startswith("document 2:")


def test_unterminated_quote_in_one_document_does_not_lose_the_others():
    bad = 'title: "unterminated\n'
    batch = parse_drafts(ONE + "\n---\n" + bad + "\n---\n" + THREE)
    assert [d.parsed.title for d in batch.drafts] == ["One", "Three"]
    assert len(batch.errors) == 1
    assert batch.errors[0].startswith("document 2:")


def test_bad_backslash_escape_in_one_document_does_not_lose_the_others():
    bad = 'title: "bad \\q escape"\n'
    batch = parse_drafts(ONE + "\n---\n" + bad + "\n---\n" + THREE)
    assert [d.parsed.title for d in batch.drafts] == ["One", "Three"]
    assert len(batch.errors) == 1
    assert batch.errors[0].startswith("document 2:")
