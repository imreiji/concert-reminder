# Calendar-feed discovery, and the character sweep rule

Date: 2026-08-02. Status: approved by the owner (feed-noise defaults accepted,
manual per-tag button stays working on characters).

## Why

The owner is expanding the tag catalogue by ~90 character and seiyuu tags
(765PRO ALLSTARS, Shiny Colors, the Love Live groups). Under the current
design every tag carrying an `eventernote_url` joins the daily sweep, so the
expansion threatened to add hundreds of daily fetches against a third party.
Investigation found both fan communities already publish forward-looking
Google Calendars of exactly the right thing:

- **imas-db.jp links maruamyu's アイマス関連イベント チケット申込期限
  calendar** — im@s ticket application deadlines, public `.ics`, verified
  live on 2026-08-02.
- **LL-Fans maintains a Love Live calendar family** — a main calendar plus
  per-series subs (Aqours, Nijigasaki, Liella!, Musical, 蓮ノ空, イキヅライブ,
  ラブカ), team-maintained, covering events and 申込期限. Public Google
  Calendars; the `.ics` URL is derivable from each calendar id (verified for
  the main one).

A handful of daily `.ics` fetches covers both franchises' events, versus ~300
per-actor pages — and iCal is a structured, stable format that a site
redesign cannot break the way an HTML change breaks a scraper.

Two sources they explicitly are NOT: `ll-fans.jp/data/event` and
`imas-db.jp/song/event/` are retrospective setlist archives (their own
descriptions say 過去; imas-db's newest entry on 2026-08-02 was July 26).
They document shows after they happen; discovery needs events before their
deadlines close. They remain good research references for the add-concert
drafting step, which needs no code.

## Owner decisions

- Calendar discovery ships BEFORE the tag expansion (reprioritized
  2026-08-02).
- **The daily sweep never fetches a CHARACTER tag's Eventernote page**,
  whatever URL it carries. The URL stays storable and rendered (it is still
  the right link for a character); the manual per-tag check button keeps
  working — one deliberate fetch is not a daily cost. This reverses the
  documented "the sweep is kind-blind" decision, on load grounds; CLAUDE.md's
  paragraph must be rewritten to record the reversal.
- Noise-filter defaults accepted as proposed (see §4).
- Volunteer-maintained feeds are understood to be a first-line source, not a
  guarantee (imas-db's previous main event calendar shut down 2025-03):
  Eventernote remains the backstop and the only source for people outside
  these franchises.

## 1. Feed configuration

`app/calendars.py` (sibling of `discovery.py`, same layer: imports domain/
and db.service, nothing in db/ imports it) holds a module-level tuple of
frozen `CalendarFeed` dataclasses:

- `key`: short ASCII id, unique, used as the lead-id namespace (`imas-tix`,
  `ll-main`, `ll-aqours`, ...).
- `label`: what /admin/discoveries and the DM show as the lead's source
  ("imas 申込期限", "LL-Fans Aqours", ...).
- `url`: the public `.ics` URL.
- `dates_are`: `"deadline" | "event"` — what this feed's DTSTART means.
  The imas ticket calendar is deadlines; LL-Fans feeds are event dates.
- `include_prefixes`: tuple of SUMMARY prefixes that count as leads; empty
  means take every VEVENT (right for the imas feed, which is single-purpose).

Code-level config, not a table or env var, deliberately: the set changes
rarely, changing it is an edit+deploy exactly like the admin whitelist, and
each entry carries typed fields no env CSV expresses well.

**The launch set is decided at build time, feed by feed**: candidate feeds
are the imas ticket calendar plus LL-Fans main and its per-series subs, and
each is probed before inclusion — my probe of the LL main feed showed stale
entries, so a feed that turns out dead or empty simply does not make the
table. The build must record (in the plan's report or the module's comments)
which candidates were dropped and why.

## 2. The ics reader

`domain/ics_read.py` — pure, hand-rolled, no new dependency. Unfold RFC 5545
continuation lines, walk `BEGIN:VEVENT`/`END:VEVENT` blocks, extract UID,
SUMMARY, DTSTART (`VALUE=DATE` or datetime; feeds are Asia/Tokyo wall time —
the result is a JST calendar date, same semantics as
`DiscoveredEvent.event_date`), and LOCATION when present. Warnings over
failures, per this repo's parser philosophy: a VEVENT missing UID or DTSTART
or SUMMARY is skipped and counted; only a body with no readable structure at
all raises. A parse never raises on content oddities — unescaping is limited
to the RFC's `\n` `\,` `\;` `\\`.

## 3. Storage: leads from any source

`discovered_events` changes by migration:

- `eventernote_event_id` RENAMED to `source_event_id`, widened String(20) →
  String(200). Calendar rows store `"<feed key>:<UID>"`; Eventernote rows
  keep their bare numeric ids. The prefix guarantees no cross-source
  collision, so the single-column UNIQUE stays.
- New `source: String(40)`, server default `"eventernote"` (backfills every
  existing row correctly). For calendar rows it is the feed key. Explicit
  rather than derived-from-prefix so queries and display never parse ids.
- New `date_is_deadline: Boolean`, server default false. True when the
  source feed's `dates_are == "deadline"`: the imas feed's DTSTART is an
  APPLICATION DEADLINE, not a performance date, and rendering it as "event
  on 8/15" would mislead the person triaging. `/admin/discoveries` and the
  DM render such rows as "申込締切 {date}" instead of the plain date.

`ConcertDay.eventernote_event_id` keeps its name — it genuinely is
Eventernote-specific. Calendar leads never exact-match legs (branch 1);
they get the date+venue hint only when a LOCATION existed.

`record_discovered` learns the new fields; its event-id dedup is unchanged
in kind — one row per (namespaced) event id however many feeds or tags list
it, `last_seen_at` refreshed on re-sighting.

## 4. One sweep, one digest

The scheduler's existing `discovery_due` branch gains a calendar pass that
runs FIRST (it is cheap), then the Eventernote actor loop, and both pour
into the same `record_discovered` call and the same single DM digest:

- Fetches go through the shared host-pinned guard (`app/fetching.py`) with
  `calendar.google.com` as the caller's allowed host, the discovery
  User-Agent, a short politeness pause between feeds, and the same 30s
  total per-fetch deadline. `heartbeat.beat()` per feed, like per artist.
- No cursor or budget machinery: ~9 bounded fetches need neither. A feed
  that fails to fetch or parse is counted failed and skipped — one feed
  must not cost the others, and never aborts the Eventernote half.
- Filtering at the feed boundary, per feed: a VEVENT counts as a lead only
  if `include_prefixes` is empty or its SUMMARY starts with one of them.
  Launch defaults (owner-approved): include ライブ／イベント／締切・申込-
  flavored prefixes on LL-Fans feeds; drop 誕生日・BD・CD・放送・配信
  entries. The imas feed takes everything (single-purpose by construction).
- The digest's `Lead` carries the deadline flag and the source label; the
  copy block's paste-ready half stays intact so triage-leads keeps working
  on mixed batches. `/admin/discoveries` shows the feed label where it
  shows the surfacing artist today.
- `stamp_discovery_run` stays ONE clock covering the whole pass —
  calendar counts fold into the same fetched/failed totals (the report
  distinguishes them in its log line only).
- The manual full-sweep button (`POST /admin/discoveries/sweep`) therefore
  includes feeds automatically; no new button.

## 5. The character rule

`run_sweep`'s tag query adds `Tag.kind != TagKind.CHARACTER`. Nothing else
changes: `EVENTERNOTE_KINDS` keeps CHARACTER (the editor field stays), the
Tags page keeps rendering the link, and `sweep_one_tag` (the manual button)
deliberately does NOT filter — the owner pressing it is consent. The six
Gakumas characters currently carrying URLs silently leave the daily
rotation; their events are im@s events, which is what the imas feed now
covers.

## 6. Out of scope, on purpose

- A feed admin UI or DB-backed feed table.
- Auto-creating anything from a calendar entry. A fan-maintained deadline is
  a POINTER; triage and add-concert still verify every round against the
  official ticket page (the skill's no-invented-deadlines rule).
- Any reminder/notification semantics change. The calendar pass writes
  leads and queues the one existing digest kind, nothing else.
- Per-feed health monitoring beyond the failed-fetch count in the digest
  line. Revisit if feeds rot silently in practice.

## 7. Tests

- ics reader: line unfolding, DATE vs datetime DTSTART, missing-field VEVENT
  skips-and-counts, escaping, an unreadable body raises.
- Feed filtering: include_prefixes honored; empty means all; deadline flag
  set from `dates_are`.
- Dedup: namespaced calendar id and Eventernote id never collide; the same
  UID re-sighted refreshes `last_seen_at` rather than duplicating.
- Migration: rename+widen+two new columns on a metadata-shaped fixture
  (`discovered_events` post-dates the naming convention, so no legacy-DDL
  fixture is needed — say so in the migration test), existing rows read back
  with `source="eventernote"`.
- Sweep: calendar pass runs before actors, one digest for both, a failing
  feed skips, characters excluded from the daily tag query, manual per-tag
  button still works on a character.
- Rendering: /admin/discoveries and the DM show 申込締切 for deadline rows
  and the feed label as the source.
