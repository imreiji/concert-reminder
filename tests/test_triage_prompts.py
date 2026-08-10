"""Tests for domain/triage_prompts.py -- the pure prompt/response module of
AI-assisted lead triage (phase 1). No I/O; consumes app.domain.prune_list
and app.domain.yaml_import the same way the runner (a later task) will."""

import pytest
import yaml

from app.domain.prune_list import parse_prune_list
from app.domain.triage_prompts import (
    PAGE_CHAR_CAP,
    LeadLine,
    Survivor,
    TriageResponseError,
    classify_prompt,
    draft_prompt,
    merge_classify_results,
    parse_classify_response,
    strip_rounds,
)
from app.domain.types import DismissReason


def _lead(id="486243", title="学マス LIVE", date="2026-09-12", venue="Zepp Haneda",
          deadline=False, source="eventernote"):
    return LeadLine(source_event_id=id, title=title, date_iso=date, venue=venue,
                     date_is_deadline=deadline, source=source)


def test_classify_prompt_names_every_dismiss_reason_except_talk():
    system, user = classify_prompt([_lead()])
    for reason in DismissReason:
        if reason.name == "TALK":
            assert reason.value not in system
        else:
            assert reason.value in system
    assert "486243" in user and "学マス LIVE" in user


def test_classify_prompt_flags_deadline_dates():
    _, user = classify_prompt([_lead(deadline=True)])
    assert "申込締切" in user


def test_parse_classify_response_round_trips_the_prune_yaml():
    text = (
        "```yaml\n"
        "dismiss:\n  stage: ['481833']\n"
        "survivors:\n  - title: \"学マス LIVE\"\n    lead_ids: ['486243']\n"
        "    representative: '486243'\n"
        "```"
    )
    result = parse_classify_response(text)
    parsed = parse_prune_list(result.prune_yaml)   # must not raise
    assert parsed.entries[0].event_id == "481833"
    assert result.survivors[0].representative == "486243"


def test_no_dismissals_is_absence_not_error():
    result = parse_classify_response("survivors: []\n")
    assert result.prune_yaml == ""
    assert result.survivors == ()


@pytest.mark.parametrize("body", ["dismiss:\n  stage: []\n", "dismiss:\n  stage: null\n"])
def test_an_empty_dismiss_list_is_absence_not_failure(body):
    """A model that names a reason and then lists nothing under it has proposed
    NO dismissal -- absence, not an error. Before this, `{stage: []}` was a
    truthy dict, so it reached parse_prune_list, raised PruneListError, and took
    the whole response (survivors included) down with it."""
    result = parse_classify_response(
        body + "survivors:\n  - title: t\n    lead_ids: ['1']\n    representative: '1'\n"
    )
    assert result.prune_yaml == ""
    assert [s.title for s in result.survivors] == ["t"]


@pytest.mark.parametrize("value", ["[]", "''", "0", "false"])
def test_a_falsy_non_mapping_dismiss_is_absence_too(value):
    """`dismiss: []` is a model saying "nothing to dismiss" in the wrong shape,
    and the bare `if dismiss:` this module started with read every falsy value
    that way. Only a TRUTHY non-mapping is a real disagreement about the format
    and stays fatal."""
    result = parse_classify_response(f"dismiss: {value}\nsurvivors: []\n")
    assert result.prune_yaml == ""
    assert result.survivors == ()


def test_a_truthy_non_mapping_dismiss_is_still_fatal():
    with pytest.raises(TriageResponseError):
        parse_classify_response("dismiss: ['481833']\nsurvivors: []\n")


def test_prose_around_the_fence_is_tolerated():
    """Models preface a fenced block with a sentence however firmly they are
    told not to. The fence is the payload; the chat around it is not an error."""
    result = parse_classify_response(
        "Here is the result:\n```yaml\nsurvivors: []\n```"
    )
    assert result.survivors == ()
    assert result.prune_yaml == ""


def test_malformed_survivor_is_a_warning_not_a_failure():
    result = parse_classify_response(
        "dismiss:\n  live: ['1']\nsurvivors:\n  - lead_ids: 'not-a-list'\n"
    )
    assert result.survivors == ()
    assert result.warnings


def test_unusable_response_raises():
    with pytest.raises(TriageResponseError):
        parse_classify_response("I'm sorry, I can't help with that.")


def test_strip_rounds_removes_what_the_model_invented():
    text = (
        "title: ライブ\ntitle_en: Live\ntitle_zh: 演唱会\n"
        "rounds:\n  - label: 最速先行\n    apply_closes_jst: 2026-09-15 23:59\n"
    )
    stripped = strip_rounds(text)
    assert "最速先行" not in stripped
    assert "apply_closes_jst" not in stripped
    data = yaml.safe_load(stripped)
    assert data["rounds"] == []
    assert data["title_zh"] == "演唱会"   # unicode survives the round-trip


def test_draft_prompt_caps_page_size_and_forbids_rounds():
    system, user = draft_prompt(
        Survivor(title="t", lead_ids=("1",), representative="1"),
        [_lead()], page_html="x" * (PAGE_CHAR_CAP + 50_000),
    )
    assert len(user) < PAGE_CHAR_CAP + 10_000
    assert "rounds: []" in system


def test_draft_prompt_labels_the_payload_as_an_eventernote_page():
    # run_triage fetches EVENT_URL = eventernote.com/events/{id} for this
    # pass -- never an official ticket page -- so the user message must not
    # call the payload something it isn't.
    _system, user = draft_prompt(
        Survivor(title="t", lead_ids=("1",), representative="1"),
        [_lead()], page_html="<html></html>",
    )
    assert "Eventernote page HTML" in user
    assert "Official ticket page" not in user


def test_draft_system_prompt_does_not_claim_eventernote_has_no_ticket_data():
    # Eventernote event pages routinely carry the full ticket ladder in their
    # free-text description -- the system prompt must not tell the model
    # otherwise, only that this pass still doesn't act on it.
    system, _user = draft_prompt(
        Survivor(title="t", lead_ids=("1",), representative="1"),
        [_lead()], page_html="<html></html>",
    )
    assert "no lottery data" not in system
    assert "NEVER round/ticket information" not in system
    # The rule stays, now with the reason spelled out: extraction is the
    # completion pass's job, which grounds and verifies each timestamp.
    assert "COMPLETION" in system
    assert "grounds every timestamp in a verbatim quote" in system


# -- Merging the per-batch classify results --------------------------------
#
# The classify pass is sliced into batches of TRIAGE_CLASSIFY_BATCH leads
# (app/triage.py), so the runner gets one ClassifyResult per batch and needs
# exactly one back. The merge is here, not there, for the reason every other
# piece of response handling is here: the runner is the run ORDER only.


def test_merge_concatenates_survivors_warnings_and_dismissals():
    first = parse_classify_response(
        "dismiss:\n  stage: ['1']\n"
        "survivors:\n  - title: a\n    lead_ids: ['2']\n    representative: '2'\n"
    )
    second = parse_classify_response(
        "dismiss:\n  stage: ['3']\n  release: ['4']\n"
        "survivors:\n  - title: b\n    lead_ids: ['5']\n    representative: null\n"
        "  - lead_ids: 'not-a-list'\n"          # a warning, not a failure
    )
    merged = merge_classify_results([first, second])

    assert [s.title for s in merged.survivors] == ["a", "b"]
    assert merged.warnings == second.warnings
    # THE property the runner leans on: the merged text is still a prune list.
    parsed = parse_prune_list(merged.prune_yaml)
    assert {e.event_id: e.reason.value for e in parsed.entries} == {
        "1": "stage", "3": "stage", "4": "release",
    }


def test_merge_folds_one_reason_split_across_batches_into_one_key():
    """Two batches dismissing for the same reason must produce ONE key with
    both ids -- a repeated `stage:` key is exactly what parse_prune_list's
    _UniqueKeyLoader refuses, so a naive text concatenation would round-trip
    into a PruneListError and take the whole press down."""
    results = [
        parse_classify_response(f"dismiss:\n  stage: ['{i}']\nsurvivors: []\n")
        for i in range(1, 4)
    ]
    merged = merge_classify_results(results)
    assert yaml.safe_load(merged.prune_yaml) == {"dismiss": {"stage": ["1", "2", "3"]}}
    assert len(parse_prune_list(merged.prune_yaml).entries) == 3


def test_merge_keeps_the_first_reason_when_two_batches_name_one_id():
    """It cannot happen honestly -- a lead sits in exactly ONE batch, so no two
    batches are ever shown the same id -- which means a repeat is an id the
    model made up. parse_prune_list treats one id under two reasons as fatal
    for the WHOLE list, so the merge resolves it here (first wins) and warns,
    rather than letting one hallucinated id cost every real dismissal."""
    first = parse_classify_response("dismiss:\n  stage: ['9']\nsurvivors: []\n")
    second = parse_classify_response("dismiss:\n  release: ['9']\nsurvivors: []\n")
    merged = merge_classify_results([first, second])

    parsed = parse_prune_list(merged.prune_yaml)   # must not raise
    assert [(e.event_id, e.reason.value) for e in parsed.entries] == [("9", "stage")]
    assert any("9" in w for w in merged.warnings)


def test_merge_of_results_with_nothing_dismissed_is_the_empty_string():
    merged = merge_classify_results(
        [parse_classify_response("survivors: []\n") for _ in range(3)]
    )
    assert merged.prune_yaml == ""      # "" is absence, as everywhere else
    assert merged.survivors == ()


def test_merging_one_result_returns_it_unchanged():
    only = parse_classify_response(
        "dismiss:\n  stage: ['1']\nsurvivors:\n  - title: a\n"
        "    lead_ids: ['2']\n    representative: '2'\n"
    )
    merged = merge_classify_results([only])
    assert merged.survivors == only.survivors
    assert parse_prune_list(merged.prune_yaml).entries == \
        parse_prune_list(only.prune_yaml).entries
