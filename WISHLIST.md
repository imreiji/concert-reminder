# WISHLIST.md

Every potential feature raised in roadmap and UX discussions, ordered by
user impact (highest first). Maintained per the "Feature wishlist" section
in CLAUDE.md: fully re-evaluated and re-ranked every time a feature ships.
Each entry notes impact and effort so re-ranking has a basis. Shipped and
rejected ideas move to the bottom sections instead of being deleted.

## Proposed (highest impact first)


### 1. Minute-level reminder offsets

Impact: medium (raised from low) - effort: small. Raised: 2026-07-18
(domain-model review discussion). Re-ranked 2026-07-19.

`ReminderRule`/`PresetItem` already support `offset_days` + `offset_hours`
(0-23); there's no `offset_minutes`. The 60s scheduler tick already
delivers at ~1-minute granularity, so the gap is purely the data model +
form UI, not scheduling precision.

Re-ranked up because the condition the owner attached to it has now
fired: the entry said "revisit if/when FCFS-style rounds get their own
flag", and `RoundKind.FCFS_SALE` shipped on 2026-07-18. First-come-
first-served sales are exactly the case where "remind me 5 minutes before
it opens" beats "remind me 3 hours before", so this is no longer
speculative - it just has no user complaints behind it yet.

### 2. Upgrade rounds and their qualifying-round set

Impact: medium - effort: medium. Raised: 2026-07-19 (concert page and
editor, out-of-scope list).

Some rounds only exist for people who already got something out of an
earlier one - an upgrade or a seat-improvement round whose entry condition
is "won round 1". The model has no way to say that: `Round` carries no
relationship to another round, so the editor cannot express the dependency
and the reminder planner cannot act on it. Today an upgrade round pings
everyone tracking the concert, including people who never applied to the
qualifying round and cannot enter this one.

`RoundOutcome` already knows who won what, so the suppression half is a
short extension of `_apply_outcome_suppression` (invariant 2's per-user
pass) once the qualifying set exists. The cost is the schema and the editor
UI for it, not the logic. Held out of the concert page and editor branch
deliberately rather than half-built.

### 3. Collapse a round's multiple "Coming up" rows into one

Impact: medium - effort: medium. Raised: 2026-07-19 (Home/Discover split,
branch review). Re-ranked 2026-07-19.

`upcoming_deadlines` emits one row per future anchor, so a single round
with opens/closes/results/payment ahead of it takes up to four of Home's
ten "Coming up" slots, all naming the same round. The correctness half of
this (each of those rows offering its own independent capture buttons,
including on a round nobody could have entered yet) was fixed on
2026-07-19 by gating capture on `DeadlineRow.can_capture`; what remains is
purely the row budget. Deferred rather than done because collapsing
changes the shape of the fragment that `POST /rounds/{id}/outcome` swaps
back in via htmx, and the no-buttons gate already removed the harm.

Kept at medium rather than raised, but it is now cheaper than it was: the
concert page's per-leg round rows (2026-07-19) already render one row per
ROUND with a single primary anchor chosen by `_primary_anchor`, so the
collapsed shape exists and has tests behind it. What remains is deciding
whether Home wants the same rule and re-pointing the htmx swap at it.

### 4. Let the creation form express a multi-leg round in one pass

Impact: low - effort: small. Raised: 2026-07-19 (concert page and editor,
branch review).

The edit page now assigns legs with real ids via toggle chips, so a round
covering several legs says so directly. Creation cannot: the form renders
before any `ConcertDay` exists, so there is nothing to put an id on, and
its leg control is a single-value `<select>` matched server-side by
`resolve_round_leg`. That helper returns EVERY day matching the string, so
a multi-leg round is expressible in principle - but only when two legs
happen to share the one label-or-city text it matched on, and labels are
normally distinct per leg. In practice a genuinely multi-leg round has to
be created, then edited.

Low impact because the follow-up edit is one click from the redirect
creation already lands on, and multi-leg rounds are the minority shape.
The fix is a client-side version of the same chip control the editor uses,
keyed on `day_key` (which already exists) instead of on ids.

### 5. Eventernote links on performer chips

Impact: low - effort: small. Raised: 2026-07-19 (concert page and editor,
out-of-scope list).

The redesigned concert page leads with lineage and performers, and every
performer is a chip - but the chips are inert, because linking one out to
its eventernote page needs an `eventernote_url` column on `Tag` that does
not exist yet. Concerts already carry one; tags do not. Small and
self-contained, and it makes the page's new headline block do more than
name people.

## Shipped

### Concert subscriptions and per-leg opt-out (2026-07-19)

Shipped as: `ConcertSubscription` (state `subscribed`/`opted_out`) and
`LegOptOut`, both OVERRIDE tables -- no row means follow the tag-derived
default, so no backfill (invariant 8). `tracked_concert_ids` now applies
the override; per-leg opt-out folds into `_apply_outcome_suppression`;
writes re-sync via `reinstate_user_rules`. The concert page's Following
toggle and per-leg opt-out, Home's real "skip this concert", and a
rebuilt Preferences (left rail, a Following section with the pruned count
and restore) all became real instead of placeholders. Prunes stick across
re-follow; opting out of a won ticket needs a heavy confirmation and never
deletes the outcome.

### Concert page and editor redesign (2026-07-19)

Shipped as: two surfaces rebuilt around the same fact, that a round belongs
to a set of LEGS. The concert page (`templates/concert_detail.html` +
`_round_rows.html`) now leads with lineage and performers rather than a
metadata header, and replaces the single rounds table with one round-row
group per leg plus an all-legs group, grouped by `service.concert_round_rows`
(cancelled legs included, a round covering every live leg deliberately
landing in the all-legs group rather than being repeated under each). Rounds
on that page carry capture buttons via the `_capture_actions.html` macro
shared with Home's rows, so `POST /rounds/{id}/outcome` now answers two
surfaces - it reads which one from `HX-Current-URL` and sends the concert
page its own rounds region instead of Home's three fragments.

The editor (`templates/concert_edit.html`) puts rounds first, folds the rest,
turns cancelled into a per-leg toggle, and moves duplicate/delete into a
danger row that states what duplicate copies (invariant 3: the pruned tag
set, `expand=False`, no rounds or legs).

The data-loss fix underneath: the editor used to express `applies_to` as one
free-text string per round, matched server-side against each day's city or
label, and pre-filled that box from `applies_to[0]` alone - so opening a
two-leg round and saving it untouched silently narrowed it to one leg. Legs
are now toggle chips submitting real ids, encoded as ONE `round_legs` field
per round row (a flat repeated field could not say which row an id belonged
to). A leg added in the same save has no id yet, so its chip carries the
row's client-generated `day_key`, resolved after the flush.

Branch review on 2026-07-19 then closed a second, larger copy of the same
bug before merge: `round_legs` was end-padded unconditionally, so a browser
still holding the pre-deploy edit page - which posts the old `round_leg`
field and no `round_legs` at all - would have had one Save wipe `applies_to`
on every round of the concert. A whole-array omission now preserves each
round's existing legs; a partial array raises rather than sliding every
later row's selection by one.

### Web-side won/lost for a round with nothing left ahead of it (2026-07-19)

Was proposed on 2026-07-19 (Home/Discover split review) and shipped the same
day by the concert page above, which took the first of the two options it
named: a capture surface on the concert page itself, rather than a grace
window on Home.

The gap was that capture lived only on Home's "Coming up" rows, and a row
exists only while the round has a FUTURE anchor - so a round whose results
had passed with no payment deadline behind it dropped off Home entirely,
stranding anyone sitting on APPLIED with no web-side way to say how it went.
The concert page lists EVERY round of the concert regardless of timing and
runs the same `capture_gates`, so `can_report_result` is true there exactly
when it should be. Nothing was relaxed in `record_round_outcome` to get it
(the gates stay on the read side), and no second write path was added.

### Home and Discover split (2026-07-19)

Shipped as: the old combined index page cut in two by the question each
answers. `/` (`templates/home.html`, handler in `web/app.py`) is Home -
personal, login-gated, four blocks: Up next, the four-column campaign board
(`domain/board.py`'s `column_for` plus `service.board_cards`), Coming up
(`service.my_deadline_rows`, the page's only capture surface), and a teaser
out to Discover. `/discover` (`web/routes/discover.py` +
`templates/discover.html`) is the catalogue - and is public, the only
content page an anonymous visitor can reach - with a round-status facet, a
next-deadline sort, and one merged status pill per card
(`service.discover_statuses`). Capture actions post to
`web/routes/outcomes.py`, which shares `record_round_outcome` with the DM
buttons and swaps three fragments back (the rows, plus the board and its
tally out-of-band).

Branch review on 2026-07-19 then fixed six things before merge: the top
block claimed "Closes next" while showing an open (now "Up next", honest
about which moment it names); every future anchor of one round offered its
own capture buttons, so a user could record APPLIED against a round that
had not opened (now gated on `can_capture`); WON and LOST existed only as
Discord buttons, leaving a `dm_blocked` user or a `bot_enabled=False`
deploy with a four-column board the web could only drive two columns of
(now offered on an APPLIED row once its result moment lands); the teaser
counted every Concert row rather than what `/discover` lists;
`tracked_concert_ids` ran twice per render; and the docs were behind.

### Public terms of service page (2026-07-19)

Shipped as: `GET /terms`, the counterpart to `/privacy` — same shape
(`web/routes/terms.py` + `templates/terms.html`, `current_user` not
`require_user` so Discord's reviewers can read it signed out), filling the
Terms of Service URL field the Developer Portal offers alongside the
Privacy Policy URL. The clause that actually matters is the disclaimer of
warranty: the service tracks real ticketing deadlines and cannot promise a
DM arrives, arrives on time, or that the date is right, so it says so
plainly and puts confirmation with the official source on the user. Also
covers non-affiliation (the app is entirely about other people's events),
Discord's own minimum age rather than a number, acceptable use, editor
contributions staying in the shared catalogue after erasure (matching the
privacy policy and `delete_user`), availability/shutdown, and New Brunswick
governing law. Contacts reuse the existing `PRIVACY_CONTACT_*` settings via
a new shared `web/contact.py`; the two pages cross-link.

### Public privacy policy page (2026-07-19)

Shipped as: `GET /privacy`, a public (deliberately no `require_user`)
long-form policy page in `web/routes/privacy.py` + `templates/privacy.html`,
linked from the `base.html` footer. Required by Discord's Developer Terms
for any app collecting user data, and mandatory at bot verification /
100+ guilds; also covers the GDPR transparency obligation. Content is
written against the actual schema rather than boilerplate, and documents
the erasure semantics the `1384cadd692e` migration enables (personal data
deleted, contributed catalogue kept with the author anonymised). The
operator's contact handle and email come from two new blank-by-default
settings (`PRIVACY_CONTACT_DISCORD` / `PRIVACY_CONTACT_EMAIL`) so real
contact details never enter the repo; the page renders with both, one, or
neither configured.

### First-run guided setup (2026-07-18)

Shipped as: a `/welcome` wizard route that sequences new logins through
tag subscriptions (follow artists), default reminder preset selection,
timezone confirmation, a test DM to verify delivery, and calendar feed
subscription. Backed by an `onboarding_step` column on `User` to track
progress, a new-user redirect in the OAuth callback, and a `next`
redirect parameter added to the five existing routes the wizard reuses
(concerts, presets, tags, preferences, calendar) to stitch them together
into one seamless flow.

### Corrected and expanded round-kind taxonomy (2026-07-18)

Shipped as: two new `RoundKind` enum values, `FCFS_SALE` and `TOUR_PACKAGE`,
splitting out first-come-first-served sales from the previously-conflated
general-sale kind and adding support for overseas tour package sales. A
centralized `LABEL_BY_ROUND_KIND` dict in `db/service.py` now provides
display labels for all nine kinds (replacing six inline label derivations
scattered across templates), with corrected emoji assignments (removing
the racing emoji misapplied to `GENERAL_SALE`, adding the appropriate
emoji for the two new kinds). Also updated `ingest.py`'s ramen.events
keyword table to recognize FCFS and tour-package keywords in HTML parsing.

### Per-round personal lottery outcome tracking (applied / won / lost / paid) (2026-07-18)

Shipped as: state-aware DM buttons (Applied/Not applied, Won/Lost, Paid)
on the reminder embed, backed by a new `RoundOutcome` table and
`record_round_outcome`/`_apply_outcome_suppression` in `db/service.py` —
losing suppresses that round's payment reminder and arms the next
round's opens reminder; winning skips the general-sale ping. Also
generalized `snooze_reminder` to accept a `days` parameter and added a
"Remind me later" modal-driven button (replacing the plain 24h snooze)
on the CLOSES-anchor reminder specifically, plus relabeled that anchor's
link button to "Apply here".

### Free-text search matches artists, groups, and venues (2026-07-18)

Shipped as: a centralized `concert_search_text` helper (`web/app.py`) that
is now the single source of truth for what search matches — title,
title_en, every attached tag's name across all four kinds
(franchise/group/artist/venue), and a free-text `Concert.venue` fallback
only when no VENUE tag is attached. Used at all three call sites: the
server-side `matches_query` fallback, the tile grid's `data-search`
attribute, and the "Coming up soon" deadline list's `data-search`
attribute (previously title-only).

### Surface undeliverable DMs to the user (2026-07-18)

Shipped as: a sitewide `dm_blocked` banner (driven by `SessionUser` /
`users.dm_blocked_since`, set by the scheduler's delivery-outcome
tracking) plus a synchronous `POST /me/test-dm` diagnostic route and
"Send test DM" button on the preferences page.

## Rejected

### Daily digest mode (rejected 2026-07-18)

Was: an opt-in "one morning DM listing everything due" to reduce
per-deadline ping fatigue for multi-subscription users. Rejected by the
owner during design review — not worth the scheduling complexity it
would have needed (a per-user local-morning gate layered onto
`due_reminders()`/`tick()`) for a noise problem the owner doesn't
consider significant enough to solve right now.
