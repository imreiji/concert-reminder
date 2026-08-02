import pytest

from app.domain.prune_list import PruneListError, parse_prune_list
from app.domain.types import DismissReason


def test_a_list_parses_into_entries():
    got = parse_prune_list("""
dismiss:
  stage:
    - 481833
    - 481832
  release:
    - 466181
""")
    assert [(e.event_id, e.reason) for e in got.entries] == [
        ("481833", DismissReason.STAGE),
        ("481832", DismissReason.STAGE),
        ("466181", DismissReason.RELEASE),
    ]


def test_ids_are_strings_even_when_yaml_reads_them_as_ints():
    """`- 481833` is an int to YAML, and eventernote_event_id is a String
    column. Comparing int to str silently matches nothing, which would look
    like a stale file rather than a bug."""
    got = parse_prune_list("dismiss:\n  free:\n    - 481300\n")
    assert got.entries[0].event_id == "481300"
    assert isinstance(got.entries[0].event_id, str)


def test_an_unknown_reason_is_an_error_naming_the_key():
    """Not a warning. This file's whole purpose is to write a column whose
    value is that every row in it is a real judgment, so an unrecognised class
    must not become a silent skip."""
    with pytest.raises(PruneListError) as e:
        parse_prune_list("dismiss:\n  nonsense:\n    - 1\n")
    assert "nonsense" in str(e.value)


def test_the_same_id_under_two_reasons_is_refused():
    """Refused outright rather than resolved by ordering: last-one-wins would
    make the result depend on dict iteration order."""
    with pytest.raises(PruneListError) as e:
        parse_prune_list("dismiss:\n  stage:\n    - 42\n  release:\n    - 42\n")
    assert "42" in str(e.value)


def test_an_empty_or_missing_dismiss_block_is_an_error_not_a_no_op():
    """A file that parses to zero dismissals is almost always a mistake --
    wrong key, bad indentation -- and applying it cheerfully would report
    success for nothing."""
    for text in ("", "dismiss:\n", "something_else:\n  stage:\n    - 1\n"):
        with pytest.raises(PruneListError):
            parse_prune_list(text)


def test_a_non_list_under_a_reason_is_an_error():
    with pytest.raises(PruneListError):
        parse_prune_list("dismiss:\n  stage: 481833\n")


def test_yaml_that_is_not_a_mapping_is_an_error_not_a_crash():
    for text in ("- just\n- a\n- list\n", "plain string\n", "[1, 2]\n"):
        with pytest.raises(PruneListError):
            parse_prune_list(text)


def test_duplicate_id_under_the_SAME_reason_is_deduped_with_a_warning():
    """Harmless -- the second dismiss is a no-op anyway -- so warn rather than
    refuse, following the draft parser's warnings-over-failures philosophy."""
    got = parse_prune_list("dismiss:\n  stage:\n    - 7\n    - 7\n")
    assert len(got.entries) == 1
    assert any("7" in w for w in got.warnings)


def test_a_block_of_all_empty_lists_is_an_error_not_a_no_op():
    """The empty-block check must assert on the FINAL entry count, not on
    whether the `dismiss:` mapping itself is falsy -- a block whose reason
    keys each map to an empty list is truthy (a non-empty dict) but still
    parses to zero dismissals, which the module's own docstring says is
    almost always a mistake."""
    with pytest.raises(PruneListError):
        parse_prune_list("dismiss:\n  stage: []\n  release: []\n")


@pytest.mark.parametrize("item", ["null", "{a: 1}", "[1, 2]", "true", "false"])
def test_non_scalar_or_boolean_ids_are_rejected(item):
    """A null/mapping/list/bool id would otherwise become a garbage string
    (str(None) -> "None", str(True) -> "True") that is accepted as real and
    matches no eventernote_event_id -- looking exactly like a stale file
    rather than a malformed one. bool is a subclass of int in Python, so
    `true`/`false` must be excluded deliberately rather than slipping
    through an isinstance(x, int) check."""
    with pytest.raises(PruneListError):
        parse_prune_list(f"dismiss:\n  stage:\n    - {item}\n")


def test_a_repeated_reason_key_is_an_error_not_a_silent_drop():
    """yaml.safe_load resolves a repeated mapping key to its LAST occurrence
    only -- the first list would vanish with no error and no warning, which
    for a file whose every entry becomes a permanent dismissal is the worst
    available outcome."""
    with pytest.raises(PruneListError):
        parse_prune_list("dismiss:\n  stage:\n    - 1\n  stage:\n    - 2\n")


def test_a_repeated_top_level_key_is_an_error_not_a_silent_drop():
    with pytest.raises(PruneListError):
        parse_prune_list("dismiss:\n  stage:\n    - 1\ndismiss:\n  release:\n    - 2\n")
