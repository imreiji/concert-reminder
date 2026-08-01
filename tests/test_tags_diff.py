"""The pure differ: what an import WOULD do, before anything is written.

Its own module rather than joining tags_yaml.py: that one is about the FORMAT
(serialize/parse), this is about COMPARISON, and a module with three jobs is how
a file starts growing unwieldy.
"""

from app.domain.tags_diff import COMPARABLE_FIELDS, plan_tag_import
from app.domain.tags_yaml import ParsedTag, TagExport
from app.domain.types import TagKind


def _incoming(handle="kozue", **kw):
    base = dict(handle=handle, name="乙宗梢", kind=TagKind.ARTIST)
    return ParsedTag(**{**base, **kw})


def _current(handle="kozue", **kw):
    base = dict(handle=handle, name="乙宗梢", kind="artist")
    return TagExport(**{**base, **kw})


def test_a_handle_not_in_the_catalogue_is_new():
    plan = plan_tag_import([_incoming()], [])
    (tag,) = plan.tags
    assert tag.is_new
    assert tag.conflicts == [] and tag.fills == {}
    assert [t.handle for t in plan.created] == ["kozue"]


def test_a_blank_field_is_a_fill_not_a_conflict():
    """Writing into emptiness cannot lose anything, so it needs no decision."""
    plan = plan_tag_import(
        [_incoming(eventernote_url="https://www.eventernote.com/actors/1")],
        [_current(eventernote_url=None)],
    )
    (tag,) = plan.tags
    assert tag.fills == {"eventernote_url": "https://www.eventernote.com/actors/1"}
    assert tag.conflicts == []
    assert not tag.is_new


def test_an_empty_string_counts_as_blank():
    """Every writer normalises "" -> None today, but the differ must not depend
    on that holding forever."""
    plan = plan_tag_import([_incoming(city="横浜")], [_current(city="")])
    assert plan.tags[0].fills == {"city": "横浜"}
    assert plan.tags[0].conflicts == []


def test_a_blank_in_the_file_changes_nothing():
    plan = plan_tag_import([_incoming(city=None)], [_current(city="横浜")])
    (tag,) = plan.tags
    assert tag.fills == {} and tag.conflicts == []
    assert not tag.needs_choice


def test_identical_values_change_nothing():
    plan = plan_tag_import([_incoming(city="横浜")], [_current(city="横浜")])
    assert plan.tags[0].fills == {} and plan.tags[0].conflicts == []


def test_two_different_values_are_a_conflict():
    plan = plan_tag_import([_incoming(name_en="Kozue")], [_current(name_en="Kozue Otomune")])
    (tag,) = plan.tags
    assert tag.fills == {}
    (conflict,) = tag.conflicts
    assert (conflict.field, conflict.current, conflict.incoming) == (
        "name_en", "Kozue Otomune", "Kozue",
    )
    assert tag.needs_choice
    assert [t.handle for t in plan.conflicted] == ["kozue"]


def test_every_comparable_field_is_actually_compared():
    """A field silently missing from COMPARABLE_FIELDS would never conflict and
    never fill -- it would just be ignored, which is the quiet kind of wrong."""
    incoming = _incoming(
        name="A", name_en="B", name_zh="C", parent="p", region="R", city="D",
        city_en="E", city_zh="F", address="G", location_url="http://h",
        eventernote_url="http://i", voiced_by="j",
    )
    current = _current(
        name="z", name_en="z", name_zh="z", parent="z", region="z", city="z",
        city_en="z", city_zh="z", address="z", location_url="z",
        eventernote_url="z", voiced_by="z",
    )
    conflicts = {c.field for c in plan_tag_import([incoming], [current]).tags[0].conflicts}
    assert conflicts == set(COMPARABLE_FIELDS)


def test_voiced_by_is_compared_like_any_other_field():
    plan = plan_tag_import(
        [_incoming(voiced_by="asami-imai")], [_current(voiced_by=None)]
    )
    assert plan.tags[0].fills == {"voiced_by": "asami-imai"}


def test_a_recast_is_a_conflict_not_a_silent_fill():
    """A character already pointed at one seiyuu, and a file naming another, is
    two different values -- so it is a decision, not an automatic write."""
    plan = plan_tag_import(
        [_incoming(voiced_by="asami-imai")], [_current(voiced_by="someone-else")]
    )
    (tag,) = plan.tags
    assert tag.fills == {}
    (conflict,) = tag.conflicts
    assert (conflict.field, conflict.current, conflict.incoming) == (
        "voiced_by", "someone-else", "asami-imai",
    )


def test_the_comparable_field_count_moved_to_twelve():
    """This pin exists so a field cannot join the FORMAT while the differ
    silently skips it -- which would make the reformat look successful and
    quietly drop every seiyuu link."""
    assert len(COMPARABLE_FIELDS) == 12
    assert "voiced_by" in COMPARABLE_FIELDS


def test_kind_disagreeing_skips_the_tag_entirely():
    """A venue arriving as an artist could orphan a leg's venue_tag_id. Not
    choosable: warn loudly and touch nothing, fills included."""
    plan = plan_tag_import(
        [_incoming(kind=TagKind.VENUE, city="横浜")],
        [_current(kind="artist", city=None)],
    )
    (tag,) = plan.tags
    assert tag.kind_mismatch
    assert tag.fills == {} and tag.conflicts == []
    assert not tag.needs_choice
    # Assert what the operator needs from the warning -- which tag, and both
    # kinds -- rather than the literal word "kind", which is a brittle proxy.
    (warning,) = plan.warnings
    assert "kozue" in warning
    assert "venue" in warning and "artist" in warning


def test_member_additions_and_removals_are_separate_directions():
    plan = plan_tag_import(
        [_incoming(handle="g", kind=TagKind.GROUP, members=["a", "b"])],
        [_current(handle="g", kind="group", members=("b", "c"))],
    )
    (tag,) = plan.tags
    assert tag.member_additions == ["a"]
    assert tag.member_removals == ["c"]
    assert tag.needs_choice


def test_a_new_tag_has_no_member_diff():
    """Its members are simply created; there is nothing to compare against."""
    plan = plan_tag_import([_incoming(handle="g", kind=TagKind.GROUP, members=["a"])], [])
    (tag,) = plan.tags
    assert tag.is_new
    assert tag.member_additions == [] and tag.member_removals == []


def test_parent_is_an_ordinary_field_not_a_set():
    plan = plan_tag_import([_incoming(parent="love-live")], [_current(parent="gakumas")])
    (tag,) = plan.tags
    (conflict,) = tag.conflicts
    assert (conflict.field, conflict.current, conflict.incoming) == (
        "parent", "gakumas", "love-live",
    )
    assert tag.member_additions == [] and tag.member_removals == []


def test_a_catalogue_tag_absent_from_the_file_is_untouched_and_unmentioned():
    """Nothing is ever deleted. A tag the file does not mention does not appear
    in the plan at all."""
    plan = plan_tag_import([_incoming("kozue")], [_current("kozue"), _current("kaho")])
    assert [t.handle for t in plan.tags] == ["kozue"]


def test_incoming_warnings_are_carried_through():
    plan = plan_tag_import([_incoming()], [], warnings=["parser said something"])
    assert "parser said something" in plan.warnings
