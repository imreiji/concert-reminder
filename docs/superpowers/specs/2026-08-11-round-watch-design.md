# Round watch: the quiet-ladders surface

**Status:** design, approved by the owner 2026-08-11, section by section.
**Implements:** WISHLIST #2 ("Nothing re-checks a tracked concert for newly
opened rounds"), the cheapest of the three shapes that entry records -- the
"quiet ladders" admin surface. The other two shapes (teaching the discovery
matcher a round-gap dimension; a scheduled re-fetch of each concert's own
official URL) are NOT built here and stay on the list.
**Owner rulings, 2026-08-11:** a worklist, not an autonomous re-fetch; a
re-checked stamp rather than a dismissal; a copy block AND per-row links; a DM
when a concert newly goes quiet.

## The problem

Discovery's sweep answers "what exists that you are not tracking". Nothing
answers "what changed about what you already track". A round announced after a
concert is imported is invisible: no sweep visits the concert's own pages, no
surface lists ladders that have gone quiet, and the reminder machinery can only
plan from rounds it has been given.

The failure is silent, and it is the app's core promise failing: a user who
followed the right artist, got the new-event DM, and still misses the lottery
because the round arrived after import. WISHLIST #2 records the evidence in
triplicate from the 2026-08-05 batch -- most sharply the 蓮ノ空 103期卒業公演,
whose missing アップグレード rounds surfaced only because fan-calendar leads
happened to name them.

The 2026-08-10 grounding change sharpened it once more. Phase 1 now keeps the
rounds it can quote from an Eventernote page, and some rounds exist ONLY there,
because an official page drops a round once it closes. A ladder assembled from
two sources that each forget different parts of it is exactly a ladder that
goes stale in a way only a re-check would notice.

## What this deliberately is not

**It does not fetch anything.** No third-party request, no new host policy, no
new trust decision. The whole feature is a query over the catalogue plus a
stamp. `ApprovedPublicHosts` and `/admin/fetch-domains` have since made the
heavyweight re-fetch shape *possible* (WISHLIST #2 records that its blocker is
gone), and it is still not what this builds. Visibility first: converting the
failure from silent to visible is most of the value, and it is the half that
needs no judgment about arbitrary ticket pages.

**It does not write rounds.** There is no update path back into a concert --
import answers 409 for a concert that already exists (invariant 6) -- so
whatever a re-check finds is typed into the concert's edit page by a human.
That is the same shape `/admin/discoveries` already has: it writes one thing
(`dismissed_at`) and never creates a concert.

**It does not hide anything.** The re-checked stamp sorts and dims; it never
removes a row. A concert dismissed in March genuinely does grow a 一般発売 in
July, and permanent dismissal is the one shape that would hide it.

## The predicate

One definition, in one place, `db/quiet_ladders.py`:

> A concert is on the list when it is not dead, its last live leg is in the
> future OR it has no legs at all, and no round of it holds a future moment.

Formally, against `now`:

    not all_legs_cancelled(days)
    and (no live leg exists or the latest live leg is in the future)
    and next_anchor_at(concert, now) is None

**The third clause reuses the shipped signal literally.** `_next_anchor_iso`
(`db/core.py:3790`) already computes the catalogue-level "earliest future
moment among live rounds", returning None for precisely this condition, and the
agent read API already serves it as `next_anchor_at`. It is promoted to
`next_anchor_at(concert, now) -> datetime | None`, with the ISO version
becoming a one-line wrapper over it. Two definitions of "future anchor" that
could drift is the bug factory this avoids -- the page and the API answer
identically by construction, and the predicate test that pins it is the one
asserting a concert WITH a future anchor is absent from the list.

**Why dateless concerts are in.** `all_legs_cancelled` is documented as "the
concert HAS legs and every one is cancelled", so a dateless draft is not dead --
the same exemption the SQL half (`discoverable_concert_criterion`) makes. That
is deliberate here too: ブシロード20周年記念ライブ, imported with no dates because
its page says 出演日程やチケットの詳細は後日発表, is the canonical case this
feature exists for -- a concert with no `ConcertDay` rows at all, which is also
what `duplicate_concert` produces.

**"Dateless" means NO LEGS, not an undated leg** -- corrected 2026-08-11, after
the first draft of this spec ruled on a state the schema forbids.
`ConcertDay.starts_at_utc` compiles to `DATETIME NOT NULL`, so a leg always
carries a date and a concert cannot hold a mix of dated and undated ones. The
earlier draft's "dated legs decide when a concert has both" ruling was
therefore unreachable, and the test written for it would have asserted
something impossible.

What the second clause actually distinguishes is a concert with ZERO
`ConcertDay` rows -- which is exactly what a skeleton import and
`duplicate_concert` produce, and exactly the ブシロード20周年記念ライブ case --
from one whose legs have all been performed.

**Why past concerts fall off by themselves.** A ladder on a concert that has
already happened is finished, not quiet. Nothing needs to expire it: the leg
clause stops matching the day after the show, so the list drains itself and
never accumulates.

**The whole predicate runs in Python, over one unfiltered scan.** Candidates
come from a plain `select(Concert)` with no WHERE clause; the leg/cancellation
checks and the anchor clause (`next_anchor_at`, which is Python because
`is_round_cancelled` is) all run against the loaded rows. The catalogue is
~157 productions, so a scan is cheap and honest -- cheaper than a SQL
transliteration of a Python predicate that would then be free to drift from
it, and it keeps the predicate in exactly one place.

`db/quiet_ladders.py` is a feature module: it imports `core`, never the facade,
and its public names are re-exported from `db/service.py` or
`tests/test_service_facade.py` fails.

## The two stamps

Two nullable `UTCDateTime` columns on `concerts`, with different owners:

- **`quiet_since_utc`** -- system-owned. Set by the pass when a concert
  is on the list and the column is NULL. Cleared when it leaves. Never written
  by a human.
- **`ladder_rechecked_at_utc`** -- yours. Set by the Checked button. Never
  written by the pass.

They are two columns because they answer two questions -- "how long has this
been quiet" and "have I looked at it" -- and one column would have to lie about
one of them.

**Both belong to the CURRENT quiet spell.** When a concert leaves the list,
the pass clears both. A concert that goes quiet, gets checked, recovers a round
and later goes quiet again arrives unchecked, because the earlier check was
about a different question.

**`quiet_since_utc` means "first observed quiet", not "went quiet"**, and the
migration backfill is why: it stamps every already-quiet concert at deploy time
so the first pass announces nothing rather than DMing the entire back
catalogue. Under that name the backfilled value is honest; under the other it
would be false for every pre-existing row.

## The pass

**The page does not depend on this pass having run.** Membership is derived
live from the predicate on every page load, so the list is never stale. The
pass owns exactly the two things a query cannot: the entry stamp and the DM.
That split is what keeps a scheduler failure from making the page wrong, and it
is why there is no "run now" button -- unlike the sweep, there is nothing to
run.

It runs **every tick**, in `scheduler/loop.py`, after the discovery sweep block
and shaped like it: its own `try`/`except`, its own commit, failures logged
rather than raised. The tick's own comment carries the principle -- "the least
important operation in the tick must never be able to roll back the most
important one" -- and a bookkeeping pass sits well below reminder delivery.

**Every tick, and NOT on the sweep's 24-hour clock**, which is the one place
this deliberately departs from the pattern it otherwise copies. The sweep needs
that clock because its work is 86 third-party fetches ending in a DM: expensive,
rude to repeat, and not idempotent. This pass is a query and a diff over the
local catalogue, and it is **self-idempotent** -- once a newcomer is stamped it
is no longer a newcomer, so a re-run announces nothing. A clock would protect
nothing and would delay a notice by up to 24 hours for no gain. The stamp and
the queued notice commit in ONE transaction, which is what makes the notice
exactly-once: if the commit fails, neither happened, and the next tick retries
both.

It also means `quiet_since_utc` is accurate rather than approximate -- a
concert is stamped within a minute of going quiet, so the page's "since DATE"
column is a real measurement and not "the date the pass happened to notice".

A concert cannot oscillate on and off the list by itself; leaving requires
gaining a future anchor and returning requires losing one. An editor could flap
one by hand with repeated edits, which would re-announce. That is bounded, rare,
and preferable to suppressing a genuine second quiet spell.

Each run:

1. Compute today's list.
2. On the list with `quiet_since_utc` NULL -> stamp it. These are the newcomers.
3. Off the list with `quiet_since_utc` set -> clear it, and clear
   `ladder_rechecked_at_utc` with it.
4. Newcomers -> one notice.

## The notice

It mirrors `_record_and_announce` (`discovery.py:431`) point for point, because
that function already solved this exact problem for leads.

- **One digest per pass, not one per concert.** A day on which five concerts go
  quiet is one DM. The body is built by a pure `domain/quiet_ladder_message.py`
  with the same cap-and-shrink shape as `build_discovery_dm`, so the prose and
  the list always name the same concerts -- slicing at the caller is what let
  the two halves disagree there.
- **No newcomers, no DM**, and running every tick makes this load-bearing
  rather than merely tasteful. Discovery's comment states the reason at a
  daily cadence -- "a daily 'nothing found' trains the reader to ignore the
  channel" -- and at a per-minute cadence the same mistake would be 1,440 DMs a
  day. Silence is the normal output of this pass.
- `kind="quiet_ladder"`, `concert_id=NULL`. NULL already means "render the plain
  body, not a rich embed" and already makes `record_deliveries` skip the title
  lookup, so the drain needs no change. A digest naming several concerts could
  not be one concert's embed anyway.
- Queued for each id in `sorted(settings.admin_ids)`, the same audience as
  `ops_alert` and the discovery notice. `Notification.user_id` is an FK to
  `users.discord_id`, so an admin who has never signed in must be `ensure_user`d
  first -- but ONLY when `session.get(User, admin_id)` returns None, since
  `ensure_user` refreshes the username and would otherwise overwrite a real
  admin's name with the placeholder on every single run.
- **NOT in `UNREPORTED_NOTE_KINDS`** (owner-confirmed). That set is only for
  notices that REPORT ON deliveries; this one reports on the catalogue. It is an
  ordinary notice and belongs in `delivery_log` like any other -- the same call
  the Eventernote discovery notice makes, for the same reason.

## The page

`GET /admin/quiet-ladders`, admin-only, in a new
`web/routes/quiet_ladders.py`. Its own module and its own page rather than a
section of `/admin/discoveries`: that module's docstring argues for splitting on
exactly this line ("discovery is a fourth unrelated concern", and a router
registers whole). Discoveries answers "what exists that you are not tracking";
this answers "what changed about what you track".

English-only and not wrapped in `_()`, like `/admin/deliveries`,
`/admin/broadcast` and `/admin/discoveries`: an operational page only admins see
should not cost msgids in three languages, which
`tests/test_i18n_catalogues.py` would then demand. Only the Preferences LINK to
it is translated, which is the precedent `/admin/discoveries` already sets.

**Sort:** never-checked first, then longest-since-checked, tie-broken by
longest-quiet. Rows checked recently render dimmed, never hidden -- the stamp
answers "have I looked at this", and hiding would silently promote it to "is
this resolved", which it cannot answer.

**Per row:** title, leg dates (or "no dates announced"), the date it went
quiet (rendered "since DATE"), and the rounds the concert DOES carry with
their moments -- a concert
with a closed 最速先行 reads differently from one with nothing at all. Links out
to `official_url`, `eventernote_url` and the concert's edit page. Plus a
**Checked** button: `POST /admin/quiet-ladders/{event_id}/checked`, which sets
`ladder_rechecked_at_utc` and redirects 303, exactly as `dismiss_lead` does.

**The copy block** carries the whole list as text -- `event_id`, both titles, leg
dates, the three URLs, and the rounds already known. That last part is what
makes it useful to hand an agent: without it the agent re-proposes rounds the
catalogue already holds. Rendered as `data-copy="..."` and read via
`dataset.copy`, never interpolated into the `onclick` -- the browser
HTML-decodes an attribute before parsing it as JS, so Jinja's escaping does not
protect it (invariant 7). This mirrors `admin_discoveries.html:230`.

## Migration

Two nullable `UTCDateTime` columns on `concerts`, plus a backfill stamping
`quiet_since_utc` for every concert the predicate already matches.

- After autogenerate, replace `app.db.models.UTCDateTime()` with
  `sa.DateTime()` and delete the `import app.db.models` line.
- `concerts` is one of the LEGACY-shaped tables carrying anonymous constraints
  from before the naming convention. This migration only ADDS columns and
  touches no constraint, so the `drop_constraint` /
  `naming_convention=NAMING_CONVENTION` requirement does not apply. Recorded so
  that a later reader knows it was considered rather than missed.

## Testing

- **The predicate, case by case:** no legs at all and no rounds (on), future
  legs with every round closed (on), all legs past (off), all legs cancelled
  (off), a past leg beside a live future one (on -- the latest live leg
  decides), and a concert holding a future anchor (off). The last pins the reuse of
  `next_anchor_at` -- if this surface and the read API ever drift apart, it
  fails.
- **The pass:** stamps newcomers; **an immediate second run announces nothing**,
  which is the idempotency the per-tick cadence rests on and the test that must
  fail if someone reintroduces a clock instead; clears both stamps when a
  concert leaves; a concert that goes quiet, is checked, recovers and goes quiet
  again arrives unchecked.
- **The notice:** one digest naming several concerts; none at all when there are
  no newcomers; `ensure_user` called only for an admin with no row, and not for
  one who has signed in.
- **The page:** a logged-in admin GET render test (CLAUDE.md: a missing one
  shipped a 500 once), a non-admin 403, a signed-out redirect.
- **The facade:** `tests/test_service_facade.py` enforces the
  `db/quiet_ladders.py` exports with no new test.
- **The migration:** an already-quiet concert is stamped by the backfill, so the
  first pass after deploy is silent.

## Deferred

The other two shapes from WISHLIST #2 stay on the list and are unaffected by
this build: teaching the discovery matcher to flag a "round gap" when a calendar
lead names a round its matched concert lacks, and the scheduled re-fetch of each
concert's own official URL. Both remain worth doing; both answer a different
question than "which ladders have gone quiet", and neither is a prerequisite for
the other now that this surface exists to receive their output.
