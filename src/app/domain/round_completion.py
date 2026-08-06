"""The completion prompt, its reply, and the surgical merge back into a draft.

Pure, like `triage_prompts.py`, and split from it on the same principle
`tags_yaml`/`tags_diff` are split: that module is about TRIAGING a queue, this
one is about COMPLETING one document. It reuses `extract_yaml` from there
rather than growing a third fence-stripper -- models preface a fenced block
with a sentence however firmly a prompt forbids it, and one place should know
that.

The model is asked for ONE key, `rounds:`, and nothing else. Not a whole draft:
the rest of the document has already been produced (phase 1) and possibly
proofread, and re-emitting it would churn text a human already read for no
gain. That is also what makes `merge_rounds` a one-key rewrite rather than a
document replacement.

EVIDENCE IS LIFTED OUT AT PARSE TIME. The model writes it inside each round,
because that is the only place it can write it; this module removes it before
the round mapping goes anywhere near a draft. A draft is a document that gets
exported, re-pasted and committed into `concerts`, and scaffolding that rides
along becomes an unknown key somebody's parser warns about two features later.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import yaml

from app.domain.page_text import PAGE_TEXT_CAP
from app.domain.round_evidence import TIMESTAMP_FIELDS, ProposedRound
from app.domain.triage_prompts import extract_yaml


class CompletionResponseError(Exception):
    """The reply can't be used at all -- not YAML, or not a mapping. Anything
    short of that degrades to a warning on one round, the same
    warnings-over-failures split every parser in `domain/` makes."""


# `kind` values are the exact `RoundKind` (app.domain.types) enum values, not
# a shorthand of them -- `yaml_import._round_kind` compares this field against
# those literal strings and silently defaults anything else to `other` with a
# warning, so a mismatched list here would be a silent quality loss on every
# completion, not a caught error.
_COMPLETION_SYSTEM_PROMPT = """\
You are filling in the ticket rounds for ONE dekimasen.app concert draft, in
the vocabulary `.claude/skills/add-concert/SKILL.md` uses. You are given the
draft (which already has its title, its legs and its cast) and the text of that
production's official ticket page.

Output exactly ONE YAML document holding ONE key, `rounds`, and no prose before
or after it:

```yaml
rounds:
  - label: 1次先行抽選
    label_en: 1st advance lottery
    label_zh: 首轮先行抽选
    kind: lottery_round
    applies_to: [Day 1]
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    results_jst: 2026-01-15 18:00
    payment_deadline_jst: 2026-01-20 23:59
    url: https://example.com/ticket
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
      results_jst: "当落発表 2026年1月15日(木)18:00"
      payment_deadline_jst: "入金期限 2026年1月20日(火)23:59"
```

Rules, and the first one is the only one that matters:

- EVERY timestamp you write MUST have an `evidence` entry quoting the text on
  the page you read it from, copied VERBATIM from that page. A round whose
  evidence cannot be found on the page is thrown away by the application, so an
  invented quote does not get you a round -- it loses you one. If the page does
  not state a time, leave that field out. If it states none, return
  `rounds: []`.
- NEVER infer, estimate or complete a timestamp the page does not state. A
  fabricated deadline reaches a real person as a real reminder for something
  that was never real, which is the worst thing this system can do.
- Times are JST, written `YYYY-MM-DD HH:MM`. A page writing 2026年1月10日(土)
  23:59 means `2026-01-10 23:59`. A deadline written 23:59 on the last day
  stays 23:59 -- do not round it.
- `applies_to` is a list of leg labels copied EXACTLY from the draft's
  `performances`. Omit it (or leave it empty) when the round covers the whole
  event; that is the common case for a tour-wide lottery.
- Japanese is canonical. Fill `label`/`label_en`/`label_zh` as all three, or
  leave the two variants out entirely -- never a partial set.
- One round per campaign rung. 1次先行, 2次先行 and 一般発売 are three rounds,
  not one; a single round's own 受付開始 and 申込締切 are two fields of ONE
  round, not two rounds.
- `kind` is one of: lottery_round, fcfs_sale, general_sale, stream_ticket_sale,
  eligibility_item_sale, goods_sale, tour_package, upgrade, other. Use `other`
  when unsure rather than guessing a mechanic.
"""


def completion_prompt(draft_text: str, page_text: str) -> tuple[str, str]:
    """The system+user pair for one draft's completion."""
    user = (
        "The draft as it stands:\n"
        f"{draft_text}\n\n"
        f"The official ticket page, as text (may be truncated at {PAGE_TEXT_CAP} "
        f"characters):\n{page_text[:PAGE_TEXT_CAP]}"
    )
    return _COMPLETION_SYSTEM_PROMPT, user


def _as_stamp_text(value) -> str:
    """A timestamp field as the draft vocabulary's TEXT.

    PyYAML resolves `2026-01-10 23:59:00` to a `datetime` and a bare date to a
    `date`. `yaml_import._dt` parses these fields from text, so leaving a
    datetime object in the mapping would dump back a shape the draft parser
    does not read -- and the failure would land two tasks away, at paste time.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def parse_completion_response(text: str) -> tuple[list[ProposedRound], list[str]]:
    """Rounds (evidence held apart) and per-round warnings.

    Raises `CompletionResponseError` only when the reply as a WHOLE is
    unusable; a single malformed round is skipped and named instead.
    """
    yaml_text = extract_yaml(text)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CompletionResponseError(f"that doesn't parse as YAML: {exc}") from exc
    if data is None:
        return [], []
    if not isinstance(data, dict):
        raise CompletionResponseError(
            "expected a YAML mapping with a 'rounds' key -- got something else"
        )

    raw_rounds = data.get("rounds")
    if raw_rounds is None:
        return [], []
    if not isinstance(raw_rounds, list):
        return [], [f"rounds: expected a list, got {type(raw_rounds).__name__} -- ignored"]

    rounds: list[ProposedRound] = []
    warnings: list[str] = []
    for i, raw in enumerate(raw_rounds, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"round {i}: expected a mapping -- skipped")
            continue
        payload = dict(raw)
        raw_evidence = payload.pop("evidence", None)
        evidence: dict[str, str] = {}
        if isinstance(raw_evidence, dict):
            evidence = {str(k): str(v) for k, v in raw_evidence.items()}
        elif raw_evidence is not None:
            warnings.append(f"round {i}: evidence was not a mapping -- treated as absent")
        for name in TIMESTAMP_FIELDS:
            if name in payload:
                payload[name] = _as_stamp_text(payload[name])
        label = str(payload.get("label") or "").strip()
        if not label:
            warnings.append(f"round {i}: no label -- skipped")
            continue
        payload["label"] = label
        rounds.append(ProposedRound(data=payload, evidence=evidence, label=label))
    return rounds, warnings


def _split_comment_prefix(text: str) -> tuple[str, str]:
    """The leading '#' comment lines, and the rest.

    Load-bearing, not cosmetic: a phase-1 draft's first line is
    `# source: https://www.eventernote.com/events/N`, and the triage runner's
    duplicate containment matches that WHOLE LINE inside stored draft text. A
    naive safe_load/safe_dump round-trip drops every comment, which would make
    the next triage press re-draft a production the operator already has --
    silently, and only visible as duplicates in the pending list.
    """
    lines = text.splitlines(keepends=True)
    cut = 0
    for line in lines:
        if line.lstrip().startswith("#") or not line.strip():
            cut += 1
        else:
            break
    return "".join(lines[:cut]), "".join(lines[cut:])


def merge_rounds(draft_text: str, rounds: Sequence[dict]) -> str:
    """Rewrite ONLY the `rounds:` key of a stored draft.

    Known and accepted: comments INSIDE the body are lost to the round-trip.
    Phase-1 drafts have none (they are safe_dump output) and an agent draft's
    inline comments are not load-bearing; a comment-preserving YAML library
    would be a new dependency protecting data nothing reads. The leading
    prefix, which IS load-bearing, is preserved above.
    """
    prefix, body = _split_comment_prefix(draft_text)
    data = yaml.safe_load(body)
    if not isinstance(data, dict):
        data = {}
    data["rounds"] = [dict(r) for r in rounds]
    return prefix + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def draft_leg_labels(draft_text: str) -> list[str]:
    """The `performances` labels an `applies_to` may name. Never raises: an
    unreadable draft simply has no legs to bind to, and the caller's verify
    step will reject any applies_to rather than crash the run."""
    try:
        data = yaml.safe_load(_split_comment_prefix(draft_text)[1])
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    labels = []
    for day in data.get("performances") or []:
        if isinstance(day, dict):
            label = str(day.get("label") or "").strip()
            if label:
                labels.append(label)
    return labels
