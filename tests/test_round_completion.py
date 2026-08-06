"""The completion prompt's reply, and the surgical merge back into a draft."""

import pytest
import yaml

from app.domain.round_completion import (
    CompletionResponseError,
    DraftMergeError,
    completion_prompt,
    draft_leg_labels,
    merge_rounds,
    parse_completion_response,
)

SKELETON = """\
# source: https://www.eventernote.com/events/486243
title: 例）ライブ
title_en: Example live
kind: tour
performances:
- label: Day 1
  label_en: Day 1
  venue: Zepp Haneda
- label: Day 2
  label_en: Day 2
  venue: Zepp Namba
rounds: []
"""

REPLY = """\
```yaml
rounds:
  - label: 1次先行抽選
    kind: lottery
    applies_to: [Day 1]
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
```
"""


def test_the_prompt_carries_the_draft_and_the_page():
    system, user = completion_prompt(SKELETON, "チケット情報 申込締切")
    assert "rounds" in system
    assert "Day 1" in user and "チケット情報 申込締切" in user


def test_the_prompt_forbids_inventing_a_deadline():
    system, _user = completion_prompt(SKELETON, "x")
    assert "evidence" in system
    assert "NEVER" in system


def test_a_fenced_reply_parses_into_rounds_with_evidence_held_apart():
    rounds, warnings = parse_completion_response(REPLY)
    assert not warnings
    assert len(rounds) == 1
    assert rounds[0].label == "1次先行抽選"
    assert rounds[0].evidence["apply_closes_jst"].startswith("申込締切")
    # Evidence is lifted OUT of the data: it must never reach the draft.
    assert "evidence" not in rounds[0].data
    assert rounds[0].data["apply_closes_jst"] == "2026-01-10 23:59"


def test_a_reply_that_is_not_a_mapping_is_unusable():
    with pytest.raises(CompletionResponseError):
        parse_completion_response("- just\n- a list\n")


def test_an_empty_rounds_list_is_an_answer_not_an_error():
    rounds, warnings = parse_completion_response("rounds: []\n")
    assert rounds == [] and not warnings


def test_a_malformed_single_round_is_skipped_and_named():
    rounds, warnings = parse_completion_response("rounds:\n  - 'not a mapping'\n")
    assert rounds == []
    assert warnings and "round 1" in warnings[0]


def test_a_timestamp_yaml_resolved_to_a_datetime_comes_back_as_text():
    # PyYAML resolves `2026-01-10 23:59:00` to a datetime object. The draft
    # vocabulary is text, and yaml_import's own _dt parses it from text, so a
    # datetime here would be dumped back in a shape parse_draft does not read.
    rounds, _w = parse_completion_response(
        "rounds:\n  - label: x\n    apply_closes_jst: 2026-01-10 23:59:00\n"
        "    evidence: {apply_closes_jst: q}\n"
    )
    assert rounds[0].data["apply_closes_jst"] == "2026-01-10 23:59"


def test_merge_replaces_only_the_rounds_key():
    merged = merge_rounds(SKELETON, [{"label": "1次先行抽選", "kind": "lottery"}])
    data = yaml.safe_load(merged)
    assert data["title"] == "例）ライブ"
    assert data["kind"] == "tour"
    assert [d["label"] for d in data["performances"]] == ["Day 1", "Day 2"]
    assert data["rounds"][0]["label"] == "1次先行抽選"


def test_merge_keeps_the_source_comment_that_containment_reads():
    # phase 1's duplicate containment matches the WHOLE '# source: ...' line
    # inside a stored draft. A round-trip that drops it would silently make the
    # next triage press re-draft this production.
    merged = merge_rounds(SKELETON, [{"label": "x"}])
    assert merged.startswith("# source: https://www.eventernote.com/events/486243\n")


def test_merge_survives_a_draft_with_no_comment_prefix():
    merged = merge_rounds("title: x\nrounds: []\n", [{"label": "y"}])
    assert yaml.safe_load(merged)["rounds"][0]["label"] == "y"


def test_leg_labels_come_off_the_draft():
    assert draft_leg_labels(SKELETON) == ["Day 1", "Day 2"]


def test_merge_refuses_a_non_mapping_body_rather_than_wiping_it():
    # A body that parses to a YAML list (not a mapping) must not be silently
    # replaced with `{rounds: [...]}` -- that would discard the title, legs
    # and cast the stored draft already had, with no warning at all.
    original = "# source: x\n- just\n- a list\n"
    with pytest.raises(DraftMergeError):
        merge_rounds(original, [{"label": "y"}])
    # The property that matters: the function raised before producing
    # anything, so nothing overwrote the stored draft.
    assert original == "# source: x\n- just\n- a list\n"


def test_merge_refuses_a_malformed_body_rather_than_wiping_it():
    original = "# source: x\ntitle: [unterminated\n"
    with pytest.raises(DraftMergeError):
        merge_rounds(original, [{"label": "y"}])
    assert original == "# source: x\ntitle: [unterminated\n"


def test_a_timestamp_with_no_value_is_dropped_and_warned():
    # `apply_closes_jst:` with nothing after the colon is the model
    # half-answering, not declining -- it must not travel through as the
    # literal string "None".
    rounds, warnings = parse_completion_response(
        "rounds:\n  - label: x\n    apply_closes_jst:\n    evidence: {}\n"
    )
    assert "apply_closes_jst" not in rounds[0].data
    assert warnings and "apply_closes_jst" in warnings[0]
