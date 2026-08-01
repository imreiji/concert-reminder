"""The tags vocabulary: serializer and parser, in one module on purpose.

Splitting export from import across two files is how the original round-trip
hole opened (see the tag-handles spec) -- so their tests live together too, and
the round-trip test below is the one that matters most.
"""

import pytest

from app.domain.tags_yaml import (
    ParsedTag,
    TagExport,
    TagsFileError,
    parse_tags,
    tags_to_yaml,
)
from app.domain.types import TagKind


def test_round_trips_every_field():
    """The centrepiece: what goes out must come back identical."""
    tags = [
        TagExport(
            handle="k-arena-yokohama", name="Kアリーナ横浜", kind="venue",
            name_en="K Arena Yokohama", name_zh="K竞技场横滨",
            region="Kanto", city="横浜", city_en="Yokohama", city_zh="横滨",
            address="神奈川県横浜市西区みなとみらい",
            location_url="https://maps.example/k-arena",
        ),
        TagExport(
            handle="hasunosora", name="蓮ノ空", kind="group", name_en="Hasunosora",
            parent="love-live", members=("kozue-otomune", "kaho-hinoshita"),
            eventernote_url="https://www.eventernote.com/actors/1",
        ),
    ]
    parsed = parse_tags(tags_to_yaml(tags))

    assert parsed.warnings == []
    # Sorted by (kind, handle): group before venue.
    assert [t.handle for t in parsed.tags] == ["hasunosora", "k-arena-yokohama"]
    group, venue = parsed.tags
    assert venue.kind is TagKind.VENUE
    assert (venue.name, venue.name_en, venue.name_zh) == (
        "Kアリーナ横浜", "K Arena Yokohama", "K竞技场横滨",
    )
    assert (venue.region, venue.city, venue.city_en, venue.city_zh) == (
        "Kanto", "横浜", "Yokohama", "横滨",
    )
    assert venue.address == "神奈川県横浜市西区みなとみらい"
    assert venue.location_url == "https://maps.example/k-arena"
    assert group.kind is TagKind.GROUP
    assert group.parent == "love-live"
    assert group.members == ["kozue-otomune", "kaho-hinoshita"]
    assert group.eventernote_url == "https://www.eventernote.com/actors/1"


def test_empty_fields_are_omitted_not_emitted_as_null():
    """A restore file is read by people; `city: null` on every artist is noise."""
    text = tags_to_yaml([TagExport(handle="kozue", name="乙宗梢", kind="artist")])
    assert "city" not in text
    assert "null" not in text
    assert "members" not in text
    assert "parent" not in text


def test_serialization_is_deterministic():
    """Two exports of an unchanged catalogue must be byte-identical, or the file
    is not diffable and most of its value as a backup is gone."""
    tags = [
        TagExport(handle="b-venue", name="B", kind="venue"),
        TagExport(handle="a-artist", name="A", kind="artist"),
    ]
    assert tags_to_yaml(tags) == tags_to_yaml(tags)
    # Sorted by (kind, handle), NOT input order, so caller ordering cannot leak.
    assert tags_to_yaml(tags) == tags_to_yaml(list(reversed(tags)))
    text = tags_to_yaml(tags)
    assert text.index("a-artist") < text.index("b-venue")


def test_unparseable_input_raises():
    for bad in ("{[", "just a string", "other: 1"):
        with pytest.raises(TagsFileError):
            parse_tags(bad)


def test_a_tag_without_a_handle_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - name: 乙宗梢\n    kind: artist\n")
    assert parsed.tags == []
    assert any("handle" in w for w in parsed.warnings)


def test_a_tag_without_a_name_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - handle: kozue\n    kind: artist\n")
    assert parsed.tags == []
    assert any("name" in w for w in parsed.warnings)


def test_an_unknown_kind_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - handle: k\n    name: K\n    kind: mascot\n")
    assert parsed.tags == []
    assert any("mascot" in w for w in parsed.warnings)


def test_a_duplicate_handle_keeps_the_first_and_warns():
    parsed = parse_tags(
        "tags:\n"
        "  - {handle: kozue, name: 乙宗梢, kind: artist}\n"
        "  - {handle: kozue, name: Someone Else, kind: artist}\n"
    )
    assert [t.name for t in parsed.tags] == ["乙宗梢"]
    assert any("kozue" in w for w in parsed.warnings)


def test_a_handle_is_normalised_and_warns_when_it_changes():
    """The file is machine-written, but a person may edit one. Normalise through
    the same slug_core that mints a handle rather than storing something the app
    could never generate."""
    parsed = parse_tags("tags:\n  - {handle: 'Kozue Otomune!', name: 乙宗梢, kind: artist}\n")
    assert parsed.tags[0].handle == "kozue-otomune"
    assert any("kozue-otomune" in w for w in parsed.warnings)


def test_a_handle_that_normalises_to_nothing_is_skipped():
    parsed = parse_tags("tags:\n  - {handle: 'ホール', name: Hall, kind: venue}\n")
    assert parsed.tags == []
    assert any("handle" in w for w in parsed.warnings)


def test_a_scalar_where_a_list_belongs_warns_rather_than_crashing():
    parsed = parse_tags("tags:\n  - {handle: g, name: G, kind: group, members: kozue}\n")
    assert parsed.tags[0].members == []
    assert any("members" in w for w in parsed.warnings)


def test_an_unknown_key_warns_but_keeps_the_tag():
    parsed = parse_tags(
        "tags:\n  - {handle: kozue, name: 乙宗梢, kind: artist, favourite_food: ramen}\n"
    )
    assert len(parsed.tags) == 1
    assert any("favourite_food" in w for w in parsed.warnings)


def test_a_nested_structure_where_text_belongs_is_refused_not_stringified():
    """Same alias-bomb defence as yaml_import._text: never str() a dict."""
    parsed = parse_tags("tags:\n  - {handle: kozue, name: {a: b}, kind: artist}\n")
    assert parsed.tags == []
    assert any("name" in w for w in parsed.warnings)


def test_parsed_tag_defaults_are_empty_not_none_for_members():
    t = ParsedTag(handle="x", name="X", kind=TagKind.ARTIST)
    assert t.members == []


def test_a_character_round_trips_with_its_seiyuu():
    text = tags_to_yaml([TagExport(
        handle="chihaya", name="如月千早", kind="character", voiced_by="asami-imai",
    )])
    assert "voiced_by: asami-imai" in text
    (parsed,) = parse_tags(text).tags
    assert parsed.voiced_by == "asami-imai"
    assert parsed.kind is TagKind.CHARACTER


def test_voiced_by_is_omitted_when_unset():
    """The exporter omits empty fields -- a human reads these files, and
    `voiced_by: null` on every artist is noise."""
    text = tags_to_yaml([TagExport(handle="imai", name="今井麻美", kind="artist")])
    assert "voiced_by" not in text


def test_voiced_by_is_normalised_as_a_handle_not_kept_as_typed():
    """It is a HANDLE, exactly like `parent` -- so it goes through the same
    slug_core the minting path uses. A person edits these files by hand and will
    type the seiyuu's name; storing that verbatim would resolve against nothing.
    """
    parsed = parse_tags(
        "tags:\n"
        "  - {handle: chihaya, name: 如月千早, kind: character, voiced_by: 'Asami Imai!'}\n"
    )
    assert parsed.tags[0].voiced_by == "asami-imai"


def test_a_nested_structure_in_voiced_by_is_refused_not_stringified():
    parsed = parse_tags(
        "tags:\n  - {handle: c, name: C, kind: character, voiced_by: {a: b}}\n"
    )
    assert parsed.tags[0].voiced_by is None
    assert any("voiced_by" in w for w in parsed.warnings)


def test_voiced_by_is_a_known_key_not_an_unknown_one():
    """`_TAG_KEYS` is what decides whether a key is real or a typo. A field the
    parser reads but the key set does not know warns on every single row."""
    parsed = parse_tags(
        "tags:\n  - {handle: c, name: C, kind: character, voiced_by: imai}\n"
    )
    assert parsed.warnings == []
