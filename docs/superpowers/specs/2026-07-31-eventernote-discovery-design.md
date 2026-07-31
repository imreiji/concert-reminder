# Eventernote actor-page discovery

**Date:** 2026-07-31
**WISHLIST:** #1
**Status:** design agreed, not implemented

## Goal

Tell the maintainer about performances by artists in the catalogue that the
catalogue does not have, so a concert never goes untracked because nobody knew
it existed.

## What this is, and what it deliberately is not

It is a **lead generator**, not an importer.

Eventernote carries no ticket information. The `add-concert` skill already says
so in as many words: eventernote is authority for per-leg facts (date, venue,
doors, cast) and never for rounds. Rounds -- the lottery windows and deadlines
that are the whole point of this app -- come from official ticket pages. So
discovery cannot produce a finished draft, and must not pretend to. It produces
a lead: *this performance exists and you are not tracking it*.

Turning a lead into a draft stays with an agent following
`.claude/skills/add-concert/SKILL.md`, exactly as it works today. That split is
the design:

- **The app** does what is mechanical -- fetch a list, parse rows, compare
  against the catalogue, report what is unaccounted for. No LLM is involved at
  any point, which is a hard constraint: the deploy has no API access.
- **The agent** does what needs judgment -- grouping loose legs into one
  concert, finding the official ticket page, extracting rounds, writing
  trilingual titles.

A second consequence of the split is robustness. Parsing a LIST of (title, date,
venue, id) is far less fragile than parsing a whole event page, because a site's
index markup changes less often than its detail pages.

## Why this is buildable now

Two prerequisites landed in the last three days:

- **Tag handles** (#113) gave a tag a stable identity, so "is this performer
  already a tag?" is a lookup rather than `match_tag_ids_by_name`'s documented
  first-tag-wins guess.
- **86 actor ids** now sit in `tags.eventernote_url` (2026-07-31), which is the
  input this feature reads. One tag, `yuki-sato-liella`, is a test tag with no
  Eventernote presence and is correctly blank.

## Measured facts about the source

These were measured against the live site, not assumed (per the
measure-don't-reason rule in CLAUDE.md):

- An actor's events page is `/actors/<name>/<id>/events`. The name segment is
  DECORATIVE -- `/actors/x/5847` resolves identically -- so the id is the key.
  This was verified by fetching a deliberately wrong name segment.
- Rows are **20 per page, strictly newest-first**. For Liyuu (34637) on
  2026-07-31, page 1 ran 2026-11-15 down to 2026-08-01 (all future) and page 2
  opened at 2026-07-26 (already past).
- Therefore **future events are always a prefix of the list**. Walk page 1, keep
  rows while the date is not in the past, stop at the first past row. Nearly
  every artist needs exactly one fetch; only an artist with more than 20 future
  events needs page 2.
- A full sweep is therefore ~86 fetches, not the ~1,548 that walking all 18
  pages per artist would cost.
- The site advertises its next-page link on an `eventernote.s3.amazonaws.com`
  host. This is not hypothetical SSRF paranoia: a fetcher that follows where the
  page points leaves the host it was pinned to.

## Architecture

### `src/app/domain/eventernote.py` (new, pure)

No I/O, no ORM, no httpx -- the same shape as `domain/ingest.py`, which takes an
HTML string and returns a draft without fetching anything itself.

```
@dataclass(frozen=True)
class ActorEvent:
    event_id: str      # eventernote's numeric event id, as text
    title: str
    date: date         # JST calendar date; the list gives no time
    venue: str         # free text as displayed

def parse_actor_events(html: str) -> list[ActorEvent]
def future_events(events: Sequence[ActorEvent], today_jst: date) -> list[ActorEvent]
```

`future_events` implements the stop rule as a pure function over already-parsed
rows: take while `date >= today_jst`, stop at the first row that is not. It takes
`today_jst` as a parameter rather than reading the clock, so it is testable
without freezing time.

Rows that cannot be parsed are SKIPPED AND COUNTED, never raised on -- the same
warnings-over-failures philosophy as `parse_draft` and `parse_tags`. A site
redesign should degrade to "found nothing", which the operator can see, not to a
crashed scheduler tick.

### The fetch (service layer)

Reuses the three-way guard from `web/routes/imports.py` verbatim, with
`eventernote.com` as the allowlisted host:

1. https + exact host only, checked before the request
2. the same check re-run on every redirect hop via an httpx response hook
3. the body streamed under a byte cap

`MAX_REDIRECTS` and the byte cap carry over unchanged. As with
`fetch_ramen_html`, the fetch function accepts a test-only
`httpx.AsyncBaseTransport` so tests never touch the network.

Fetches within a sweep run **sequentially with a small delay**, not concurrently.
Eighty-six parallel requests at a third party is rude and is the kind of thing
that gets an IP blocked.

### The diff (service layer)

Compares parsed events against the catalogue and returns only what is
unaccounted for. Pure decision logic lives in the domain module where it can;
anything needing the DB stays in `db/service.py` with the rest of the business
logic.

## State: `discovered_events`

A new table, **keyed on the Eventernote event id**, one row per event rather
than per artist. This is load-bearing: the LoveLive 15th anniversary concert
lists nine catalogue tags as performers, and without an id key the maintainer
would be told about it nine times.

| column | notes |
|---|---|
| `id` | PK |
| `eventernote_event_id` | unique, the match key |
| `title`, `venue` | as seen, for display |
| `event_date` | a `Date` column, NOT a datetime. The list gives a calendar day and no time, and inventing midnight would create a fake deadline-shaped value in a schema where every datetime is an aware UTC instant (invariant 1). It is a JST calendar date, like the performance dates rendered by `fmt_day_month`. |
| `first_seen_via_tag_id` | FK to `tags.id`, ON DELETE SET NULL -- which artist surfaced it |
| `first_seen_at`, `last_seen_at` | timestamps, aware UTC per invariant 1 |
| `announced_at` | nullable -- when it went out in a DM |
| `dismissed_at` | nullable -- the maintainer waved it off |
| `concert_id` | nullable FK, set once it becomes a real concert |

A lead is **open** when `announced_at`, `dismissed_at` and `concert_id` are all
NULL.

Also new: **`ConcertDay.eventernote_event_id`**, nullable, indexed. Populated by
the import path going forward. This is what makes "do I already have this?" an
exact id lookup instead of fuzzy title matching -- Japanese titles vary in
spacing, brackets and 〜 marks; ids do not.

## Is it already present?

Evaluated in this order:

1. **A leg carries this event id** -> present, silently. Exact, no guessing.
2. **Dismissed** -> never mentioned again.
3. **Already announced** -> not re-announced.
4. **Otherwise** -> a new lead, reported exactly once.

### The date-and-venue collision is a HINT, never a suppression

Existing concerts have no event ids, so for a while many leads will duplicate
concerts already in the catalogue. The obvious heuristic -- same date, same
venue, therefore already held -- is **wrong in a case this app models
explicitly**: 昼公演 and 夜公演 are two separate Eventernote events on the same
date at the same venue, and they are two legs. Auto-suppressing on
date-plus-venue would hide precisely the second show.

So a collision marks the lead "you may already have this" and it is still
reported. The maintainer decides.

## The DM

Goes through the `notifications` outbox and is drained by the scheduler like
everything else -- **never sent from the walk directly** (invariant 4). The walk
queues; the existing drain delivers.

- `kind = "discovery"`, `concert_id = NULL`. A NULL `concert_id` already means
  "render the plain-text body rather than a rich embed", and
  `record_deliveries` already skips title lookup for such rows, so no change to
  the drain is needed.
- **Not** added to `UNREPORTED_NOTE_KINDS`. That set is only for notices that
  report ON deliveries; a discovery notice is an ordinary notice and belongs in
  `delivery_log` like any other.
- **Recipients: admins** (`ADMIN_WHITELIST`), the same audience as `ops_alert`.
  This is catalogue maintenance, not a per-user feed. `Notification.user_id` is
  an FK to `users.discord_id`, so an admin who has never signed in has no row to
  target and queuing would raise `IntegrityError` at flush, far from the cause.
  Follow `evaluate_and_alert`'s existing precedent exactly: `ensure_user` with a
  placeholder name only when `session.get(User, admin_id)` returns None. Guarded
  on absence rather than called unconditionally, because `ensure_user` refreshes
  the username and would otherwise overwrite a real admin's name with the
  placeholder on every sweep.
- **One DM per sweep**, leads grouped by artist, listing at most ten with a
  "+N more" line and a link to the review page.

### The DM carries a paste-ready agent prompt

The point of a lead is to become a concert, and the step between them is handing
the sources to an agent. The DM therefore ends with a **fenced code block** the
maintainer can copy straight into an agent session to start the add-concert
workflow.

Two halves, because Discord forces the split: text inside a fenced block is NOT
linkified, so the readable list above stays clickable while the block below stays
copyable. The same content twice is deliberate, not redundancy.

The block is a PROMPT, not a bare URL list -- it names the skill, so pasting it
is the whole action:

    Add these to dekimasen.app using the add-concert skill.
    Group legs of the same tour into ONE draft.

    https://www.eventernote.com/events/464372  2026-11-15  バンテリンドーム ナゴヤ
    https://www.eventernote.com/events/464371  2026-11-14  バンテリンドーム ナゴヤ
    https://www.eventernote.com/events/486174  2026-10-31  Veats Shibuya

Rules:

- **One block per DM, covering the leads that DM names.** Grouping legs into
  concerts is judgment and stays with the agent (the seam this whole design
  rests on), so the block does not attempt to cluster -- it carries the second
  instruction line telling the agent to do it.
- **The 2000-character message limit is a hard budget, and the block yields
  first.** Prose, the readable list, then as many block lines as fit; if lines
  are dropped the block says so on its last line. A DM that silently loses
  leads from the copy block while listing them above is the quiet kind of wrong.
- The venue is included as free text because it is what the agent needs to
  disambiguate a tour's legs, and it costs one column.
- `/admin/discoveries` offers the same block with a real copy button -- per lead
  and for a ticked selection. That surface has no character limit and is the
  better ergonomics; the DM version exists so a lead can be acted on from a
  phone without opening the site.

### Announcing marks EVERY reported lead, listed or merely counted

`announced_at` is set on all open leads the DM covers -- including those folded
into the "+N more" count -- not only the ten it names.

This matters most on the **first sweep**, when every future event of all 86 tags
is new at once, most of them duplicating concerts already in the catalogue.
Marking only the named ten would trickle a large backlog out at ten per day for
weeks, which is worse than useless: the real leads would be buried behind
duplicates of concerts already held. The count-plus-link shape says the true
thing in one message and sends the maintainer to the page, which is the surface
built for bulk triage.

## Cadence

**Once a day, not once a tick.** Eighty-six fetches on the 60s loop would be
both useless and rude. The scheduler checks a last-run timestamp and sweeps when
a day has passed.

A settings flag (`discovery_enabled`, default False, same shape as
`rehearsal_enabled` and `bot_enabled`) switches the subsystem off entirely --
which is also what keeps tests and dev runs off the network.

## `/admin/discoveries`

The review surface, admin-only, listed in Preferences beside the other admin
pages. Open leads with their source link and a Dismiss button; a filter to show
dismissed ones. English-only and not wrapped in `_()`, like `/admin/deliveries`
and `/admin/rehearsal`.

It is also where the agent reads when asked to turn a lead into a draft.

## Error handling

- A fetch that fails (timeout, non-200, host violation, oversized body) logs,
  counts, and moves to the next tag. One unreachable artist must not abort a
  sweep.
- A page that parses to zero rows is recorded as such rather than treated as
  "no future events" -- these are different facts, and conflating them is how a
  site redesign becomes a silent no-op that looks like good news.
- A sweep that finds nothing new sends **no DM at all**. Silence is the correct
  output for a quiet day; a daily "nothing found" message trains the reader to
  ignore the channel.
- The walk never writes to `concerts`. Its only writes are `discovered_events`
  rows and one queued notification.

## Testing

- **Parser**: a saved copy of a real actor events page as a fixture, asserting
  parsed count, ids, dates and the ordering assumption. Also a truncated and a
  structurally-changed page, asserting graceful degradation rather than a raise.
- **Stop rule**: pure, over synthetic rows -- including the boundary (an event
  today is future) and an all-past page.
- **Fetch guard**: the redirect-to-S3 case specifically, via `MockTransport`,
  asserting it is refused. This mirrors the existing ramen.events guard tests.
- **Diff precedence**: each of the four branches, plus the 昼/夜 case asserting
  a same-date-same-venue event is REPORTED with a hint, not suppressed.
- **Dedup**: one event listing several catalogue tags produces exactly one lead.
- **No re-announce**: two sweeps over the same page produce one DM.
- **Page render**: a logged-in admin GET of `/admin/discoveries`, per the
  every-page-has-a-render-test convention.
- **DM budget**: a sweep with many leads produces a message under 2000
  characters, and when block lines are dropped the block SAYS so. Assert the
  rendered length and the truncation notice -- not that "the code is careful",
  which is not a property.
- **Copy block content**: assert one line per named lead with its event URL, and
  that the fenced block is present and closed. An unclosed fence swallows the
  rest of the message into a code block, which is invisible in a length check.

Tests must assert the property, not a proxy for it -- the lesson recorded in the
2026-07-31 next-session notes after three tests in two days passed or failed for
reasons unrelated to their claims.

## Decisions recorded

- **All event types, no filtering.** Talk shows, radio, release events and
  streams all come through. The owner's end goal is to cover those event types
  through later feature work, so filtering them out now would build a wall he
  intends to demolish. Future-only is the single filter, and it is a date
  comparison rather than a guess about what kind of thing an event is.
- **Walks every tag with an `eventernote_url`, not just followed ones.** A
  concert missing from the catalogue is missing for everybody. Following governs
  who gets reminded, not what gets catalogued.
- **Scheduled from v1**, at the owner's request, rather than on-demand first.

## Out of scope for v1

- **Linking a dismissed lead to the existing leg it duplicates.** Dismissal ends
  the lead but does not backfill `eventernote_event_id` on the leg it matched.
  Deliberate: it keeps v1 honest and small. Revisit if date-and-venue hints turn
  out to be frequent enough to be annoying.
- **Backfilling event ids onto existing legs.** Only the import path populates
  them, so the exact-match branch grows in coverage over time rather than
  arriving complete.
- **Walking past events.** The stop rule ends at today by construction.
- **Any automatic concert creation.** `import_commit` remains the only write
  path into `concerts`, and a lead never bypasses it.
