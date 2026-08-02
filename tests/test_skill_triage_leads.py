"""The triage-leads skill's examples must parse against the REAL parsers.

A skill that emits a format nothing reads is a proposal, not a workflow, and
the drift is silent: the skill keeps producing files that stopped importing.
Same guarantee test_skill_example_draft_parses_clean gives add-concert.
"""
import pathlib

from app.domain.prune_list import parse_prune_list
from app.domain.types import DismissReason
from app.domain.yaml_import import parse_drafts

SKILL = pathlib.Path(".claude/skills/triage-leads/references")


def test_example_prune_list_parses_clean():
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert got.entries, "the example must actually name some leads"
    assert got.warnings == (), "a warning means the example teaches a bad habit"


def test_example_prune_list_shows_more_than_one_reason():
    """An example with a single reason would let an agent infer the file takes
    one class at a time, which is exactly wrong -- the whole point is one file
    covering the whole backlog."""
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert len({e.reason for e in got.entries}) >= 3


def test_example_prune_list_never_dismisses_the_always_catalogued_class():
    """The scope ruling catalogues class D (radio/talk/番組イベント)
    unconditionally -- unlike `live` (a real, trackable concert someone still
    chooses to skip, which the ruling's own table lists as a legitimate
    per-lead dismissal), `talk` can never legitimately appear in a prune
    file. An example that dismissed a talk show would teach the opposite of
    the ruling. This checks only `talk`, not "every non-dismissible class",
    because `talk` is the one DismissReason value the ruling makes
    universally wrong -- there is no such blanket rule for the others."""
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert DismissReason.TALK not in {e.reason for e in got.entries}


def test_example_batch_parses_into_several_drafts():
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    assert len(batch.drafts) >= 2, "a batch example with one draft teaches nothing"
    assert batch.errors == ()


def test_example_batch_drafts_are_each_complete():
    """Each document must stand alone -- it is stored verbatim and re-parsed
    later, so a draft that only makes sense in context would break on review.

    Pins leg/round label trilinguality too, not just the title's: the
    all-three-languages-or-none variant rule is enforced at the real
    import_commit boundary (a 422), so a label missing its EN/ZH half here
    would parse clean today and only fail there -- exactly the drift a
    pinning test exists to catch before it does."""
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    for d in batch.drafts:
        assert d.parsed.title and d.parsed.title_en and d.parsed.title_zh
        assert d.parsed.days and d.parsed.rounds
        for day in d.parsed.days:
            assert day.label and day.label_en and day.label_zh
        for round_ in d.parsed.rounds:
            assert round_.label and round_.label_en and round_.label_zh


def test_example_batch_shows_a_multi_leg_concert():
    """The collapse rule is the skill's hardest judgment: a tour is ONE concert
    with several legs. An example of only single-leg concerts would not teach
    it."""
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    assert any(len(d.parsed.days) >= 2 for d in batch.drafts)
