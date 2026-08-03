---
name: triage-leads
description: Turn a batch of dekimasen.app discovery leads (the sweep's DM copy block, or /admin/discoveries' paste block -- Eventernote pages and fan-maintained calendar feeds both) into a prune list and a multi-concert import batch. Use when the owner pastes discovery leads, asks to "triage leads", "process the discovery queue", "clear the backlog", or "prune the discoveries".
---

# Triage discovery leads

The daily discovery sweep finds events the catalogue may lack and calls them
*leads*. It reads two kinds of source -- Eventernote actor pages, and a
handful of fan-maintained public calendars -- and both arrive in the same
paste. A lead is not a concert -- it says only "this exists and you are not
tracking it". Closing the loop from lead to tracked concert has meant a human
reading every one, one at a time, at over 400 open leads. This skill does the
first two-thirds of that reading (no ticket page needed) and hands the
survivors to `add-concert` for the third.

It produces exactly two files, never touches the database, and never
guesses a deadline. Read `docs/discovery-lead-taxonomy-2026-08-01.md` before
starting a real triage -- it is the survey this skill's rules were read off,
with the seven lead classes, their signals, and the 2026-08-02 scope ruling
below in full. This file states only the operational rules; that one is the
reference.

## The one rule that overrides everything below

**Eventernote carries NO ticket or lottery information whatsoever.** It knows
a date, a venue, and a cast -- never an application window, a result date, a
price, or an organizer. Every round time in a survivor's draft (Pass 3) must
come from that production's own official ticket page, or it must not exist.

**A calendar lead is not the exception it looks like.** Some feeds are ticket
calendars, so a lead can arrive already saying `申込締切 2026-09-15` -- and
that is still a fan-maintained POINTER, not a verified round. It has no time
of day, no round name you can trust, and no way to tell a first lottery from
a fourth. Copying it into `apply_closes_jst` would be inventing a deadline
with an extra step. Confirm it on the official page like any other round, or
leave the round out.

A fabricated `apply_closes_jst` that reaches `import_commit` sends a real
user a real reminder for a deadline that was never real. That is worse than
importing nothing: the app's entire promise is that a deadline it names is
real. If the ticket page cannot be found, the honest draft has an empty
`rounds:` list -- or the lead waits for a later pass. Never fill a round field
from a guess, a typical pattern, or "what these usually look like."

## 0. What you start from

Your input is pasted text -- the sweep's DM copy block, or the fenced block
`/admin/discoveries` renders -- never a login. You cannot open the Tags page
or the discoveries page yourself; everything you know about a lead is in the
paste. Each line names an id, a date, and a venue, in that order; the prose
half above it (when present) adds a title and a source heading. That id is
the only id you will ever see -- `DiscoveredEvent.id` is internal and never
appears in what you're given.

**Two shapes of id, and a mixed batch is normal.** A line from an Eventernote
actor page carries its event URL (`https://www.eventernote.com/events/486243`)
and the number in it is the event id. A line from a calendar feed carries a
namespaced id instead -- `<feed key>:<UID>`, like
`ll-liella:abc123def@google.com` -- because there is no Eventernote page
behind it and nothing to link to. Its prose heading is the feed's label
("LL-Fans Liella!", "imas 申込期限") where an Eventernote lead's is the artist
whose page surfaced it. **Both go into the prune list VERBATIM**, exactly as
the paste spelled them, quoted if YAML needs it: the parser takes any string
and the app matches on the whole value, so trimming a namespace or "tidying" a
URL into a bare number turns a real dismissal into an id that matches nothing.

**A date prefixed `申込締切` IS THE APPLICATION DEADLINE, not the show date.**
Some feeds are ticket calendars, so their entry says when applications close
and says nothing at all about when the performance is. Never file such a date
as a leg's date -- a lead like `申込締切 2026-09-15` may well be for a concert
in December. Both the show dates and the real round times come from Pass 3's
research; this line only tells you a campaign exists and is closing. A plain
date with no prefix is a performance date, as before. (The prefix is additive
-- the rest of the line keeps its field order, so parse it the same way.)

Work in three passes, cheapest first, and stop at the end of each one to
produce its file before starting the next -- Pass 3 is the only one that
costs a fetch, and there is no reason to pay for it before Pass 1 and 2 have
cut the queue down.

## 1. Collapse by title stem (no network)

The largest single reduction available, and the trap that makes it worth its
own pass: **several different mechanisms produce a repeated title, and they
want opposite treatment.**

| Pattern | Example | What it becomes |
|---|---|---|
| A tour or a multi-day run at different times/venues | 学園アイドルマスター LIVE TOUR -標- (4 cities, 8 leads) | ONE concert, one leg per date/venue |
| A per-member or per-part split at ONE venue on ONE day | 『Liella!と結ぶプロジェクト』お渡し会 (11 leads, one per member) | ONE event (or none, if Pass 2 dismisses it) -- never 11 legs |
| Successive ROUNDS of one campaign, from a ticket calendar (`申込締切` lines) | 最速先行 / オフィシャル2次先行 / 一般発売 of the same live, 3 leads | ONE production -- and those are its rounds, not its legs |

The third row is the calendar feeds' own shape and is the newest: a ticket
calendar lists one entry per round, so a single campaign arrives as a small
run of deadline lines whose titles share a stem and whose dates are days or
weeks apart. Merge them into one production. Do NOT read them as legs (they
are the same performance sold three times) and do not carry their dates into
Pass 3 as round times -- see the calendar rider at the top of this file.

The test: do the repeated leads differ by *when/where the audience goes*, or
only by *which member's individual slot it is*? The first is legs of a tour;
the app models it natively. The second is one event Eventernote happened to
list once per performer -- collapsing it into a multi-leg concert would
invent legs nobody experiences as separate performances. When in doubt,
check whether the times overlap on the same day at the same venue: that
overlap is the per-member signature.

Group every lead into one production per title stem before Pass 2. 443 raw
leads collapse to roughly 120-150 productions this way, before any
classification happens.

## 2. Classify against the scope ruling (no network)

The 2026-08-02 scope ruling is binary: **catalogue ticketed concerts/tours
and radio/talk/番組イベント; everything else is a dismissal with a reason.**
Per production (not per raw lead -- Pass 1 already merged the duplicates):

| Keep | `DismissReason` | Dismiss for |
|---|---|---|
| Ticketed concert/tour | -- | -- |
| Radio / talk / 番組イベント | -- | -- |
| | `live` | A real concert/tour, just not one worth tracking |
| | `stage` | 朗読劇 / ミュージカル / 舞台 / リーディング |
| | `release` | 発売記念 / お渡し会 / 特典会 / 写真集 |
| | `festival` | Multi-artist bill; the concert is the festival, not this artist |
| | `fanmeet` | ファンミーティング / バースデーイベント with no lottery to track |
| | `free` | No ticket at all -- 餅まき, 盆踊り, 駅長就任式 |
| | `other` | Doesn't fit any class above |

`talk` is never a dismissal reason -- the ruling keeps that whole class in
the queue unconditionally.

Two rules the taxonomy earned, both there to stop a keyword match from being
wrong in the specific way that loses a lead that mattered:

- **The `!_` venue prefix** (`!_東京都内某所`, Eventernote's
  undisclosed-venue placeholder) is the strongest single signal for a
  release event, but it is a SIGNAL, not a rule -- some cruises and fan
  events use it too. Weigh it alongside the title, don't dismiss on it alone.
- **【当選者限定】 ("winners only") means a lottery HAPPENED.** A production
  titled this way had a real deadline someone had to meet, even though it
  reads like a release event on the surface. A blanket
  dismiss-on-発売記念-keyword rule would lose exactly this one -- check for
  【当選者限定】 (or an equivalent "by invitation to those who won" phrase)
  before dismissing anything that otherwise looks like class C.

**If a production's class is genuinely unclear, leave it out of the prune
list rather than guess.** A dismissal is permanent -- see the warning in
section 4 -- and an uncertain lead belongs in neither file, to be looked at
again later.

Write every dismissal to a prune list in the format
`.claude/skills/triage-leads/references/example-prune-list.yaml` shows --
read it first, copy its shape exactly. It is a YAML mapping of
`DismissReason` value to a list of lead ids written exactly as the paste
gave them (an Eventernote event id, or a namespaced calendar id -- never
internal lead ids; see section 0), one entry per RAW lead that production
covered (a collapsed 8-leg tour that gets dismissed still lists all 8 ids). It is parsed by
`app.domain.prune_list.parse_prune_list` and pinned to it by
`tests/test_skill_triage_leads.py`.

## 3. Research the survivors (needs the ticket page)

The only pass that costs a fetch, and it exists for exactly what survived
Pass 2. **Delegate the actual drafting to the `add-concert` skill, one call
per surviving production** -- it owns the draft schema, the fetching order,
the trilingual rules and the tag vocabulary, and restating any of that here
would only let this skill drift out of sync with the parser add-concert is
already pinned to. Feed it what you collected: the production's title, its
merged leg list from Pass 1, the artist(s), and the eventernote URLs for
each leg (`https://www.eventernote.com/events/<id>`) as its per-leg source.
A calendar-sourced production has no such URL to hand over -- give
`add-concert` the title, the feed's franchise and whatever the deadline
lines said, and let it find the official page from there. It is the weaker
starting point of the two, which is a reason to be more careful about the
rounds it comes back with, never a reason to fill them in yourself.

Work in small batches -- five to ten productions at a time -- rather than
researching the whole survivor list before emitting anything. Each
production needs its own official-site fetch, and a batch that fails
partway (an unreachable site, a page that turns out to need a browser)
should not cost the productions already drafted. Emit each batch as its own
multi-document YAML file and hand it off (section 4) before starting the
next.

Collect every survivor's draft into one file: several YAML documents
separated by `---`, no wrapper key -- exactly what
`.claude/skills/triage-leads/references/example-batch.yaml` shows, and what
`app.domain.yaml_import.parse_drafts` reads. Each document is a complete,
independent `add-concert` draft; nothing in one document may depend on
another, because each is stored and reviewed on its own.

Follow the same-hesitation rule as Pass 2: if a survivor's ticket page can't
be found or its rounds can't be pinned down honestly, emit it with an empty
`rounds:` list (or leave it out of this batch entirely for a later pass) --
never with invented times. See the top-of-file warning; it is not optional
here.

## 4. Emit and hand off

You will produce up to two files per triage session (a prune list from Pass
2 is expected every time; a batch from Pass 3 only once something survives):

- **The prune list** goes to `https://dekimasen.app/admin/discoveries/prune`
  (admin-only). Pasting it there shows a PLAN -- what would be dismissed and
  why, unknown ids, already-dismissed ids, and any id claimed under two
  reasons -- before anything is written; a second paste on the plan screen
  commits it.
- **The batch** goes to `https://dekimasen.app/concerts/import`, into the
  "paste many drafts" box (posts to `/concerts/import/batch`). Each document
  that parses becomes one row on that editor's pending-drafts list, reviewed
  and committed ONE AT A TIME through the same prefilled preview
  `add-concert`'s single-draft output already uses.

State both destinations explicitly when you hand the files back -- a
paste-ready file with nowhere named to paste it has not closed the loop.

Two things to say plainly every time, because they are easy to read past:

- **This skill proposes; it never writes.** Both files are proposals the
  owner imports through a surface that shows a plan (the prune list) or a
  full review form (the batch) before committing anything.
  `db.service.import_commit` remains the only path that ever writes a row
  into `concerts`, exactly as it is for a single `add-concert` draft.
- **A dismissal is permanent.** The sweep never raises a dismissed lead
  again, and nothing in the app un-dismisses one. A production you are not
  sure about belongs in neither file -- not the prune list on a guess, not
  the batch on a guess -- leave it for the next pass over the queue.
