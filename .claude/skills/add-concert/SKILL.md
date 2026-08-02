---
name: add-concert
description: Build a paste-ready trilingual concert draft for dekimasen.app from source URLs (official ticket pages, eventernote, ramen.events). Use when the owner says "add this concert", "import this event", gives concert/live event URLs, or asks to draft a new event for the tracker.
---

# Add a concert from source URLs

Turn one or more source pages into ONE YAML draft the owner pastes at
`https://dekimasen.app/concerts/import` (the "Or paste an agent draft"
box). The app renders a prefilled review form; nothing is saved until the
owner submits it, so your draft is a proposal, not a write.

The schema is `references/example-draft.yaml` in this skill directory --
read it first, copy its shape exactly. It is pinned by a test
(`tests/test_yaml_import.py::test_skill_example_draft_parses_clean`);
if the app evolves, that example is the current truth.

## 1. Gather sources -- roles differ

| Source | Authority for | Never trust it for |
|---|---|---|
| Official site's TICKET page | rounds: 先行 names, windows, results, payment, prices | -- |
| eventernote.com event pages | per-LEG facts: date, venue, doors/start, cast | rounds (it has none) |
| ramen.events post | convenience cross-check | completeness |

**One eventernote event page = ONE LEG, not one concert.** A tour is one
concert with several performances; collect every leg's eventernote page
(the artist's `/actors/<name>/<id>/events` page lists them) and merge
them into a single draft's `performances` list. Never emit one draft per
eventernote page.

A day with two shows (昼公演/夜公演) is TWO performances -- each entry
carries exactly one `starts_at_jst`. Label them so a human can tell them
apart (e.g. `Day 1 昼` / `Day 1 夜`, translated in all three languages).

Official sites often split per-leg ticket info into subpages (e.g.
`/information/final.php`) -- follow the TICKET / チケット navigation until
you find actual application windows.

## 2. Fetching

- WebFetch first.
- On 403 (lovelive-anime.jp blocks every non-browser client), fall back to
  Claude-in-Chrome through the owner's signed-in browser: call
  `tabs_context_mcp` first, open the page in a new tab, read it with
  `get_page_text`.
- If a page is unreachable both ways, say so and continue with what you
  have -- an incomplete draft with a note beats an invented one.

## 3. Extraction rules

- **Times are JST wall-clock**, formatted `YYYY-MM-DD HH:MM`. Japanese
  sources write 23:59 as-is but may write 27:00 for 3am next day --
  normalize to the real calendar day.
- **Never invent a time.** If a source gives only a date ("8月中旬"), omit
  the field and mention it under `notes` so the owner sees it.
- Round kinds (the `kind` value strings):
  - 抽選 / 先行 / 最速先行 / 次先行 -> `lottery_round`
  - a CD/BD sale that exists to distribute serial codes -> the lottery
    itself is still `lottery_round`; the item's own sale, if listed as a
    deadline, is `eligibility_item_sale`
  - 一般発売 that is explicitly 先着 (first come) -> `fcfs_sale`
  - 一般発売 that is itself a lottery -> `general_sale`
  - 配信 / streaming tickets -> `stream_ticket_sale`
  - overseas hotel+ticket packages -> `tour_package`
  - グッズ販売 / 物販 (a merch/goods pre-order or sale window) -> `goods_sale`
  - アップグレード (needs an existing ticket) -> do NOT emit; upgrade rounds
    have qualifier semantics the import path doesn't carry -- note it in
    `notes` for the owner to add by hand.
- `eventernote_event_id`: on each performance, the numeric id out of the
  eventernote URL that leg came from (`.../events/465358` -> `"465358"`,
  quoted) -- it is how the app later recognizes it already has that show.
- 当落発表 / results and 入金期限 / payment are ANCHORS on their lottery
  round (`results_jst`, `payment_deadline_jst`), not separate rounds.
- `applies_to`: the exact `label` strings of the performances a round
  covers. Empty list = whole event. A round selling 全公演 or with no
  per-leg distinction gets `[]`.
- `requires:` (optional) -- the ja `label` of another round IN THIS DRAFT
  (an `eligibility_item_sale` or `goods_sale` round) whose item is needed
  to enter this round. Example: a 最速先行 whose serial code comes from the
  CD sale names that CD-sale round's label here. It names a LABEL, the same
  way `applies_to` names legs -- never an id. A label that matches no round,
  matches this same round, or matches one of any other kind shows up as a
  visible warning at paste time and the link is simply dropped, so guessing
  costs nothing.

## 4. Trilingual rules (the app enforces these at submit)

- Japanese is canonical. For the title, notes and EVERY performance/round
  label you fill, provide all three of ja/en/zh -- or none of the three.
- Translate faithfully and plainly; keep proper nouns (venue names, fan
  club names, retailer names like ファミリーマート) recognizable --
  established romanizations for en, established fan translations for zh
  where they exist.
- Venue names: use the JAPANESE canonical name in `venue` (it must match
  the app's VENUE tag names, which are canonical Japanese). Include `city`
  and `venue_address` when the venue might be new to the app -- they
  prefill the inline create-a-venue dialog.

## 5. Tags

- `series.franchises` / `series.groups`: the franchise and unit names as
  the app's Tags page spells them. You cannot read that page (it is
  login-gated), so use the names the sources themselves use and don't
  agonize: an unmatched name shows up as a visible hint at paste time,
  never a silent drop. Ask the owner only when a name is genuinely
  ambiguous.
- `series.artists`: list the PERFORMERS explicitly (from eventernote's
  cast list) -- group tags do not auto-expand on this path, and the cast
  actually announced is the truth anyway.
- `series.characters`: for a bill credited to CHARACTERS rather than to the
  performers who play them (an idolm@ster event bills 如月千早, not 今井麻美),
  name the characters here. The app attaches each character's voice actor
  automatically, so do NOT also list her under `series.artists` -- the
  character is the credit and the seiyuu is derived from it. A Love Live-shaped
  bill has no characters at all; leave the key out.
- `performers`: the same cast list, one name per entry (this fills the
  free-text performers field).

## 6. Emit and hand off

- Output the complete YAML in ONE fenced block, nothing else in it.
- After the block, list anything uncertain or missing (unfetchable page,
  date-only deadline, guessed kind) as bullet points.
- Tell the owner: paste it at https://dekimasen.app/concerts/import --
  unmatched tag/venue names show as hints there, venues can be created
  inline with "+ New venue", and nothing is saved until "Create concert".
