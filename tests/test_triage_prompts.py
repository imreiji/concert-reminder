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
