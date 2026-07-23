---
name: add-concert
description: Build a paste-ready trilingual concert draft for dekimasen.app from source URLs (official ticket pages, eventernote, ramen.events). Use when the user says "add this concert", "import this event", gives concert/live event URLs, or asks to draft a new event for the tracker.
---

# Add a concert from source URLs

Turn one or more source pages into ONE YAML draft the user pastes at
`https://dekimasen.app/concerts/import` (the "Or paste an agent draft"
box — visible to signed-in editors only, so the user needs editor access
on dekimasen.app). The app renders a prefilled review form; nothing is
saved until the user submits it, so your draft is a proposal, not a
write.

The schema is `references/example-draft.yaml` in this skill directory --
read it first, copy its shape exactly. If the paste preview shows
"unknown key" warnings, this skill copy has drifted behind the app: tell
the user to fetch a fresh copy from the dekimasen.app maintainer.

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

- Use your web-fetch tool first.
- On 403 (lovelive-anime.jp blocks every non-browser client), fall back
  to browser automation through the user's signed-in browser if your
  environment offers it (e.g. Claude-in-Chrome: get the tab context
  first, open the page in a new tab, read its text). If you have no
  browser tool, ask the user to paste the page text.
- If a page is unreachable both ways, say so and continue with what you
  have -- an incomplete draft with a note beats an invented one.

## 3. Extraction rules

- **Times are JST wall-clock**, formatted `YYYY-MM-DD HH:MM`. Japanese
  sources write 23:59 as-is but may write 27:00 for 3am next day --
  normalize to the real calendar day.
- **Never invent a time.** If a source gives only a date ("8月中旬"), omit
  the field and mention it under `notes` so the user sees it.
- Round kinds (the `kind` value strings):
  - 抽選 / 先行 / 最速先行 / 次先行 -> `lottery_round`
  - a CD/BD sale that exists to distribute serial codes -> the lottery
    itself is still `lottery_round`; the item's own sale, if listed as a
    deadline, is `eligibility_item_sale`
  - 一般発売 that is explicitly 先着 (first come) -> `fcfs_sale`
  - 一般発売 that is itself a lottery -> `general_sale`
  - 配信 / streaming tickets -> `stream_ticket_sale`
  - overseas hotel+ticket packages -> `tour_package`
  - アップグレード (needs an existing ticket) -> do NOT emit; upgrade rounds
    have qualifier semantics the import path doesn't carry -- note it in
    `notes` for the user to add by hand.
- 当落発表 / results and 入金期限 / payment are ANCHORS on their lottery
  round (`results_jst`, `payment_deadline_jst`), not separate rounds.
- `applies_to`: the exact `label` strings of the performances a round
  covers. Empty list = whole event. A round selling 全公演 or with no
  per-leg distinction gets `[]`.

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
  never a silent drop. Ask the user only when a name is genuinely
  ambiguous.
- `series.artists`: list the PERFORMERS explicitly (from eventernote's
  cast list) -- group tags do not auto-expand on this path, and the cast
  actually announced is the truth anyway.
- `performers`: the same cast list, one name per entry (this fills the
  free-text performers field).

## 6. Emit and hand off

- Output the complete YAML in ONE fenced block, nothing else in it.
- After the block, list anything uncertain or missing (unfetchable page,
  date-only deadline, guessed kind) as bullet points.
- Tell the user: paste it at https://dekimasen.app/concerts/import --
  unmatched tag/venue names show as hints there, venues can be created
  inline with "+ New venue", and nothing is saved until "Create concert".
