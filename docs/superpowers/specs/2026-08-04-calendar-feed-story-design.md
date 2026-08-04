# The calendar story becomes the feed — design

Date: 2026-08-04. Owner rulings in this document were given in the design
discussion the same day; the originating WISHLIST entry is #1, "The calendar
story should be the feed, not per-round files" (raised 2026-08-04 from owner
usage pain, four gaps confirmed at filing).

## Problem

The concert page's only calendar affordance is a per-round `.ics` DOWNLOAD
(`GET /rounds/{id}/ics`, one 📅 per round row) — keeping a calendar current
means importing a new file per round, forever, and a downloaded file is a
snapshot that rots the moment a deadline moves (invariant 2 re-plans the
queue; nothing re-plans a file in somebody's calendar app). Meanwhile the
right mechanism already shipped and is invisible: `POST /me/calendar-feed`
mints a token and `GET /calendar/{token}.ics` is a live subscription — but
the owner did not know it existed, and when he found it, its content
surprised him: it carries only deadlines his reminder RULES cover, so a
sparse preset makes a sparse calendar, and show dates appear only via an
`event_start` rule.

## Rulings (owner, 2026-08-04)

1. **The per-round download buttons are REPLACED by a subscribe affordance**,
   not supplemented. The route, the button and the single-event builder go.
2. **Feed content: shows + live deadlines** (the "landscape" option): every
   tracked concert's show dates plus every future deadline on rounds that
   still concern the user — independent of which reminder rules they set.
   Reminder rules go back to meaning exactly one thing: when Discord DMs you.
3. **Deadline granularity: standing-aware.** Only the moments that need the
   user NEXT (see the derivation below), not all four timestamps of every
   round.

One terminology trap, restated from the WISHLIST entry so nobody builds it:
the owner said "caldav", but CalDAV the protocol is two-way sync and nothing
here needs it. The shipped feed is already the right protocol shape — one-way
`.ics` over HTTPS — and `webcal://` is just that URL with a scheme that makes
calendar apps subscribe instead of import. This build is UX and content, not
protocol.

## Part 1 — Content: `user_calendar_events` becomes a standing-aware landscape

`user_calendar_events` (db/service.py) is REWRITTEN. It stops reading
`reminder_queue` entirely and derives from the user's standing over their
tracked concerts, through the same shared per-user helpers every other read
surface uses — `tracked_concert_ids`, `covered_round_ids` /
`covered_round_ids_by_concert`, `user_opted_out_day_ids`,
`_round_fully_opted_out`, `is_round_cancelled` / `all_legs_cancelled`, and
upgrade eligibility (`_qualifiers_by_upgrade_round` + `is_upgrade_eligible`).
No new suppression rule is invented anywhere in this build.

The events, all future-only (a moment `<= now` is left off):

- **Show dates.** One event per LIVE leg (not cancelled, not opted out by
  this user) of every tracked, non-dead concert, at the leg's
  `starts_at_utc`. Summary: concert title — leg label.
- **Deadlines.** Per round on a tracked concert, the round first passes the
  suppression sieve: dropped when its concert is dead
  (`all_legs_cancelled`), when `is_round_cancelled`, when
  `_round_fully_opted_out` for this user, when covered (every leg it sells
  secured through another round), or when it is an UPGRADE round the user is
  not eligible for. A surviving round contributes by the user's outcome on
  it:
  - **no outcome** → its future `opens_at_utc` and its future
    `closes_at_utc` (either, both, or neither — whichever are set and
    future);
  - **APPLIED** → its future `_result_moment` (the announced results time,
    falling back to the close — the same one rule `capture_gates` and the
    catch-up dialog already use for "when does the result become
    knowable");
  - **WON** → its future `payment_deadline_at_utc`, if set;
  - **LOST / NOT_APPLIED / PAID** → nothing. A LOST round's auto-armed next
    round is an ordinary no-outcome round and contributes its own
    opens/closes, so the ladder stays visible through the round that is
    actually next.

Per-leg partial outcomes need no special case: the whole-round `outcome` is
what selects the anchor, exactly as Home's rows already behave.

**`CalendarEvent` gains a required `anchor: Anchor`** (show events carry
`Anchor.EVENT_START`). A no-outcome round can now emit TWO
events with the same summary, so the rendered summary carries a short anchor
qualifier — otherwise "opens" and "apply by" are indistinguishable entries on
somebody's phone:

- The `.ics` feed stays CANONICAL (locale `None`, the standing ruling: a URL
  has no viewer) and qualifies with the Japanese ticketing terms the domain
  already speaks: 受付開始 (opens), 申込締切 (closes), 当落発表 (results),
  支払期限 (payment); show events get no qualifier. A small module-level map
  beside the composition site — plain data, no gettext, because canonical
  text is by definition untranslated.
- `/mydeadlines` (the Discord cog) passes the recipient's language as it
  already does, and qualifies through `_()` msgids added to BOTH catalogues.

**Consequence, accepted in design: `/mydeadlines` inherits the landscape.**
It reads the same function, so the Discord command's answer changes from
rule-derived to standing-derived. This is deliberate — one derivation, and
"my deadlines" answering from actual standing is strictly more useful — but
it is a behavior change to an existing command and its tests move with it.

**What deliberately does NOT change:** the locale parameter contract
(feed canonical, cog localized); `build_calendar` (the multi-event builder);
the token scheme (invariant 5's secret-link shape, hash-only storage,
regeneration as recovery); the privacy page's description, which is still
accurate. No schema change, no migration.

## Part 2 — UX: one subscribe affordance, three surfaces, one partial

**Deletions.** The 📅 per-round link in `_round_rows.html`, the
`GET /rounds/{id}/ics` route in `web/routes/concerts.py`, and
`domain/ics_export.py`'s now-orphaned single-event `build_ics`
(`_vevent_lines`/`_uid`/`_escape`/`_stamp` stay — `build_calendar` renders
through them). A 404 test pins the route's absence.

**The concert page** (login-gated, so there is no signed-out state) gets ONE
"📅 Calendar" action in the header's action row beside Edit/Export — a
regular action, never in the kebab (destructive-only, per the standing UI
rule). It opens a native `<dialog>` (bottom sheet on phone via the existing
mobile section; backdrop-close comes ONLY from base.html's global drag-safe
handler — the sweep test forbids local handlers). Server-rendered in one of
two states off `has_calendar_feed`:

- **No feed yet:** explanatory line plus one button, "Turn on my calendar
  feed", POSTing the existing mint route with `next` set to this concert's
  path. The user lands back on the concert page with `?feed_token=<raw>`;
  the page renders the dialog OPEN showing the URL exactly once via the
  shared partial below.
- **Feed exists:** the dialog says this concert's dates are already in the
  user's one subscription feed (they track it — that is why they are on
  this page seeing the button), and that the URL cannot be re-shown because
  only its hash is stored; it links to Preferences to REGENERATE (which
  stays where it is, behind its existing "old link stops working" confirm).
  Honest, not pretending: no URL appears in this state.

**The mint route's `next` handling** (`web/routes/calendar.py`): the
hardcoded `_ALLOWED_NEXT = {"/preferences", "/welcome"}` grows a third
admissible shape, a concert page path. The value passes
`domain/urls.py:safe_next` FIRST (same-origin path or None — the standing
open-redirect guard), then must be `/preferences`, `/welcome`, or start with
`/concerts/`. Anything else falls back to `/preferences`, as today.

**The shared partial `_feed_links.html`** renders a freshly-minted URL the
same way in all three places one is shown — the concert dialog, Preferences,
and welcome step 4: an "Open in calendar app" link on `webcal://` (the https
feed URL with its scheme swapped), the https URL itself in a copyable box,
and a copy button (the same clipboard pattern preferences.html already
uses). Preferences and welcome are refitted onto the partial so the
ergonomics cannot drift.

**Copy gets truthful**, in all three languages: welcome step 4 ("keep your
reminder deadlines updated") and Preferences' feed section describe the feed
as the landscape — your shows and the deadlines that still need you — not as
a mirror of reminder rules. Both catalogues updated for every new/changed
msgid; `test_i18n_catalogues.py` enforces it.

## Testing

- **Derivation** (service tests): one test per standing state (no outcome →
  opens+closes; APPLIED → result moment incl. the closes fallback; WON →
  payment; LOST/NOT_APPLIED/PAID → nothing); exclusions (cancelled leg/round,
  dead concert, fully-opted-out round, partially-opted-out round SURVIVES,
  covered round, ineligible upgrade, opted-out leg's show date); future-only;
  untracked concerts contribute nothing; the LOST→next-round handoff.
- **Feed route**: content matches the derivation; canonical (untranslated)
  labels; anchor qualifiers present; token semantics unchanged.
- **Mint route**: `next` validation (concert path honored, off-origin or
  weird values fall back to `/preferences`), token shown once via redirect.
- **Concert page**: render test for each dialog state; the 📅-per-round link
  gone; `GET /rounds/{id}/ics` → 404.
- **`/mydeadlines` cog**: tests updated to the landscape derivation.
- **i18n**: catalogue test green; welcome/preferences render tests updated
  for the new copy.

## Out of scope, recorded

- Any feed content beyond the landscape above (e.g. per-user include/exclude
  toggles, per-concert feeds) — nothing asked for them.
- CalDAV, VTODO/VALARM emission, event durations (deadlines stay
  zero-duration points; a show event stays a point at its start).
- Changing the token/regeneration model in any way.
- Minute-level offsets (WISHLIST #2) — adjacent, untouched, and this build
  makes an FCFS "opens" moment visible on the calendar regardless.
