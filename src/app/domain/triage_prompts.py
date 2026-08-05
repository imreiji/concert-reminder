"""Prompts and response handling for AI-assisted discovery-lead triage
(phase 1). The ONLY place a triage prompt is composed or a model's reply is
parsed -- a later runner task calls these functions with LLM response text
and never builds its own prompt or its own YAML handling.

Pure module: no I/O, no sqlalchemy/fastapi/discord/httpx imports -- only
stdlib, yaml, and other domain modules, same rule as every other file in
domain/. It reuses the parsers everything else in this app already trusts
rather than growing a third YAML dialect: `app.domain.prune_list` for the
dismiss half (`parse_prune_list`/`PruneListError`) and, downstream of this
module, `app.domain.yaml_import.parse_drafts` for a draft response -- this
module never calls `parse_drafts` itself, since a draft response is handed
back as raw YAML text (`extract_yaml`) for the SAME preview/commit path a
human-authored paste already uses (`POST /concerts/import/draft`).

Two prompts, two response parsers:

- `classify_prompt`/`parse_classify_response` -- pass 1+2 of
  `.claude/skills/triage-leads/SKILL.md` (collapse by title stem, then
  classify keep-vs-dismiss), done by a model instead of an agent reading a
  paste by hand. The response is one YAML document with `dismiss:` (a
  `DismissReason` value -> list of raw lead ids, exactly the prune-list
  shape) and `survivors:` (one entry per production that should proceed to
  drafting). `dismiss` is re-dumped and run through `parse_prune_list` so a
  response this module would accept is ALSO one the existing prune-list
  importer accepts -- a `PruneListError` here means the whole response is
  unusable (`TriageResponseError`), because storing junk YAML for the owner
  to paste later would just move the failure to the plan screen. A
  malformed individual survivor (no title, non-list `lead_ids`) is skipped
  and counted into `warnings` instead -- warnings over failures, like every
  parser in this package -- but the response as a WHOLE is only unusable
  when it isn't a YAML mapping at all.
- `draft_prompt`/`extract_yaml`/`strip_rounds` -- pass 3, one survivor at a
  time, in the exact vocabulary `.claude/skills/add-concert/SKILL.md` and
  its pinned `references/example-draft.yaml` use (trilingual title, per-leg
  date/venue/label, series/artist NAME lists, never ids). `strip_rounds` is
  a belt-and-braces net for the one rule both skills repeat under threat of
  a real user getting a fake reminder: a model asked for `rounds: []` can
  still invent one anyway, so the runner is expected to call `strip_rounds`
  on every draft response regardless of what the model actually said,
  discarding any round the model proposed rather than trusting its
  self-restraint. `PAGE_CHAR_CAP` caps how much of a fetched ticket page
  rides in the user prompt, so one enormous page cannot blow an LLM
  context budget silently.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from app.domain.prune_list import PruneListError, parse_prune_list
from app.domain.types import DismissReason

PAGE_CHAR_CAP = 120_000


class TriageResponseError(Exception):
    """The model's reply can't be used at all -- not YAML, not a mapping, or
    its `dismiss` subtree doesn't round-trip through `parse_prune_list`.
    Everything short of that degrades to a warning on the result instead,
    the same warnings-over-failures split every other parser in `domain/`
    makes."""


@dataclass(frozen=True)
class LeadLine:
    source_event_id: str
    title: str
    date_iso: str
    venue: str
    date_is_deadline: bool
    source: str


@dataclass(frozen=True)
class Survivor:
    title: str
    lead_ids: tuple[str, ...]
    # A bare numeric Eventernote event id (the leg the draft pass should
    # fetch first), or None when every lead behind this production is
    # calendar-sourced and there is no Eventernote page to hand over.
    representative: str | None


@dataclass(frozen=True)
class ClassifyResult:
    # "" -- not None -- when the model proposed no dismissals at all: that
    # is an absence, not an error, and an empty string is what the prune-list
    # paste form already treats as "nothing to submit".
    prune_yaml: str
    survivors: tuple[Survivor, ...]
    warnings: tuple[str, ...]


# -- Classify prompt (pass 1 + pass 2 of triage-leads/SKILL.md) ------------

# Every DismissReason except TALK, in enum definition order -- built off the
# enum itself rather than a hardcoded list of strings so a reason added to
# (or renamed in) app.domain.types can never drift out of sync with this
# prompt. TALK is excluded because the 2026-08-02 scope ruling keeps that
# whole class in the queue unconditionally; it is never a dismissal reason.
_REASON_DESCRIPTIONS: dict[DismissReason, str] = {
    DismissReason.LIVE: "a real concert or tour, just not one worth tracking",
    DismissReason.STAGE: "朗読劇 / ミュージカル / 舞台 / リーディング",
    DismissReason.RELEASE: "発売記念 / お渡し会 / 特典会 / 写真集",
    DismissReason.FESTIVAL: "a multi-artist bill -- the concert is the festival, not this artist",
    DismissReason.FANMEET: "ファンミーティング / バースデーイベント with no lottery to track",
    DismissReason.FREE: "no ticket at all -- 餅まき, 盆踊り, 駅長就任式",
    DismissReason.OTHER: "doesn't fit any class above",
}


def _dismiss_reason_lines() -> str:
    return "\n".join(
        f"  {reason.value}: {_REASON_DESCRIPTIONS[reason]}"
        for reason in DismissReason
        if reason.name != "TALK"
    )


_CLASSIFY_SYSTEM_PROMPT = f"""\
You are triaging a batch of dekimasen.app "discovery leads" -- events an
automated sweep found that the catalogue may not be tracking yet. A lead is
not a concert: it says only "this exists and you are not tracking it".
Each lead line names an id (an Eventernote event id, or a namespaced
"<feed key>:<UID>" for a calendar-feed lead), a date, a venue, and where the
sweep found it.

## Step 1 -- collapse by title stem

Several different mechanisms produce a repeated title across leads, and they
want OPPOSITE treatment:

1. A tour or a multi-day run at different times/venues (e.g. a LIVE TOUR
   playing four cities) -- becomes ONE concert with ONE LEG per date/venue.
2. A per-member or per-part split at ONE venue on ONE day (e.g. a
   meet-and-greet sold once per member) -- becomes ONE production, NEVER one
   leg per member. The signature: the times overlap on the same day at the
   same venue.
3. Successive rounds of one campaign surfaced by a ticket calendar (a run of
   申込締切-prefixed lines sharing a title stem, days or weeks apart) --
   becomes ONE production; those lines are its ROUNDS, not its legs, and
   their dates must never be read as leg/show dates.

The test: do the repeated leads differ by WHEN/WHERE the audience goes, or
only by WHICH member's individual slot it is? The first is legs of a tour;
the second is one event Eventernote happened to list once per performer.

## Step 2 -- classify each production

Keep (no dismiss reason):
- a ticketed concert or tour
- a radio program, variety event, or 番組イベント with no ticket to track --
  this class is NEVER dismissed, whatever else it resembles

Dismiss with exactly one of these reasons:
{_dismiss_reason_lines()}

Two signals that are easy to get wrong:
- The "!_" venue-name prefix (Eventernote's undisclosed-venue placeholder,
  e.g. "!_東京都内某所") is the strongest single signal for a release event,
  but it is a SIGNAL, not a rule -- some cruises and fan events use it too.
  Weigh it alongside the title; never dismiss on it alone.
- "【当選者限定】" ("winners only") means a lottery HAPPENED. A production
  titled this way had a real deadline someone had to meet, even though it
  reads like a release event on the surface -- check for it (or an
  equivalent "by invitation to those who won" phrase) before dismissing
  anything that otherwise looks like a release/goods event.

A date prefixed "申込締切" is the APPLICATION DEADLINE, not the show date --
never read it as a leg's date; it only tells you a campaign exists and is
closing.

If a production's class is genuinely unclear, put it in NEITHER list -- a
dismissal is permanent, so an uncertain lead is left for a later pass rather
than guessed at.

## Output -- exactly ONE YAML document, no prose before or after it

dismiss:
  stage: ['481833']
  release: ['486001', '486002']
survivors:
  - title: "学園アイドルマスター LIVE TOUR -標-"
    lead_ids: ['486243', '486244']
    representative: '486243'
  - title: "LL-Fans campaign"
    lead_ids: ['ll-liella:abc@google.com']
    representative: null

`dismiss` keys are the reason values above; each value is a list of lead ids
copied VERBATIM from the input -- one entry per raw lead the dismissed
production covers (a collapsed 8-leg tour that gets dismissed still lists
all 8 ids). Omit `dismiss` (or leave it empty) when nothing is dismissed --
that is not an error.

`survivors` lists every kept production once. `lead_ids` collects every raw
lead id it covers. `representative` is the bare numeric Eventernote id of
one representative leg (whichever performance looks most complete/central),
or `null` when every lead behind this production is calendar-sourced with no
Eventernote page at all.
"""


def _format_lead_for_prompt(lead: LeadLine) -> str:
    date = f"申込締切 {lead.date_iso}" if lead.date_is_deadline else lead.date_iso
    return (
        f"- id={lead.source_event_id} title={lead.title} date={date} "
        f"venue={lead.venue} source={lead.source}"
    )


def classify_prompt(leads: Sequence[LeadLine]) -> tuple[str, str]:
    user = "\n".join(_format_lead_for_prompt(lead) for lead in leads)
    return _CLASSIFY_SYSTEM_PROMPT, user


_FENCE_RE = re.compile(r"^```(?:yaml)?\s*\n?(.*?)\n?```$", re.DOTALL)


def extract_yaml(text: str) -> str:
    """Strip a ```yaml ...``` / ``` ...``` fence if the whole response is
    wrapped in one, else return the stripped text as-is -- models reliably
    wrap YAML in a fence even when told not to."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def parse_classify_response(text: str) -> ClassifyResult:
    yaml_text = extract_yaml(text)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise TriageResponseError(f"that doesn't parse as YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise TriageResponseError(
            "expected a YAML mapping with 'dismiss'/'survivors' keys -- got something else"
        )

    prune_yaml = ""
    dismiss = data.get("dismiss")
    if dismiss:
        if not isinstance(dismiss, dict):
            raise TriageResponseError(
                f"dismiss: expected a mapping of reason -> list of ids, got "
                f"{type(dismiss).__name__}"
            )
        prune_yaml = yaml.safe_dump({"dismiss": dismiss}, allow_unicode=True)
        try:
            parse_prune_list(prune_yaml)
        except PruneListError as exc:
            # Junk dismiss YAML is fatal for the WHOLE response, not just a
            # skipped entry -- storing it for the owner to paste at
            # /admin/discoveries/prune would only move the failure to the
            # plan screen there instead of catching it here.
            raise TriageResponseError(f"dismiss list doesn't hold up: {exc}") from exc

    warnings: list[str] = []
    survivors: list[Survivor] = []
    raw_survivors = data.get("survivors")
    if raw_survivors is None:
        raw_survivors = []
    if not isinstance(raw_survivors, list):
        warnings.append(
            f"survivors: expected a list, got {type(raw_survivors).__name__} -- ignored"
        )
        raw_survivors = []

    for i, raw in enumerate(raw_survivors, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"survivor {i}: expected a mapping -- skipped")
            continue
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            warnings.append(f"survivor {i}: missing title -- skipped")
            continue
        lead_ids_raw = raw.get("lead_ids")
        if not isinstance(lead_ids_raw, list) or not lead_ids_raw:
            warnings.append(
                f"survivor {i} ({title.strip()!r}): lead_ids must be a non-empty list -- skipped"
            )
            continue
        lead_ids = tuple(str(lid).strip() for lid in lead_ids_raw)
        rep_raw = raw.get("representative")
        representative = str(rep_raw).strip() if rep_raw is not None else None
        representative = representative or None
        survivors.append(
            Survivor(title=title.strip(), lead_ids=lead_ids, representative=representative)
        )

    return ClassifyResult(
        prune_yaml=prune_yaml, survivors=tuple(survivors), warnings=tuple(warnings)
    )


# -- Draft prompt (pass 3 of triage-leads/SKILL.md, delegated to add-concert
#    vocabulary) --------------------------------------------------------

# The add-concert skill's skeleton (.claude/skills/add-concert/SKILL.md,
# references/example-draft.yaml), trimmed to one leg and rounds: []. Same
# top-level keys parse_draft (app.domain.yaml_import) knows: trilingual
# title, series/artist NAME lists (never ids), per-leg date/venue/labels.
_DRAFT_SKELETON = """\
title: 例）ライブタイトル
title_en: Example live title
title_zh: 示例演唱会标题
kind: tour
organizer: 主催者名
categories: anime song
series:
  franchises: [Franchise Name]
  groups: [Group Name]
  artists: [Performer Name]
performers: [Performer Name]
eventernote_url: https://www.eventernote.com/events/000000
official_url: https://example.com/
source_url: https://example.com/
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Venue Name
    city: City Name
    doors_jst: 2026-01-01 15:30
    starts_at_jst: 2026-01-01 17:00
    eventernote_event_id: "000000"
rounds: []
"""

_DRAFT_SYSTEM_PROMPT = f"""\
You are drafting ONE dekimasen.app concert import, in the exact vocabulary
`.claude/skills/add-concert/SKILL.md` uses. Output exactly ONE YAML document
in this shape, no prose before or after it:

```yaml
{_DRAFT_SKELETON}```

Rules:
- Japanese is canonical. Fill title/title_en/title_zh and every leg label as
  ALL THREE languages, or leave them all blank -- never a partial set.
- `series.franchises` / `series.groups` / `series.artists` and `performers`
  are NAME lists, exactly as the source spells them -- NEVER ids or slugs.
  An unmatched name becomes a harmless preview hint at paste time; guessing
  an id would attach the wrong tag silently.
- Eventernote pages carry per-leg facts (date, venue, doors/start, cast) and
  NEVER round/ticket information -- Eventernote has no lottery data at all.
- `rounds: []` is the safe default and is what this skeleton shows. Only add
  a round when its window comes from that production's own OFFICIAL ticket
  page. NEVER invent an apply_opens_jst / apply_closes_jst / results_jst /
  payment_deadline_jst -- a fabricated deadline reaches a real user as a
  real reminder for a deadline that was never real. If the ticket page can't
  be found or confirmed, leave `rounds: []` exactly as shown.
- A "申込締切"-prefixed date from a calendar lead is a POINTER to a deadline,
  not a confirmed round -- confirm it on the official page before writing it
  into a round, or leave the round out entirely.
"""


def _draft_lead_line(lead_id: str, leads_by_id: dict[str, LeadLine]) -> str:
    lead = leads_by_id.get(lead_id)
    if lead is None:
        return f"- {lead_id}: (no matching lead line)"
    date = f"申込締切 {lead.date_iso}" if lead.date_is_deadline else lead.date_iso
    return f"- {lead_id}: {lead.title} -- {date} @ {lead.venue} ({lead.source})"


def draft_prompt(
    survivor: Survivor, leads: Sequence[LeadLine], page_html: str
) -> tuple[str, str]:
    leads_by_id = {lead.source_event_id: lead for lead in leads}
    lead_lines = "\n".join(_draft_lead_line(lid, leads_by_id) for lid in survivor.lead_ids)
    truncated_html = page_html[:PAGE_CHAR_CAP]
    user = (
        f"Production: {survivor.title}\n"
        f"Representative Eventernote id: "
        f"{survivor.representative or '(none -- calendar-only lead)'}\n"
        f"Known legs from discovery:\n{lead_lines}\n\n"
        f"Official ticket page HTML (may be truncated at {PAGE_CHAR_CAP} "
        f"characters):\n{truncated_html}"
    )
    return _DRAFT_SYSTEM_PROMPT, user


def strip_rounds(draft_text: str) -> str:
    """Drop whatever `rounds:` the model wrote and replace it with `[]`,
    unconditionally -- called on every draft response regardless of what the
    model said, since a model asked for `rounds: []` can still invent one
    anyway and this is the one rule both skills repeat under threat of a
    real user getting a fake reminder."""
    data = yaml.safe_load(draft_text)
    if not isinstance(data, dict):
        data = {}
    data["rounds"] = []
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
