"""The slug primitive shared by concert event_ids and tag handles."""

from app.domain.slugs import slug_core, tag_slug_base


def test_slug_core_has_no_fallback():
    """The whole reason this exists: slugify() returns "concert" for input with
    no ASCII, which a tag cannot use -- it is indistinguishable from a tag
    really named "Concert"."""
    assert slug_core("Hasunosora 5th Live!") == "hasunosora-5th-live"
    assert slug_core("  Multiple   Spaces  ") == "multiple-spaces"
    assert slug_core("日本語タイトル") == ""
    assert slug_core("") == ""
    assert slug_core("---") == ""


def test_tag_slug_base_prefers_english():
    """name_en is mandatory at every tag create boundary, so it is reliably
    there for new tags; `name` is the fallback for rows predating that rule."""
    assert tag_slug_base("蓮ノ空", "Hasunosora") == "hasunosora"
    assert tag_slug_base("Zepp Haneda", None) == "zepp-haneda"
    assert tag_slug_base("Zepp Haneda", "") == "zepp-haneda"
    assert tag_slug_base("Zepp Haneda", "   ") == "zepp-haneda"


def test_tag_slug_base_empty_when_nothing_is_ascii():
    """Caller supplies the {kind}-{id} fallback, which needs a flushed row --
    so this returns "" rather than inventing something."""
    assert tag_slug_base("蓮ノ空", None) == ""
    assert tag_slug_base("蓮ノ空", "スクールアイドル") == ""
