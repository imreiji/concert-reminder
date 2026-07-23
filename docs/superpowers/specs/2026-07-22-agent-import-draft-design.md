# Agent-driven concert import: YAML draft round-trip + add-concert skill

Date: 2026-07-22
Status: approved by owner (design discussion, this date)

## Problem

The trilingual arc (all-three-languages-or-none enforcement, phase 4) made
manual event creation roughly three times the typing on exactly the fields
no scraper reaches: `title_en`/`title_zh` are now mandatory, and every
filled leg or round label owes EN + 中文 variants. The ramen.events importer
fills one language of a subset of fields from one source, and neither
ramen.events nor any other single site carries a whole concert:

- **Official ticket pages** (tutoliella-live.jp, idolmaster-official.jp
  TICKET subpages) are the authority on rounds -- 先行 names, application
  windows, prices, general sale -- but split legs across subpages, and
  lovelive-anime.jp 403s every non-browser fetch (verified: WebFetch and
  browser-UA curl both blocked).
- **eventernote.com** has per-LEG facts (date, venue, doors/start/end,
  full cast) and artist pages listing all upcoming events, but zero ticket
  round data. One eventernote event page = one leg, not one concert.
- **ramen.events** is a curated summary with limited coverage.

Merging sources and translating labels is agent work, not regex work. The
owner has no budget for server-side API calls, so the intelligence lives
agent-side (Claude Code + a repo skill) and the app grows only a small,
dumb intake seam.

## Shape

```
sources (official / eventernote / ramen.events)
   -> agent, guided by .claude/skills/add-concert
   -> one YAML draft block (the yaml_export vocabulary, made two-way)
   -> editor pastes it at /concerts/import
   -> POST /concerts/import/draft parses + resolves names -> ids
   -> the SAME import_preview.html, fully prefilled
   -> editor proofreads in the browser
   -> the UNCHANGED import_commit writes
```

Nothing auto-saves. The preview stays the human gate, and `import_commit`
stays the only write path with every existing gate intact (variants rule,
`form_url`, `sync_concert_venue_tags`, phrase recording).

## 1. Draft schema: yaml_export becomes two-way

The draft is `domain/yaml_export.py`'s vocabulary, extended for parity
with what the app now stores. `yaml_export` itself gains the same fields
so export -> paste -> preview round-trips.

- Add `title_zh`, `notes_en`, `notes_zh` (export and import).
- `performances[].venue` is the **VENUE tag name**. The legacy `city` /
  `venue_address` keys stay accepted on import as hints only -- surfaced
  beside the quick-create dialog when no tag matches a leg's venue name --
  because city/address now live on the tag, not the leg.
- `rounds[].applies_to` stays a list of **leg labels**; the server maps
  each label to that preview row's `day_key`, the binding vocabulary the
  form already speaks. Empty list keeps its existing meaning (all legs).
- All times are `YYYY-MM-DD HH:MM` **JST wall-clock** strings -- the same
  boundary web forms use. Conversion happens at `parse_jst` on commit,
  never a second path (invariant 1).
- `series.franchises` / `series.groups` / `series.artists` and per-leg
  venues are **names**, resolved server-side at paste time. The draft
  never carries database ids.
- `kind` (concert-level) and `rounds[].kind` are the enum value strings
  (`lottery_round`, `fcfs_sale`, ...), matched tolerantly.

## 2. domain/yaml_import.py: pure parser, sibling of ingest.py

`parse_draft(text: str) -> DraftConcert`. No I/O, no ORM imports,
`yaml.safe_load` only. Same philosophy as the ramen parser: tolerant,
warnings over failures.

- Hard error (`DraftError`) only when the text is not YAML, not a
  mapping, or has no title. The route re-renders the import form with the
  message, exactly like `IngestError`.
- Everything else degrades to a warning carried on the dataclass and
  rendered in the preview's existing warning strip: unknown round kind ->
  `OTHER` + warning; malformed datetime -> blank field + warning; unknown
  top-level or nested key -> ignored + warning (skill/schema drift signal);
  an `applies_to` label matching no performance -> dropped + warning.
- `DraftConcert` is a superset of `ParsedConcert`'s role: trilingual
  title/notes, organizer, categories, kind, source/official URLs, tag name
  lists, days (trilingual labels, starts/doors, venue name), rounds
  (trilingual labels, kind, all four anchors, url, notes, applies_to
  labels).

## 3. Web seam: one new POST, zero new write paths

- `import_form.html` gains a second card: a textarea ("Paste an agent
  draft") posting to `POST /concerts/import/draft`, editor-only, with a
  byte cap on the pasted text.
- The route parses via `parse_draft`, then resolves names -> ids:
  - per-leg venue through `match_venue_tag_id`, called per leg (today's
    single global `matched_venue_tag_id` stays for the URL-scrape path);
  - franchise/group/artist through a new `db/service.py` helper
    `match_tag_ids_by_name(names, kind)` -- trimmed, case-insensitive,
    matching `name`, `name_en` and `name_zh`, since the agent may cite a
    tag in whichever language the source used. (Deliberately wider than
    `match_venue_tag_id`'s canonical-name-only rule: a draft names tags in
    any of the three languages by design; the scrape does not.)
  - concert `kind` -> `ConcertKind`, round kinds -> `RoundKind`.
- Renders the SAME `import_preview.html`, prefilled: titles/notes/
  organizer/kind, trilingual leg and round labels, all four round anchors,
  `initial_selected` for matched tags, each leg's venue `<select>`
  pre-picked, round-leg chips pre-toggled from `applies_to`. The template's
  inputs all exist today and render blank; this work threads values into
  them (value attributes and selected options -- no structural rebuild).
- Unmatched names are never silently dropped: each renders as a visible
  hint beside the relevant picker, the `venue_hint` pattern generalized.
  Venues offer the existing inline quick-create dialog (city/address hints
  prefill it); a new artist/group stays a Tags-page trip, same as manual.
- `import_commit` is untouched.
- Group tags do not expand on the create path (invariant 3: the explicit
  artist list is authoritative), so the skill instructs the agent to list
  performers explicitly; the seam does nothing special.

## 4. The skill: .claude/skills/add-concert/

Lives in the repo so skill and schema evolve in the same commit. Teaches:

- **Source strategy**: the official TICKET page is the rounds authority;
  eventernote is the legs/cast authority -- one eventernote event = one
  LEG, the whole tour = one concert; ramen.events, when it covers the
  event, is a cross-check. Merge before emitting one draft.
- **Fetch strategy**: WebFetch first; on 403 (lovelive-anime.jp blocks all
  non-browser clients) fall back to Claude-in-Chrome through the owner's
  signed-in browser.
- **Extraction rules**: JST wall-clock `YYYY-MM-DD HH:MM`; never invent a
  time -- omit the field and add a note instead; a round-kind mapping
  table (抽選/先行 -> lottery_round, 一般発売(先着) / 先着 -> fcfs_sale,
  配信 -> stream_ticket_sale, 当落/結果発表 -> results anchor on the
  round rather than a separate round, 入金/支払 -> payment anchor, ...);
  results and payment deadlines usually hide in prose, 注意事項 or Q&A
  sections -- hunt for them.
- **Trilingual rules**: Japanese is canonical; fill EN + 中文 for the
  title and every filled leg/round label (all-three-or-none is enforced at
  commit, so a gap bounces the form); proper nouns and established
  romanizations stay as-is.
- **Tags**: name the franchise and group AND list performers explicitly;
  per-leg venue by name, carrying city/address for quick-create.
- **Output**: one YAML block plus "paste it at
  dekimasen.app/concerts/import".

## 5. Security and error handling

- Draft input is editor-only and renders through the same escaped template
  paths scraped data already uses -- scraped data was already
  attacker-influenced, so this is no new class of input. Invariant 7's
  three rules apply unchanged (`| tojson` for picker data, no interpolation
  into `on*` handlers, URLs through `form_url` at commit).
- `yaml.safe_load` only; byte cap on the textarea before parsing.
- The commit boundary re-validates everything exactly as today; a
  tampered or malformed draft can at worst produce a preview the editor
  declines to submit, or a 422 at commit.

## 6. Testing

- Pure parser: a complete good draft; hostile/malformed drafts (not YAML,
  YAML bomb-ish nesting within the byte cap, wrong types per field);
  unknown keys warn; unknown kinds warn and fall back; round-trip test
  (export a concert via `concert_to_yaml`, `parse_draft` it, assert field
  equality).
- Route: draft -> 200 preview with prefilled values asserted (trilingual
  labels, anchors, `initial_selected`, per-leg venue selection, leg-chip
  pre-toggle); unmatched-name hints render; `DraftError` re-renders the
  form with the message; oversized paste rejected.
- Existing import tests untouched; the logged-in GET render test rule
  covers the extended import form.

## Out of scope (WISHLIST, not built now)

- Eventernote actor-page discovery ("check my artists' pages for new
  events") -- a natural later extension of the skill.
- In-app LLM extraction -- the seam is producer-agnostic, so this stays
  possible later without rework.

## Accepted trade-offs

- One copy-paste hop per import, accepted to avoid a new auth mechanism
  and server-side draft storage.
- Translation quality rides on the agent; the preview proofread is the
  quality gate, the same stance as the machine-assisted catalogue
  translations.
