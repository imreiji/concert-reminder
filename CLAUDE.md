# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## dekimasen.app (concert-reminder)

Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets). One Python process runs three things on a
single asyncio loop: discord.py bot, FastAPI web (Jinja2 + htmx), and a 60s
scheduler tick. SQLite + SQLAlchemy async + Alembic. Live at dekimasen.app
(AWS Lightsail behind Cloudflare). 2008 tests as of this writing (past the
Phase 12 roadmap in README.md — event_id/edit-page, venue regions, .ics
export, ramen.events import, a personal calendar-feed subscription,
free-text concert search, a personalized `/mydeadlines` Discord command, a
per-concert edit history, a per-leg cancelled status, a redesigned Tags
page (search, hierarchy, dialog-based editing, rename, retroactive
artist-to-active-events apply), an index-page reorg (open-and-upcoming
bucketing plus a global chronological deadline list), surfaced
undeliverable-DM feedback (a sitewide banner plus a synchronous test-DM
diagnostic), free-text search matching tag names (franchise/group/
artist/venue) and a free-text-venue fallback, per-round lottery outcome
tracking (applied/won/lost/paid, with automatic reminder suppression and
next-round auto-arming), a first-run guided setup wizard sequencing
tag subscriptions, a default preset, timezone confirmation, a test DM,
the calendar feed for brand-new logins, a corrected first-come-first-served
round kind (split out from the previously-conflated general-sale kind) and
a new overseas tour package round kind, both reflected in round-kind
labels/emoji and the ramen.events import heuristics, and a post-wizard
first-run capture flow — prune tag-implied concerts, record existing
applications, board reveal — at /setup, re-runnable from Preferences —
and a demo-reconciliation pass restoring the full design-token layer,
dark mode, and each view's lost components against the frozen concept
demo, and an onboarding build -- a real signed-out landing page, the
welcome wizard rebuilt on the design system and feeding into /setup,
import preview rebuilt in the day/round/leg vocabulary with real
multi-leg round binding, and retroactive-apply/privacy/terms reframed in
the design system -- and multi-language support (English/Mandarin/Japanese)
end to end: gettext catalogues, per-user + per-visitor locale resolution,
localized dates, and parallel-column UGC translation -- and a phone
retrofit (bottom tab bar, editor FAB, bottom-sheet dialogs, Discover's
filter sheet) confined to one `@media` section, and a signed-out
redirect replacing the old bare 401, returning the visitor to the page
they asked for once they log in, and the venue-to-tags move complete -- a
leg's venue is now a real VENUE tag FK, a concert's VENUE tags are derived
from its legs, venue city/address live on the tag, an editor can create a
venue without leaving the form, and the legacy free-text venue columns have
been dropped -- and a trilingual-concert-page arc on top of it: the import
preview's per-leg venue picker, leg/round label localization in the viewer's
language, a self-populating round-label phrase library (typed triples become
one-click suggestions, since real labels don't decompose into a taxonomy), and
all-three-languages-or-none variant enforcement (one pure rule, a 422 at create
boundaries with an inline browser-side block, edit-page gap notices, a
Tags-page untranslated count) -- and the agent-driven import seam (paste a
YAML draft, the preview arrives prefilled; the add-concert skill that
authors drafts is downloadable from the import page) and a native-review
i18n calibration applied across both catalogues, including a ja
agent-proofread round and the 132 reviewed English-source fixes applied
at the msgid layer (mapping preserved in
`docs/i18n-english-source-fixes-2026-07-24.csv`) -- and the editor
coherence pass: the three editor surfaces' leg/round cards share two
partials, destructive actions moved into a kebab menu, EN/中文 variants
got their own row, and the sentence-style reminder builders render
through locale-ordered slot patterns so ja/zh read grammatically -- and a
UX pass (20 changes across the board heads, Discover's active-filter chips
and live counts, the concert header's "Next for you" strip, the Tags
chips⇄table view, an htmx progress bar, and the two-shape callout grammar)
-- and a standing-at-a-glance arc: per-leg outcome truth (a round covering
Sat+Sun can come back won on one leg and lost on the other, and a round
whose legs you already hold stops asking), "Coming up" collapsed to one
block per concert, the board's ladder capped at the rungs that matter,
settled rounds folded per leg on the concert page, and performer chips
clustered by group -- and a dead concert (every leg cancelled) leaving
*Open now*, offering no capture and announcing nothing -- and the
operations trio: a `delivery_log` of every DM with `/admin/deliveries` and
a per-tick counts digest, a cancellable admin broadcast held 120s in the
outbox, and the flag-gated local rehearsal harness -- and real
403/404/422/500 pages (HTML for a navigation, the original JSON for an
XHR) with the admin pages indexed in Preferences -- and a correctness
sweep closing a born-dead concert's permanent "new event" DM and a
generated `event_id` taking a reserved word -- and Eventernote
discovery, a daily flag-gated sweep of every tag carrying an
`eventernote_url` that records performances the catalogue lacks, DMs
admins one digest ending in a paste-ready agent prompt, and reviews them
at `/admin/discoveries` -- and CHARACTER tags: an im@s bill credits
如月千早 rather than the 今井麻美 who voices her, so a character is a fifth
tag kind carrying `voiced_by_tag_id`, attaching her attaches her seiyuu
(which is what makes following the performer match the show), a group may
now sit inside a group as a subunit, and both new relationships are DRAWN
only when both of their ends are attached -- have shipped since).

## Commands

- Run everything: `uv run python -m app.main` (dev: leave `DISCORD_TOKEN`
  empty in `.env` → web-only mode, bot and scheduler DMs disabled)
- Bot dev: set `DEV_GUILD_ID` to your test server's ID — slash commands
  sync to that guild in seconds instead of the up-to-an-hour global sync
  (unset, the production default, keeps the global sync)
- Tests: `uv run pytest -q` — MUST pass before any commit
- Single test: `uv run pytest tests/test_service.py::test_name -q`
- Lint: `uv run ruff check .` — MUST be clean before any commit
- New migration: `uv run alembic revision --autogenerate -m "msg"`, then
  review it (see Migrations below), then `uv run alembic upgrade head`
- Catalogue update after adding/changing translatable strings:
  `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .` then
  `uv run pybabel update -i messages.pot -d src/app/translations -l ja`
  (and again with `-l zh`), fill in the new/fuzzy msgstrs by hand in both
  `.po` files, then delete `messages.pot` (gitignored, regenerable). The
  `.mo` files are never committed — `i18n.py` compiles `.po` to `.mo` in
  memory at first use, so there's no separate build step.
- CI (`.github/workflows/ci.yml`) runs `uv sync`, `ruff check .`, `pytest -q`
  on every push/PR to `main` — the same two gates as above, nothing extra.

## Layout

- `src/app/domain/` — pure logic, NO I/O, no discord/fastapi/sqlalchemy
  imports. Reminder math in `reminders.py`, JST↔UTC conversion in
  `timezones.py`, ramen.events HTML parsing in `ingest.py` (takes an HTML
  string, returns a draft — no httpx call itself), `.ics`/YAML export
  formatting in `ics_export.py`/`yaml_export.py`, and editor-supplied URL
  scheme validation in `urls.py` (`clean_url` normalizes an http(s) URL or
  raises `UnsafeURLError`; see invariant 7). `urls.py` also holds
  `safe_next`, the open-redirect guard on the post-login return path --
  same family, opposite direction (a same-origin PATH or None, never an
  absolute URL), and it returns None rather than raising, since a bad
  `next` is a stale link, not an editor mistake worth a 422.
  `tags_yaml.py` is the TAGS vocabulary and holds BOTH halves --
  `tags_to_yaml` and `parse_tags` in one module, deliberately. Splitting a
  format's serializer from its parser is how the catalogue round-trip hole
  opened: the concert export looked complete until something had to read it
  back, and only then did it turn out a tag had no identity to key on. Keep
  them together, and add fields to both at once. Its parser follows
  `parse_draft`'s philosophy -- warnings over failures, one bad row skipped and
  named, only an unusable file raises -- and `RESTORE_NOTES`, the text written
  into every export, lives here too because it documents the format.
  `eventernote.py` parses an actor's events page the way `ingest.py` parses a
  ramen.events page -- HTML string in, rows out, no httpx -- and skips-and-counts
  an unreadable row rather than raising, so a site redesign degrades to "found
  nothing" instead of crashing a scheduler tick every day. Its `future_events`
  is a TAKE-WHILE, not a filter, and that is the whole economy of discovery:
  rows are strictly newest-first (measured, and pinned by a test), so stopping
  at the first past row means ONE fetch per artist -- ~86 a sweep instead of the
  ~1,548 that reading all 18 pages of every artist would cost. A filter would
  be correct and eighteen times as expensive, so don't "simplify" it into one.
  An event dated TODAY counts as future. `actor_events_url` builds
  `/actors/<name>/<id>/events` from OUR name because **the name segment is
  DECORATIVE** -- `/actors/x/5847` resolves the same as the site's own path
  (verified against the live site) -- so only the id is identity, which is also
  why `actor_id_from_url` reads nothing else out of a stored URL. The site's own
  slug sometimes disagrees with the displayed name; that is fine and expected.
  `discovery_message.py` composes the discovery DM and is its own module for the
  reason `tags_yaml`/`tags_diff` are two: that one is about READING a source,
  this is about COMPOSING a message. The message is deliberately the same content
  TWICE -- a readable markdown list, then a fenced block carrying the same leads
  as a paste-ready agent prompt -- because Discord does not linkify inside a
  fence, so one half stays clickable and the other stays copyable. The 2000-char
  limit is a hard budget and **the block yields first**, saying so on its last
  line when lines are dropped; every free-text field is clipped and there is a
  final prose truncation floor, because past Discord's real cap discord.py
  raises and the WHOLE DM is lost rather than trimmed. `build_discovery_dm` takes
  `budget=None` for `/admin/discoveries`, which has no character limit and is
  where the DM's "+N more" points. Its `Lead.deadline` renders as an ADDITIVE
  `申込締切 ` prefix on the date and never reorders the line, because the
  `triage-leads` skill reads the copy block by field position; `Lead.source`
  gates the Eventernote LINK, since a calendar lead has no page behind it.
  `ics_read.py` is the RFC 5545 half of calendar discovery -- text in,
  `IcsEvent` rows out, no httpx, exactly as `eventernote.py` takes HTML.
  Hand-rolled rather than a new dependency (the feeds are Google Calendar
  exports and the app wants four fields per VEVENT), warnings over failures
  like every parser here: a VEVENT missing UID/SUMMARY/DTSTART is skipped and
  COUNTED, and only a body with no `BEGIN:VEVENT` structure at all raises
  `IcsError` -- so a feed that rots degrades to "found nothing" on the status
  line instead of crashing a scheduler tick every day. **It keeps only the
  DATE half of a DTSTART**: inventing a midnight instant would put a
  deadline-shaped fake into an aware-UTC schema (invariant 1), and a lead's
  date is a pointer, not a deadline -- which is also why the ≤1-day skew of a
  UTC-suffixed stamp at the JST boundary is accepted rather than corrected.
- `src/app/db/` — models, session, and `service.py` (all business logic that
  touches the DB; discord-free so it's testable).
- **Venues live on the LEG, as a tag.** `ConcertDay.venue_tag_id` (FK ->
  `tags.id`, ON DELETE SET NULL, indexed) is the structured venue and the ONLY
  one anything reads for display; SET NULL rather than CASCADE because a VENUE
  tag is shared taxonomy and deleting one must never take performances down
  with it. It replaced a case-insensitive free-text NAME match, which left a
  re-pointed leg rendering its previous venue forever -- the old
  `find_venue_tag` helper is gone, don't reintroduce that shape. The tag
  carries `city`/`city_en`/`city_zh` and `address`: a venue is always in one
  city, so the city is a property of the VENUE, not of each leg visiting it
  (`address` deliberately has NO locale variants -- its job is to be pasted
  into a map, and `location_url` already covers the maps link). A concert's
  VENUE tags are DERIVED, never typed: `sync_concert_venue_tags`
  (`db/service.py`) rewrites them as the union of its legs' venues, and the
  create route, the edit route and `import_commit` all call it. It returns the
  tags it NEWLY attached and every caller MUST feed those to
  `handle_newly_tagged` -- VENUE tags are subscribable, so someone following
  "Zepp Haneda" is owed the same DM notice a concert-level attach gives them
  (invariant 4). It touches VENUE rows only; franchise/group/artist attachment
  is deliberate and materialized (invariant 3) and must survive untouched.
  Discover's region filter is unchanged by all of this -- it still reads
  `concert_tags` client-side off each tile's `data-tags`, and this rollup is
  exactly what keeps that current while venues live on legs.
  `ConcertDay.venue_tag` is `lazy="raise"` ON PURPOSE: a lazy load during async
  template rendering is a `MissingGreenlet` 500, which this project has shipped
  once, and raising at the seam turns that into a loud test failure instead.
  Every path handing legs to a template must `selectinload` it, or load the
  tags separately by id the way `concert_rounds_context`
  (`web/routes/concerts.py`) does.
- **The legacy free-text venue columns are GONE (venue-to-tags is complete).**
  `ConcertDay.city`/`venue`/`venue_address` and `Concert.venue`/`venue_en`/
  `venue_zh` were dropped by migration `ce43bfcfcae3` once every venue lived on
  a leg's VENUE tag. They existed through phases 1-4 as recovery data (a leg
  whose free-text venue did not match a tag during the `789bbcc95bc3` backfill
  stayed recoverable); the owner confirmed zero unmatched legs in production
  before the drop. Do not reintroduce them or the old `apply_day_fields`
  preserve-on-empty rule -- a leg's venue is a VENUE tag and nothing else. A
  concert with no leg venue tag simply has no venue anywhere. NOTE the drop
  migration reversed the deploy order (restart on new code BEFORE
  `alembic upgrade head`) so the old process could not SELECT the dropped
  columns mid-deploy; any future column-DROP migration needs the same order.
- **A CHARACTER is a tag, and `Tag.voiced_by_tag_id` says who plays her**
  (migration `bb9780f0ad82`, 2026-08-01). `TagKind.CHARACTER` is a fifth kind
  beside franchise/artist/venue/group, because an idolm@ster bill credits
  如月千早 and never mentions 今井麻美 -- a user following the performer missed
  the show entirely, which is this app's worst failure. `voiced_by_tag_id` is a
  nullable self-FK to the ARTIST who voices her, `ON DELETE SET NULL` for the
  same reason `ConcertDay.venue_tag_id` is: deleting a performer's tag must not
  take the character down with it. A recast is this ONE value re-pointed --
  there is deliberately no history model, and the owner ruled recasting rare
  enough that there never should be.
  **It is NOT `parent_id`, and that was a decision, not an oversight.**
  `parent_id` means "the broader thing I belong to" and is what the Tags page
  renders its hierarchy from; a seiyuu is not broader than a character, and
  spending the column on her would leave 如月千早 unable to say she belongs to
  idolm@ster at all -- which Discover's franchise filtering reads.
  Only an ARTIST may voice a character, checked at BOTH write boundaries
  (`resolve_seiyuu` in `web/routes/tags.py`, 422; `apply_tag_import` in
  `db/service.py`, warn-and-leave-unvoiced). That check also refuses
  SELF-voicing for free, and the failure it prevents is silent rather than
  loud: a character pointed at herself lands in `performer_clusters`'
  `paired_seiyuu` set and is filtered out of `entries`, so she VANISHES from
  the Performing panel instead of erroring. Pointing it at a VENUE was the
  other reachable trap -- `attach_tag` materialises whatever it names onto the
  concert, so the venue would render as a performer and `handle_newly_tagged`
  would DM its followers.
  A tag's KIND stays immutable -- no route accepts it and the importer refuses
  a mismatch outright -- and the im@s reformat needs no exception: seiyuu stay
  ARTIST tags, characters are NEW tags, and a group's member list swaps
  handles. Don't add a kind-change path for this.
- `src/app/bot/` — thin shell: cogs, embed builders (`messages.py`),
  persistent buttons (`views.py`).
- `src/app/web/` — thin shell: routes, templates, static. `routes/imports.py`
  (the ramen.events importer, fetches the URL then delegates parsing to
  `domain/ingest.py`) MUST be registered before `routes/concerts.py` in
  `web/app.py` — otherwise `GET /concerts/import` gets swallowed by the
  `GET /concerts/{event_id}` route, since FastAPI matches path templates
  before literal segments. Its fetch is SSRF-guarded three ways: https +
  `ramen.events` host only, the same check re-run on every redirect hop via
  an httpx response hook, and the body streamed under a byte cap — don't
  loosen any of them. That guard is no longer local to this route: it lives in
  `app/fetching.py` and is SHARED with the Eventernote sweep (see below), so
  `fetch_ramen_html` is now a thin wrapper that translates the shared errors
  into this route's 400/502. Its preview (`import_preview.html`) is built in the
  same day-card/round-card/leg-chip vocabulary as `concert_new.html`/
  `concert_edit.html`, and `import_commit` binds a parsed round's
  `applies_to` to legs via the same `round_legs`/`day_key`/
  `parse_round_legs`/`key_to_day_id` path `create_concert` uses -- before
  this, the flat import form could not express a round spanning more than
  one leg.
  The same preview has a second producer: `POST /concerts/import/draft`
  takes a pasted YAML draft (the `domain/yaml_export.py` vocabulary made
  two-way -- `domain/yaml_import.py` parses it, warnings over failures,
  `yaml.safe_load` only) and renders `import_preview.html` fully prefilled:
  trilingual titles/labels, all four round anchors, tag/venue NAMES resolved
  to picker pre-selections via `match_tag_ids_by_name` /
  `match_venue_tag_id` (never ids in the draft; unmatched names render as
  hints, never dropped). The producer is normally an agent following
  `.claude/skills/add-concert/SKILL.md`, whose example draft is pinned to
  the parser by a test. import_commit stays the only write path.
  A THIRD producer takes MANY drafts at once: `POST /concerts/import/batch`
  splits a multi-document paste (`---` separated, plain YAML, no wrapper key)
  and persists each document verbatim as a `PendingDraft` row, which
  `/concerts/import/pending` then walks one preview at a time. It is
  deliberately NOT all-or-nothing -- `domain/yaml_import.py`'s `parse_drafts`
  names the documents that failed and keeps the rest, because at fifty
  concerts one typo must not cost the other forty-nine. Boundaries come from
  `yaml.scan()` rather than `text.split("---")` (a `---` inside a block scalar
  would cut a draft in half) and rather than `safe_load_all`/`compose_all`
  (both abort their generator on the FIRST bad document, silently losing every
  one after it); a paste that breaks the scanner itself falls back to a
  line-based split, so a scanner-level typo costs one oddly-split fragment
  instead of the batch. `PendingDraft` is the ONE place this app keeps step
  state, and the reason is that it is not step state: it is a work batch of
  fifty-to-a-hundred concerts each needing a human-read preview, which is not
  one sitting, and a hidden form field would lose it to a closed tab. A
  resubmitted pending commit (back button, refresh) answers 409 rather than
  minting a second concert -- agent drafts carry no `event_id`, so
  `generate_event_id` would de-dupe to `alpha-2` instead of colliding, and
  nothing would link the duplicate.
  **Starlette hard-caps every `Form(...)` field at 1MB**, whatever an
  app-level constant says, so `MAX_BATCH_CHARS` is 300k rather than the
  millions a paste of a hundred drafts might suggest. This applies to EVERY
  form field in the codebase, not just this route -- any future large-paste
  feature hits the same wall, and hits it as an opaque failure well before
  its own limit.
  Reminder-rule add/delete lives in
  `routes/reminders.py` (split out of `concerts.py`; renders via
  `concerts.render_rules_fragment`), and the `/me/timezone*` routes live in
  `routes/preferences.py` with the other per-user preference routes.
  `web/forms.py` holds the HTTP-boundary wrappers around domain validators
  (currently `form_url`) -- its own module so routes/concerts.py,
  routes/tags.py and routes/imports.py can all import it cheaply.
  A venue can be created without leaving the editor: `POST /tags/venue/quick`
  (`routes/tags.py`, editor-only, JSON) plus `_venue_create_dialog.html`,
  included by both `concert_new.html` and `concert_edit.html`. It answers 409
  on a duplicate name specifically, so the dialog can say "that venue already
  exists" instead of the generic 422 everything else gets. The concert-level
  venue picker was REMOVED from both forms -- the leg is the single place a
  venue is entered, and `create_concert_row` sets `venue=None`.
  Franchise/group/artist tags get the same treatment in the IMPORT PREVIEW:
  each unmatched draft name renders as a per-name create chip
  (`data-new-tag` + `data-tag-name`/`data-tag-kind`) opening
  `_tag_create_dialog.html`, backed by `POST /tags/quick` (editor-only,
  kind-aware, groups take a `parent_id`; its kind-scoped 409 returns the
  existing tag's id+name so the dialog offers one-click select-existing).
  A group created there is EMPTY on purpose -- expansion stays
  attach-time-only (invariant 3) -- and creation fires no notification
  (creation is not attachment, invariant 4). The created tag joins the
  picker via `_tag_picker_script.html`'s `pickerAddAndSelect`.
  `routes/discover.py` is the public catalogue and `routes/outcomes.py` is
  the web half of lottery-outcome capture (`POST /rounds/{id}/outcome`) --
  it shares `record_round_outcome` with the DM buttons rather than writing
  its own path (a second writer would desync the queue, invariant 2) and
  returns THREE top-level fragments: the deadline rows as the hx-target,
  plus `#board` and `#board-summary` out-of-band, since one recorded outcome
  changes all three. Don't wrap that response -- htmx only honours OOB
  elements at the top level.
- `src/app/domain/board.py` -- pure column precedence for Home's campaign
  board. `column_for(outcomes, has_open_round)` returns the ONE column a
  concert shows in; PAID > WON > APPLIED > open, deliberately, because money
  you owe outranks a round you could still enter. LOST and NOT_APPLIED place
  nothing (neither is an end state). `service.board_cards` gathers its
  inputs and `OPEN_COLUMN_LIMIT` caps the open column.
- `src/app/fetching.py` — the ONE host-pinned HTTP fetch, top-level beside
  `i18n.py` and `ops.py` (it does I/O, so it cannot live in `domain/`; both a
  web route and the scheduler import it). It was private to the ramen.events
  importer first, and it was EXTRACTED rather than copied when discovery needed
  it: two copies of a security control means a weakness found later gets fixed
  in one and missed in the other. The caller names its `allowed_host`; the guard
  raises its own `FetchError`/`HostNotAllowed`/`FetchFailed` and each caller
  translates (the web route to HTTP status codes, the sweep to a per-artist
  skip). The redirect hook is built PER CALL so it closes over that caller's
  host — a module-level hook pinned to one host is the obvious extraction bug
  and is exactly what a shared guard must not have.
- `src/app/discovery.py` — the discovery sweep: the Eventernote fetch, and
  since 2026-08-02 a calendar-feed pass in front of it. Sits ABOVE `db/` like
  `ops.py`: it imports `domain/`, `app/calendars.py` and `db.service`, and
  nothing in `db/` imports it. **The daily sweep SKIPS CHARACTER tags**
  (`Tag.kind != TagKind.CHARACTER` in `run_sweep`'s tag query) — a reversal of
  the kind-blind rule this file used to state, made on LOAD grounds (owner
  ruling, 2026-08-02): a character's `eventernote_url` is her seiyuu's own
  actor page, and the owner's ~90-tag im@s/LL expansion would have added
  hundreds of daily third-party fetches for pages whose events are franchise
  events the calendar feeds now cover. The URL stays storable and rendered (it
  is still the right link for a character), and `sweep_one_tag` — the manual
  per-tag button — deliberately does NOT filter: one fetch the owner asked for
  is not a daily cost. Don't re-widen the daily query to "every tag with a
  URL"; that is the thing that was undone. The EDITOR side is unaffected and
  keeps CHARACTER: `EVENTERNOTE_KINDS`
  (`domain/types.py`) is the one table saying which kinds' dialogs render the
  field AND which submits `edit_tag` may write it from, and the two must never
  become two lists. FastAPI folds an empty form value into an optional field's
  default, so `""` cannot mean "absent" and the omitted-leaves-alone trick
  `slug`/`voiced_by_tag_id` use is unavailable here — the KIND is what says
  whether this submit had a box. Writing it unconditionally is what erased a
  character's discovery link on the next rename. The sweep keeps the
  future prefix of each page, hands the whole sweep's events to
  `record_discovered` in ONE call (its event-id key is what stops the LoveLive
  15th, listed by nine catalogue tags, being reported nine times), and queues
  ONE `Notification` — never a DM of its own (invariant 4). **The calendar
  pass runs FIRST and pours into that same call and that same digest**: two
  pipelines, one `seen` list, one DM, and a feed that fails to fetch or parse
  is counted and skipped without ever costing the Eventernote half. It is
  outside the actor budget on purpose, so the worst-case tick is the SUM of
  both phases (spelled out at the top of `discovery.py`) — a feed roster that
  grows must never starve the artist rotation behind it. Fetches are
  SEQUENTIAL with a 1s pause; 86 parallel requests at a third party is how an
  IP gets blocked. Gated by `settings.discovery_enabled` (default False, same
  shape as `rehearsal_enabled`), which is also what keeps tests and dev runs
  off the network. Two operational rules, both learned the hard way and both
  silent when broken:
  - **A long in-tick job must beat the heartbeat inside its own loop.** The
    scheduler calls `heartbeat.beat()` BEFORE `tick()`, and `/healthz` reports
    unhealthy once the last beat is `MAX_AGE_SECONDS` (180s) old. A sweep of 86
    pages each with its own deliberate pause occupies the tick for minutes, so
    without a beat per artist it pages the owner about a perfectly healthy app.
    The loop genuinely IS alive, so beating in it is honest, not a workaround.
  - **`stamp_discovery_run` only FLUSHES**, so a stamp written in run_sweep's
    `finally` is thrown away by `scheduler/loop.py`'s handler when it (correctly)
    rolls the poisoned session back. The handler therefore RE-stamps and commits
    on the cleaned transaction. Both halves are needed, and the failure mode is
    the nastiest kind: tests are green because they never roll back, while in
    production a sweep that dies leaves `discovery_due` true and re-runs 86
    fetches every 60 seconds forever. Any future "record that we ran" written in
    a `finally` on the scheduler's session has the same hole.
- `src/app/calendars.py` — WHICH public `.ics` feeds discovery reads, and what
  counts as a lead in each. Same layer as `discovery.py`/`ops.py` (imports
  `domain/` and `db.service`; nothing in `db/` imports it), and it must NOT
  import `app.discovery` — the sweep imports THIS, so the reverse would close
  a cycle, which is why the User-Agent string is spelled out here rather than
  borrowed. The roster is CODE-LEVEL CONFIG, not a table or an env var: it
  changes rarely, changing it is an edit+deploy exactly like the admin
  whitelist, and `dates_are`/`include_prefixes` are typed fields no env CSV
  expresses. **`dates_are` is per FEED, and that is what keeps stored dates
  honest** — the LL-Fans main calendar carries performance dates while its
  per-group subs carry 申込期限, so they are separate roster entries rather
  than one feed with mixed semantics; mixing them would file a deadline as a
  show date, which is the exact mistake `date_is_deadline` exists to prevent.
  `include_prefixes` matches with `str.startswith` and empty means "take every
  VEVENT" (right for the single-purpose imas feed); a SUMMARY the list does
  not want is DROPPED and NOT counted as skipped, because skipped means
  UNREADABLE and folding a working filter into it would make a healthy feed
  read as a rotting one. The nine-feed launch roster was fetched and parsed
  one by one before inclusion and the verdicts — including why an
  empty-but-alive feed was KEPT, and the accepted promoter-named-round gap —
  are in the module's own probe block. Update it there when the roster
  changes; a roster nobody can audit is how a dead feed survives.
  Leads are namespaced (`"<feed key>:<UID>"`), which is what lets one UNIQUE
  column hold both sources.
- `src/app/scheduler/` — the tick loop that delivers DMs.
- `routes/welcome.py` -- the five-step welcome wizard, rebuilt on the design
  system and flowing seamlessly into `/setup` (`POST /welcome/advance`
  redirects there once the step count is exhausted). Its default-reminders
  step is the one step here that writes: it materialises a real
  `ReminderPreset` through the single `db/service.py` helper
  `create_preset_from_rules` (no second write path), offering three
  template rule sets (Relaxed / Standard / On the ball) plus a
  sentence-style fine-tune list over the five anchors. Offsets are
  days+hours only -- `PresetItem` has no minutes column, so the wizard
  cannot offer a "30 minutes before" choice; see the minute-offset entry
  in WISHLIST.md.
- `routes/setup.py` — the first-run capture flow, run AFTER the `/welcome`
  wizard. Three plain GETs (`/setup` prune tiles → `/setup/applications` →
  `/setup/ready` reveal) plus two batch POSTs. NO capture-flow step state
  exists anywhere: each screen renders current DB truth (tag-implied concerts
  minus overrides, outcomes), which is what makes it tamper-safe and
  re-runnable (Preferences' "Run first-time setup again" points here). Pruning
  goes through the branch-4 `set/clear_concert_subscription` writers;
  applications funnel EXCLUSIVELY through `record_round_outcome`. All logic is
  in `db/service.py`'s `# First-run capture flow (/setup)` section.
- `routes/calendar.py` — the personal calendar-feed subscription
  (`POST /me/calendar-feed` mints the token, `GET /calendar/{token}.ics` is
  the feed itself). The `.ics` route deliberately has NO `require_user` —
  calendar apps poll it directly with no cookies, so the token in the URL
  *is* the credential.
  **The feed is the user's STANDING-AWARE LANDSCAPE, not a mirror of their
  reminder rules** (ruling 2026-08-04). `user_calendar_events` reads no
  `reminder_queue` at all: it derives every tracked concert's live show dates
  plus each surviving round's next moments, selected by that user's outcome —
  no outcome → future opens + closes, APPLIED → `_result_moment`, WON →
  payment deadline, LOST/NOT_APPLIED/PAID → nothing (a LOST round's auto-armed
  successor is an ordinary no-outcome round and carries the ladder on).
  Future-only, and every exclusion goes through the shared per-user helpers
  the other read surfaces use — no suppression rule is invented here.
  **Reminder rules therefore mean exactly one thing: when Discord DMs you**;
  a sparse preset used to read as a broken calendar, which is the bug this
  replaced. `/mydeadlines` (`bot/cogs/reminders.py`) reads the SAME function,
  so it inherits the landscape — a deliberate behavior change, one derivation.
  `CalendarEvent.anchor` is required because a no-outcome round emits two
  events with one summary: the feed qualifies canonically from
  `CANONICAL_ANCHOR_QUALIFIERS` (`domain/ics_export.py` — 受付開始/申込締切/
  当落発表/支払期限, plain data and deliberately NOT gettext, since canonical
  text is by definition untranslated), while the cog qualifies through `_()`
  in the recipient's language. The locale contract is unchanged: feed
  canonical (`locale=None`), cog localized.
  There is NO per-round `.ics` download any more — the 📅 link,
  `GET /rounds/{id}/ics` and `build_ics` were deleted (a file is a snapshot
  that rots the moment a deadline moves; the feed re-plans on every fetch).
  `build_calendar` and its VEVENT helpers stay; a 404 test pins the absence.
  A minted URL is shown through ONE partial, `_feed_links.html` (webcal://
  link + copy button + the URL), consumed by Preferences, welcome step 4 and
  the concert page's calendar dialog, so the ergonomics cannot drift; the
  mint's `next` runs `safe_next` first, then an allowlist of SHAPES
  (`/preferences`, `/welcome`, `/concerts/` prefix) — `_allowed_next`.
- `routes/rehearsal.py` — the local rehearsal harness (`/admin/rehearsal`):
  seed one canonical concert, pull its reminders forward so the real 60s tick
  delivers them now, and send any DM shape in any language on demand. **Gated,
  not guarded**: `web/app.py` registers this router only when
  `settings.rehearsal_enabled` is true, which production never sets, so there
  the routes do not exist at all — `require_admin` on each one is a second
  layer for a misconfigured deploy, not the primary guard. Its own module
  because a router registers whole and `admin.py` serves routes production
  needs. English-only and NOT wrapped in `_()`, like `/admin/deliveries`. Its
  shape catalogue (`POST /admin/rehearsal/shape`) sends a DM straight from a
  web route: the SECOND sanctioned exception to invariant 4, alongside
  `POST /me/test-dm` and for the same reason (a manual, one-at-a-time
  diagnostic, not a system-initiated notice) — with the extra claim that this
  route is absent from production entirely. Don't read either as licence for a
  third. Operator setup (second Discord app, test server, the redirect URI
  that bites) is `docs/local-dev-bot.md`.
- `routes/discoveries.py` — the discovery review surface
  (`GET /admin/discoveries`, `POST /admin/discoveries/{id}/dismiss`), admin-only,
  linked from Preferences with the other admin pages. Its own module rather than
  a section of `admin.py` for the same reason `rehearsal.py` is: a router
  registers whole, and discovery is a fourth unrelated concern beside the
  delivery log, the broadcast and the catalogue round-trip. English-only and NOT
  wrapped in `_()`, like `/admin/deliveries`; only the Preferences LINK is
  translated. **It writes exactly one column, `dismissed_at`** — it never creates
  a concert, because neither source carries a verified round: Eventernote has no
  ticket information at all, and a fan-maintained calendar entry is a POINTER a
  human still checks against the official page. A lead says "this exists and you
  are not tracking it" and nothing more. Turning one
  into a concert stays with an agent following `.claude/skills/add-concert`,
  which is what the page's copy block (the same `build_discovery_dm`, with
  `budget=None`) is for. `import_commit` remains the only write path into
  `concerts`. Two things it deliberately does NOT do: `open_leads` does not
  filter on `announced_at` (announced is not triaged — the sweep marks every
  fresh lead announced whether the DM named it or merely counted it, so this page
  is where a first sweep's "+N more" is actually reachable; the column is SHOWN
  instead), and a same-date-same-venue collision with an existing leg is a HINT
  on the row, never a suppression, because 昼公演 and 夜公演 are two Eventernote
  events on one date at one venue and suppressing would hide exactly the second
  show. `ConcertDay.eventernote_event_id` is the exact-match half of the same
  question: populated by the import path going forward, so "do I already have
  this?" is an id lookup rather than a guess about Japanese titles that vary in
  spacing, brackets and 〜 marks. It is not backfilled, so that branch gains
  coverage over time rather than arriving complete. It keeps its
  Eventernote-specific NAME on purpose — a calendar lead never exact-matches a
  leg, it only ever gets the date+venue hint, and only when the VEVENT carried
  a LOCATION.
  **A lead's id column is `DiscoveredEvent.source_event_id`** (migration
  `d446e6c0a3e6` renamed it from `eventernote_event_id` and widened it):
  Eventernote rows keep their bare numeric ids, calendar rows carry a
  namespaced `"<feed key>:<UID>"`, and the prefix is what lets the single
  UNIQUE column serve both without a cross-source collision. Two sibling
  columns say the rest — `source` (`"eventernote"` or a `CalendarFeed.key`,
  stored EXPLICITLY rather than parsed back out of the id, so nothing has to
  split a string to know where a row came from) and `date_is_deadline`, which
  is why this page and the DM render such rows as `申込締切 {date}`: the imas
  feed's DTSTART is an application deadline, and showing it as a performance
  date would mislead exactly the person triaging it. Both server-default to
  the pre-calendar behaviour, so every pre-existing row reads back correct.
- Concert edit history: `db/service.py`'s `snapshot_concert`/
  `record_concert_edit`/`concert_audit_log`, backing the `ConcertAudit`
  table (`db/models.py`). Deliberately lightweight — only the concert's own
  top-level scalar fields (title, organizer, URLs, notes, ...), NOT
  day/round/tag adds-removes-edits. `edit_concert` (`web/routes/concerts.py`)
  must call `snapshot_concert` BEFORE mutating the concert and
  `record_concert_edit` AFTER — get that order backwards and every diff
  reads as unchanged.
- `src/app/i18n.py` — gettext plumbing, top-level (not `domain/`, since it
  does file I/O at startup; not `web/`, since the bot imports it too).
  `messages.po` in `src/app/translations/{ja,zh}/LC_MESSAGES/` compile to
  `.mo` in memory at first use per locale (no `.mo` on disk, no deploy-ritual
  change). `en` is `NullTranslations` — the identity function, so English
  output stays byte-identical to the pre-i18n app and no EN test should ever
  assert a translated string. Locale is an asyncio-context `ContextVar`
  (`get_locale`/`set_locale`), set once per request by `web/app.py`'s
  middleware and once per recipient by the scheduler. Write translatable
  strings as `_("literal")` at the point they're rendered/looked up (`_` is
  `gettext`); a module-level dict keyed or valued by translatable text (e.g.
  `LABEL_BY_ROUND_KIND`) instead wraps each literal in `N_()`, a no-op marker
  that only makes `pybabel extract` see it — the real translation happens
  later, at lookup time, via `_`/`gettext`, never at the dict's definition
  time.
  `tags_diff.py` is the third piece of the tags vocabulary and deliberately its
  own module: `tags_yaml.py` is about the FORMAT (serialize/parse), this is
  about COMPARISON, and one module doing all three is how a file starts growing
  unwieldy. It reuses `TagExport` as the current-catalogue carrier rather than
  inventing a second shape, and `service.current_tag_exports` is the ONE builder
  of that snapshot -- the zip export and the differ must compare against exactly
  the same thing or a restore drifts. `gettext_in(locale, msg)` is the explicit-locale escape hatch for
  text composed before a per-recipient locale is known (e.g. `NoticeContext`,
  built once for many recipients up front). `loc_field(obj, field, locale)`
  resolves a UGC field's viewer-locale variant: en → `{field}_en`, zh →
  `{field}_zh`, ja → the original column (Japanese IS the source of truth,
  there's no `_ja` column); an empty string counts as unfilled and falls
  through; there is no cross-locale chaining (zh never falls back through en
  to the original). The UGC layer now also covers venue names through tags
  (`Tag.name_en`/`name_zh`, plus `city_en`/`city_zh`), and phase 2 added
  `ConcertDay.label_en`/`label_zh` and `Round.label_zh` (migration
  `a589d82c11b4`) so leg and round labels resolve in the viewer's language
  too. `Round.label_en` CHANGED MEANING there: it predates the i18n layer
  and used to render to EVERY viewer as an English gloss beside the Japanese
  label, and it is now a true locale variant selected by `loc_field`.
  THREE locale sources are in play, and choosing the wrong one is SILENT --
  nothing raises, the text just comes out in somebody else's language.
  `get_locale()` for anything inside a web request; `user.language` for
  per-recipient text composed once for many recipients OUTSIDE any request
  (scheduler DMs, `NoticeContext`); and an explicit `locale: str | None`
  parameter where the caller must decide -- currently `user_calendar_events`
  alone, where `None` is DELIBERATE so the `.ics` feed stays canonical rather
  than following whoever happened to trigger the render. This bites hardest in
  `db/service.py`, where ~10 sites COPY a label string into a dataclass before
  it ever reaches a template: the field resolves at the copy site, not at
  render time, so the locale has to be right there.
  Editing existing English copy must keep the msgid
  byte-identical (or both catalogues silently lose that translation) and
  update BOTH `messages.po` files — `tests/test_i18n_catalogues.py` extracts
  every msgid in-process and fails on anything untranslated (fuzzy entries
  count as untranslated, since `i18n.py` compiles with `use_fuzzy=False`).
  Locale resolution treats the `lang` cookie as a CACHE of `users.language`,
  never the source of truth: `web/app.py`'s middleware reads the cookie if
  present and supported, else negotiates from `Accept-Language`
  (`i18n.negotiate`), else `en`; the single write path is public
  `POST /language` (`web/app.py`), which always sets the cookie and also
  updates the DB column when signed in (Discord DMs read the column, not the
  cookie); the OAuth callback (`web/auth.py`) seeds the column from the
  cookie, but ONLY at account creation, since the column can't otherwise
  distinguish "defaulted to en" from "chose en".
- Bot and web NEVER contain business logic; they call `db/service.py`.
- `docs/superpowers/specs/` + `plans/` — date-prefixed design specs and
  implementation plans; each recent feature (cancelled legs, Tags redesign,
  index reorg) committed one of each before code. Follow that pattern for
  substantial features. `docs/codebase-review-2026-07-17.md` records a
  full-codebase review and the fixes it drove.
- `docs/superpowers/demo/` — the interactive concept demos that drove the UI,
  and the **design source of truth** for it. All are self-contained
  single-file mockups on the same design tokens the app ships. Review UI/UX
  changes against the matching one; when the shipped design deliberately
  moves, update that demo so it stays the reference. The full inventory,
  because reaching for the wrong file wastes a whole pass:
  - `dekimasen-demo.html` — the reconciliation reference for Home/Discover/
    concert/editor/tags/preferences/setup. The default answer.
  - `dekimasen-onboarding-demo.html` — the signed-out landing, the new-user
    flow, import + import preview, retroactive-apply, legal.
  - `dekimasen-mobile-demo.html` (static frames, reference CSS values) and
    `dekimasen-mobile-live.html` (interactions) — the phone reference.
  - `dekimasen-tablet-demo.html` — the 701-1040px band.
  - `dekimasen-ux-pass-demo.html` — the 2026-07-24 UX pass's 20 changes,
    including the two-shape callout grammar.
  - `_tablet_harness.html` — not a demo: the measuring rig the tablet band
    was built against (see the measure-don't-reason rule below).

  Known gap: the 403/404/422/500 error pages shipped with no demo frame, and
  the signed-out `.signin-note` never got one either. Both are logged in
  WISHLIST's demo-parity cosmetics entry rather than left to be rediscovered.

## Feature wishlist

`WISHLIST.md` (repo root) tracks every potential feature raised in
roadmap/UX discussions, ordered by user impact (highest first), with
impact + effort noted per entry. Read it before any feature-planning or
roadmap discussion. Every time a new feature is pushed: move the shipped
entry to its Shipped section (with the date), then do a full revision
pass over the remaining entries — re-rank by impact and reconsider which
are still useful, since a shipped feature can raise, lower, or obsolete
others. Append new ideas from any discussion with their date and context;
move rejected ideas to the Rejected section with the reason instead of
deleting them.

## Non-negotiable invariants

1. **Timezones**: the DB stores aware UTC only — the `UTCDateTime`
   TypeDecorator rejects naive datetimes. Forms enter times in JST
   (`parse_jst`/`jst_to_utc`); display is always dual via `fmt_dual`:
   "…JST (…user-tz)". Never store or compare naive datetimes.
2. **Queue sync**: `reminder_queue` is a materialized outbox. Any edit to
   concerts/windows/days/rules must call the relevant `sync_*` function.
   Re-planning is always safe: unsent rows update/delete freely; a deadline
   postponed after its reminder was sent re-arms it (sent_at cleared). Only
   successful DM delivery marks a row sent; discord.Forbidden drops it;
   transient errors retry next tick. Never break these semantics.
   A cancelled `ConcertDay` is never deleted, only flagged —
   `db/service.py`'s `concert_round_rows()` and every `applies_to` consumer
   rely on the day row still existing. Rounds have no status of their own; a round counts
   as cancelled when every day in its `applies_to` is cancelled.
   A concert whose EVERY leg is cancelled contributes no live rounds
   anywhere — including the General rounds `is_round_cancelled` rightly
   exempts, since they name no leg — and that concert-level question is
   `all_legs_cancelled(days)` (`db/service.py`), the Python twin of
   `discoverable_concert_criterion`, pinned to it by an agreement test.
   NEVER answer it by widening `is_round_cancelled`: a General round on a
   multi-leg concert with one dead leg must stay live, so the per-round
   predicate cannot see this and must not learn to.
   `RoundOutcome` (per-user, per-round lottery progress) layers a second,
   per-user suppression pass onto the same `sync_rule` candidate-list
   filtering, orthogonal to cancellation — see
   `db/service.py`'s `_apply_outcome_suppression`.
   `RoundOutcomeDay` layers per-day WON/LOST UNDER that: a real lottery
   resolves per performance, so a round covering Sat+Sun can come back won on
   one and lost on the other. NO rows means all — a round settled as a whole
   settled every leg it covers, which is every row predating this and every
   single-leg round — so the first explicit per-day write MATERIALIZES the
   implicit rows before adding its own, and nothing downstream re-derives the
   convention. Write only through `record_round_day_result` /
   `record_remaining_days_lost` (a second writer desyncs the queue exactly as
   a second `record_round_outcome` would), and read the legs a user actually
   secured through `secured_day_ids_by_round`, never off the round outcome.
   An UPGRADE round (`RoundKind.UPGRADE`) is a nested second campaign whose
   availability is per-user DERIVED, never stored: a user is eligible only
   when they hold a secured (WON/PAID) ticket in one of the round's
   `round_qualifiers` (an empty qualifier set means any secured ticket on the
   concert, mirroring `applies_to`'s empty-means-all). Eligibility is pure
   (`domain/upgrades.py:is_eligible`) and threaded through the same per-user
   seams — `_apply_outcome_suppression` (exempt from the secured-elsewhere
   suppression, then re-suppressed when ineligible), auto-arm, `column_for`,
   and every capture surface — not the pure planner. Editors set the
   qualifier set as chips (`parse_round_qualifiers`); never persist
   eligibility.
3. **Group tag expansion** (agreed with the owner, do not change): attaching
   a GROUP tag to a concert materializes its members AT THAT MOMENT only.
   Editors prune non-performers; removed members stay removed; detach +
   re-attach re-expands; group membership edits never rewrite existing
   concerts. The creation form passes `expand=False` because its explicit
   artist list is authoritative. `POST /concerts/{event_id}/duplicate`
   (`web/routes/concerts.py`) follows the same rule when cloning a concert:
   it re-attaches the source's exact already-pruned tag set with
   `expand=False`, never re-expanding a GROUP tag to its current membership.
   **Expansion chains exactly ONE fixed step further: group -> character ->
   seiyuu** (2026-08-01). A GROUP's members may be ARTIST tags, CHARACTER tags
   or a mix — nothing requires uniformity, and a group with no character in it
   behaves byte-for-byte as it always did. Where they ARE characters, stopping
   at the members would leave every seiyuu unattached and a group-credited im@s
   show would match nobody following the performer, so `attach_tag` also
   attaches the `voiced_by_tag_id` of every character it just attached —
   directly or via a group. Because `tracked_concert_ids` matches MATERIALIZED
   `concert_tags` rows, that one act makes following the seiyuu work with **zero
   change to subscription code**. This is a fixed two-step, NOT recursion, and
   NOT the nested-groups rule returning: a seiyuu is an ARTIST and expansion
   stops at artists, so the chain terminates by construction. It is deliberately
   not gated on `expand` — that flag exists so the creation form's explicit
   artist list is not overridden, and attaching the seiyuu overrides nothing.
   The reverse NEVER happens: attaching 今井麻美 pulls in no characters, because
   she also appears as herself at events with no im@s connection.
   **A seiyuu attached via a character is DERIVED, never chosen** (owner ruling,
   2026-08-01, and the principle the rest of the model hangs on). An event
   credits the character OR the performer. So the concert editor never offers a
   derived seiyuu as a tick — `edit_concert_form` subtracts
   `{c.voiced_by_tag_id for attached characters}` out of
   `initial_selected["artist"]` — and `edit_concert` EXPANDS its desired set the
   same way before diffing (`keep_ids`), mirroring on the detach side what
   `attach_tag` does on the attach side. Both halves are needed together: pre-
   ticking her is what made the prune rule below unreachable for a whole task,
   and dropping her from the picker without the expansion would instead detach
   the performer on every save. Which seiyuu are derived needs no provenance
   column — she is derived exactly when some attached character names her, the
   same derivation the display rule and the prune rule run.
   **The editor's picker splits a group's members BY KIND**
   (`tag_picker_context`, `_tag_picker_script.html`): `members` is the ARTIST
   half and feeds `autoArtists()` -> `artist_tags`, `character_members` is the
   CHARACTER half and feeds `autoCharacters()` -> `character_tags`, and each
   row carries its own excluded set so either kind can be pruned. Unsplit,
   ticking such a group posted CHARACTER ids as `artist_tags` and
   `resolve_tags(..., ARTIST)` answered 422 — and the workaround an editor
   reaches for after that (× the offending chips) SILENTLY attached the group
   alone, since the creation form expands with `expand=False`. `autoArtists()`
   additionally removes the seiyuu of every SELECTED character, which is where
   the derived-seiyuu ruling belongs for a seiyuu who is ALSO a direct artist
   member: offering her means posting her, which means `after_ids` pins her and
   dropping her character can never drop her.
   CHARACTER is a first-class PICKED kind on all three editor surfaces, so
   `edit_concert` must keep resolving `character_tags` into `desired_tags`:
   omission means removal for every non-VENUE kind, and leaving characters out
   of the diff detached them (and cascaded their seiyuu off) on a save that
   never mentioned them. The concert DRAFT vocabulary carries characters too
   (2026-08-01): `series.characters` + `series_handles.characters` through
   `concert_to_yaml`/`parse_draft`, so `export.zip` is a faithful backup of an
   im@s bill (before this a restore came back artists-only — the derived seiyuu
   survived as the ARTIST row she is and the character was simply gone) and the
   `add-concert` skill can author one. `concert_to_yaml`'s `characters`
   parameter is deliberately REQUIRED rather than defaulted: a kind added after
   the format shipped and quietly defaulting to empty is exactly how this hole
   opened, and there is only one production caller to break.
   **Pruning a character ALWAYS detaches her seiyuu** (owner, 2026-08-01),
   with one refinement that is not an exception: unless another still-attached
   character shares that seiyuu. A performer can voice two characters on one
   bill, and detaching her because one was pruned would silently drop the
   other's performer. `detach_tag` derives that at prune time; it also honours
   `keep_tag_ids`, the caller's statement of its DESIRED end state, which is
   what makes the editor's detach-then-attach order safe (without it a seiyuu
   the editor ticked deliberately sits in neither diff, the first save loses her
   and a second identical save restores her). KNOWN EDGE, accepted rather than
   solved: `concert_tags` records no provenance — group expansion has had that
   blind spot since it shipped — so a seiyuu who was ALSO there in her own right
   goes when the character is pruned, and the editor re-adds her.
   **`parent_id` widened to GROUP -> GROUP (a subunit) and CHARACTER ->
   FRANCHISE**, both the SAME meaning the column already carried (竜宮小町
   belongs to 765PRO ALLSTARS the way 765PRO ALLSTARS belongs to idolm@ster).
   The permitted table is `ALLOWED_PARENT_KINDS` in `domain/types.py` and every
   write path reads it — `POST /tags`, `POST /tags/quick` and the catalogue
   importer — because two copies of it drifted apart once and a file could then
   not express a subunit at all. Membership stays FLAT: a subunit's members are
   its own tags, never the parent group, so `TagMember`'s no-nested-groups rule
   still stands. GROUP -> GROUP made loops possible for the first time, so
   `would_create_tag_cycle` guards the one path that can set a parent on an
   EXISTING tag (`apply_tag_import`); nothing else in the codebase walks
   `parent_id` transitively, which is exactly why an unguarded loop would not be
   noticed until something did and then would hang rather than fail.
   **A tag is identified by its `slug`, never its name.** Names are NOT unique
   and never will be: two performers may genuinely share one, and a venue may
   share one with a group (owner ruling, 2026-07-29). `Tag.slug` is the only
   unique column — auto-generated from `name_en`/`name` by `assign_tag_slug`
   (`db/service.py`, the single minting path; a model-level default guarantees
   non-nullness, but only that helper de-duplicates). **`create_tag_row` is the
   single place a `Tag` row is constructed**: `slug=None` mints one, a value is
   used verbatim — the three editor routes take the first branch, the catalogue
   importer the second, because its handles come from a file and must not be
   silently renamed. The handle is editable on the Tags page,
   ASCII by construction, and absent from every URL (tag pages stay on the
   numeric id). Anything answering "do I already have this tag?" must ask by
   slug; a name match is a hint for a human. There is deliberately NO
   single-result lookup by name — `find_tags_by_name_and_kind` is plural, and
   both single-result ancestors were DELETED because `scalar_one_or_none` raises
   `MultipleResultsFound` by construction once names repeat. A rename never
   touches the slug, for the same reason invariant 6 freezes `event_id`.
   The three create surfaces deliberately DIVERGE on a duplicate name, and this
   is not drift: `POST /tags` allows it (the Tags page is where deliberate
   things happen, and it already warns before submit via `#new-tag-dupe`), while
   `POST /tags/quick` and `POST /tags/venue/quick` still answer 409 with the
   existing tag's id so their dialogs can offer one-click select-existing —
   mid-import, an existing tag of the name you just typed is almost certainly
   the one you meant. `tests/test_error_pages.py` pins that those 409s keep
   their JSON body instead of becoming an HTML error page.
   **The catalogue round-trip keys on handles, and only on handles.**
   `GET /admin/export.zip` writes `tags.yaml` plus one draft per concert;
   `POST /admin/import/tags` reads the former. A concert draft carries
   `series_handles` and per-leg `venue_handle` beside the names, and where a
   handle block names a kind it is AUTHORITATIVE — the name list is ignored
   outright, with NO per-entry fallback, because falling back would reintroduce
   `match_tag_ids_by_name`'s first-tag-wins guess, which is the exact failure
   handles exist to remove. A missing handle means "import tags.yaml first" and
   surfaces as unmatched. **The tags import PLANS before it writes** (2026-07-31):
   `domain/tags_diff.py` compares the file against the catalogue and
   `POST /admin/import/tags` renders that plan, while `/apply` commits it. Per
   field, a blank on the DB side is a FILL applied automatically (writing into
   emptiness cannot lose anything), a blank in the file changes nothing, and two
   differing values are a CONFLICT somebody resolves. EVERY DEFAULT CHANGES
   NOTHING: an unanswered conflict keeps the catalogue's value, and a member
   removal — the only destructive act in the feature — happens solely when
   explicitly ticked. `kind` is compared but never choosable: a venue arriving as
   an artist could orphan a leg's `venue_tag_id`, so it warns and the tag is
   refused whole. `voiced_by` rides the round-trip as a HANDLE, like `parent`
   and for the same reasons (ids mean nothing across a restore, names are not
   unique), and it joined `COMPARABLE_FIELDS` as its TWELFTH entry — the count
   is pinned by a test precisely so a field cannot enter the format while the
   differ silently skips it. `/apply` RE-PARSES and RE-PLANS from the pasted file, so the
   browser only ever sends `mine`/`theirs`, never a value — nothing can be
   injected. Nothing is ever deleted: a catalogue tag the file omits is untouched
   and unmentioned. It writes `TagMember`
   directly (never `attach_tag`, which would drag invariant 3's expansion into
   something that must touch no concert), and queues no notification. A draft
   may also carry `event_id`, checked by the same `validate_event_id` the edit
   page uses, so a restore keeps its URLs and a re-import of a concert that
   still exists answers 409 rather than duplicating it.
4. **Notifications**: new-event notices go through the `notifications`
   table (DB outbox drained by the scheduler) — never send DMs directly
   from web routes. One narrow, explicit exception: `POST /me/test-dm`
   (`web/routes/preferences.py`) sends synchronously and reports the
   result inline — a manual, user-initiated, low-volume diagnostic
   action is a different animal from a system-initiated notice, which
   must still go through the outbox for its retry/ordering/audit
   properties. Don't extend this carve-out to anything else without
   discussing it first.
   **`handle_newly_tagged` must be called only once the concert's legs are
   written.** It asks `all_legs_cancelled` (a dead concert notifies nobody
   and applies no preset), so calling it while the legs of the current
   submit are still unflushed asks the question of the concert as it
   ARRIVED, and both answers are wrong in a way nothing surfaces: a
   suppressed notice has no re-announce path, and an announced dead concert
   has no un-send. `create_concert_row` therefore RETURNS its newly attached
   tags instead of consuming them -- it used to call the pipeline itself,
   which is exactly how create and import shipped a 🆕 "Apply here" for a
   concert whose only leg arrived cancelled -- and `create_concert`,
   `import_commit` and `edit_concert` each run it after their legs flush,
   next to the venue rollup, which always got this right.
   `duplicate_concert` is the one exception and is correct: it creates no
   legs at all, so its clone is a genuine dateless draft, which
   `all_legs_cancelled` deliberately exempts. Don't "unify" it with the
   others.
   Any new notification kind that REPORTS ON deliveries must be added to
   `UNREPORTED_NOTE_KINDS` (`db/service.py`), or it will log its own delivery,
   report that next tick, and DM every admin once a minute forever. The
   delivery log (`delivery_log`) covers both drains deliberately -- the
   likeliest way this app messages the wrong people is `handle_newly_tagged`
   fanning a `new_event` notice across a tag's followers, which is a
   notification, not a reminder. `/admin/deliveries` is its reader and the
   ONLY surface that names recipients; the digest DM reports counts, because
   a name in Discord history is a record `POST /me/delete` cannot reach.
   An admin broadcast (`/admin/broadcast`) is the one path that puts
   admin-authored text into other users' DMs, and it still goes through the
   outbox -- it is queued HELD via `Notification.send_after_utc` (120s) so it
   can be cancelled, and cancelling deletes only the UNSENT rows. Both new
   `Notification` columns are nullable and NULL means the pre-broadcast
   behaviour, which is what keeps every other notice unaffected by the drain
   query's hold clause. `due_notifications`' `send_after_utc IS NULL` branch is
   load-bearing: SQL evaluates `NULL <= now` as NULL, so dropping it stops the
   entire outbox. The broadcast is NOT in `UNREPORTED_NOTE_KINDS` and must not
   be added -- it terminates after one hop, and whether the remedy reached its
   recipients (`FORBIDDEN` ones included) is the question it was sent asking.
   The Eventernote sweep's `discovery` notice is likewise NOT in
   `UNREPORTED_NOTE_KINDS`, and for the plainer reason: that set is only for
   notices that REPORT ON deliveries, and this one reports on a third-party
   page. It is an ordinary notice and belongs in `delivery_log` like any other.
   It is queued with `concert_id = NULL`, which already means "render the
   plain-text body, not a rich embed" and already makes `record_deliveries` skip
   the title lookup, so the drain needed no change at all. Its recipients are
   `ADMIN_WHITELIST`, the same audience as `ops_alert`, and it follows
   `evaluate_and_alert`'s precedent exactly: `Notification.user_id` is an FK to
   `users.discord_id`, so an admin who has never signed in must be `ensure_user`d
   first or the queue raises `IntegrityError` at flush, far from the cause -- but
   only when `session.get(User, admin_id)` returns None, since `ensure_user`
   refreshes the username and would otherwise overwrite a real admin's name with
   the placeholder on every single sweep.
5. **Auth**: three tiers — admin, editor, user. Admins = `ADMIN_WHITELIST`
   env (Discord IDs), env-only by design (no runtime UI; edit `.env` +
   restart). Editors = `EDITOR_WHITELIST` env (permanent bootstrap/
   break-glass set) OR the `users.is_editor` DB flag, which admins can
   toggle live from the preferences page or `/promote-editor` /
   `/demote-editor` Discord commands. Admins automatically pass editor
   checks too. Sessions are DB-backed sha256 token hashes (revocable).
   Ownership checks 404, not 403, on other users' presets/subscriptions.
   Being SIGNED OUT is not an error: `require_user` raises `LoginRequired`
   (not an HTTPException), and `web/app.py`'s handler sends the visitor to
   `/`, which signed out is the real landing page with the sign-in CTA --
   303, never 307, so a signed-out POST is not replayed against `/`, and
   `HX-Redirect` + 204 for htmx requests, since an XHR would follow a 303
   and swap the whole landing page into a fragment target. Being signed in
   and unauthorized IS an error and stays 403 (`require_editor`/
   `require_admin`) -- don't fold the two together.
   The redirect carries `?next=<path>` so login returns the visitor to the
   page they asked for. Three rules hold it together: only GETs get a
   `next` (a POST body is gone, so replaying its URL renders a form that
   looks like it submitted and didn't); htmx uses `HX-Current-URL`, since
   the fragment endpoint is not somewhere you can stand; and the value
   always passes `domain/urls.py:safe_next`, which reduces it to a
   same-origin path or None (it folds backslashes -- `/\evil.com` reaches
   the network as scheme-relative `//evil.com`). `next` rides to Discord
   in OUR signed session cookie next to `oauth_state`, never as an OAuth
   query param, so it cannot return attacker-controlled; the callback
   re-validates anyway, and an account whose wizard was never finished
   (`User.welcomed_at` NULL -- row existence proves nothing, the bot's
   `ensure_user` mints bare rows) still goes to `/welcome` regardless. Templates link sign-in via the `login_url(request)` global,
   never a bare `/auth/login` -- miss one CTA and that button silently
   drops the destination the others keep.
   No separate CSRF token: mutating routes rely on `SameSite=Lax` cookies
   (`web/app.py`'s `SessionMiddleware`). Deliberate for an app this size —
   don't read it as a gap to fill or bolt a token system onto.
   Any future personal-secret-link feature (the calendar feed is the first)
   should reuse the same shape: `secrets.token_urlsafe`, only the SHA-256
   hash stored (`User.calendar_token_hash` mirrors `WebSession.token_hash`),
   the raw value shown to the user exactly once and never persisted
   anywhere retrievable — recovery is "generate a new one," not "look up
   the old one."
   Self-serve erasure is `POST /me/delete` (`web/routes/preferences.py`):
   `require_user`-scoped to the caller, revokes the session via the shared
   `revoke_session` helper, then calls `service.delete_user`, behind a
   heavy client-side confirmation naming what is kept vs removed.
6. **`event_id` vs `id`**: every FK targets `Concert.id` (internal PK), but
   URLs use the editor-chosen, unique `event_id` string instead. `"new"` and
   `"import"` are reserved and rejected as `event_id` values so they can
   never collide with the `/concerts/new` and `/concerts/import` routes.
   Where no form supplies one (the import commit, the duplicate route),
   `generate_event_id` slugs from `title_en` and falls back to `title`:
   `slugify` strips everything outside `[a-z0-9]`, so slugging the Japanese
   title collapsed every Japanese-only concert to the `"concert"` fallback
   and minted `concert-2`, `concert-3` -- unique, but empty in a URL whose
   whole job is to be the human-readable identity. Never backfill existing
   ids; `event_id` is editor-owned once the concert exists.
7. **Injection boundaries** -- three rules, each cheap to follow and silent
   when broken:
   - **URLs**: every editor-supplied URL goes through `form_url`
     (`web/forms.py`) at the route boundary; it wraps `domain.urls.clean_url`
     and turns a bad scheme into a 422. Stored URLs land in `href`
     attributes, so a `javascript:` value that slips past executes
     in-origin. The bot layer uses `clean_url` directly, via
     `safe_button_url` in `bot/messages.py` -- never `form_url`, which
     would drag fastapi into the bot.
   - **Inline `<script>` data**: tag names and anything else user-controlled
     that reaches the picker's inline script use `| tojson`, never `| safe`,
     and the context value stays a raw Python object. Hand `tojson` the
     output of `json.dumps` and it double-encodes into a quoted string --
     the picker silently breaks while the escaping tests still pass.
   - **Inline `on*` handlers**: never interpolate user-controlled text into
     one. The browser HTML-decodes the attribute before parsing it as JS, so
     Jinja's `&#39;` escaping does not protect you. Put the value in a
     `data-` attribute and read it via `dataset`. Use `data-tag-name` /
     `data-preset-name`, not `data-name`: that one collides with the shared
     `filterChips()` selector in `base.html`. The i18n build hit the same
     rule with translated `confirm()` text: `onclick="return
     confirm(this.dataset.confirm)"` reading a `data-confirm="{{ _(...) }}"`
     attribute, never `onclick="return confirm('{{ _(...) }}')"` -- a
     translated string is just as user-controlled as a tag name once it can
     contain an apostrophe.
8. **Concert subscriptions are OVERRIDES, not records.** Whether a user
   "tracks" a concert is derived, and `tracked_concert_ids` is the single
   place that derivation lives -- do not add a second. The rule: a concert is
   tracked when a followed tag matches AND no `opted_out` row exists, OR a
   `subscribed` row exists. **No row is the common case** and means "follow
   the tag-derived default" -- so `ConcertSubscription` and `LegOptOut` are
   never backfilled; they hold only explicit user edits, exactly as group-tag
   expansion (invariant 3) materializes members lazily and persists only
   prunes. A prune STICKS across unfollow/re-follow of the tag (removed stays
   removed); Preferences surfaces the otherwise-invisible pruned count. Any
   write to a subscription or leg opt-out re-syncs that user's rules via
   `reinstate_user_rules`, the same resync `record_round_outcome` runs -- skip
   it and a pruned concert keeps reminding. `set_leg_opt_out` now runs that
   resync ITSELF: the writer owns it, the way `record_round_outcome` owns its
   own, so no call site can forget it and none should add a second (the
   suppression is a read-side pass, but `reminder_queue` is materialized, so
   without the resync the already-queued reminders were duly delivered to a
   reader who had just said "not going"). An opt-out suppresses informational
   reminders only; it never deletes a `RoundOutcome`, so opting out of a won
   ticket forfeits the reminder, not the record (the UI gates that with a heavy
   confirmation naming the loss). Per-leg opt-out suppresses a round only when
   its `applies_to` is non-empty and EVERY leg in it is opted out -- the
   per-user analogue of the every-leg cancellation rule. That is ONE predicate,
   `_round_fully_opted_out`, over ONE batched loader,
   `user_opted_out_day_ids` (both `db/service.py`): it started folded into
   `_apply_outcome_suppression` alone, which is exactly why every other surface
   never asked. Its consumers are the planner's round pass, `sync_rule`'s DAY
   candidates (the day half -- without it an `event_start` rule planned
   show-start rows that reached the queue, and through it the `.ics` feed, the
   show-start DM and `/mydeadlines`), Home's `my_deadline_rows`, `board_cards`'
   LIVE card set, the concert page's `_needs_you` veto and catch-up dialog
   (via `RoundRow.opted_out`), and `/setup`'s rows and tallies. Three
   deliberate NON-consumers, so nobody "fixes" them: Discover's pills (event
   state is a fact about the catalogue, and the standing pill renders
   `RoundOutcome` records, which an opt-out never touches), the concert
   page's row rendering and capture gates (the page shows the whole campaign
   and is where you opt back in), and that same page's settled-round fold --
   `_split_leg_rounds` consumes `_wants_you`, not `_needs_you`, so an open
   round on a fully opted-out leg stays UNFOLDED on its dimmed leg, on the
   same reasoning: the page is where you opt back in and the fold is
   presentation. Partial opt-out survives everywhere, exactly as partial
   cancellation does.

## Migrations (SQLite gotchas — these have bitten before)

- `Base.metadata` has a NAMING_CONVENTION; keep it. SQLite runs migrations
  in batch (table-rebuild) mode which refuses unnamed constraints.
- **The live DB predates that convention, and tests cannot see it.** Tables
  created by older migrations (`concerts`, `tags`) carry anonymous
  constraints -- a bare `FOREIGN KEY(created_by) REFERENCES users(discord_id)`,
  an unnamed `UNIQUE (name)` -- while tables created later (`concert_audit`)
  are named. Every test DB is built from `Base.metadata`, so everything is
  named there and the divergence is invisible to the whole suite. A migration
  calling `drop_constraint` therefore passes locally and dies on the server
  with `ValueError: No such constraint: 'fk_...'` (this shipped once).
  Any migration touching `drop_constraint` must (a) pass
  `naming_convention=NAMING_CONVENTION` into `batch_alter_table` so Alembic
  names anonymous constraints during reflection, and (b) be tested against a
  legacy-shaped fixture, not a metadata-built one -- see
  `tests/test_migration_legacy_anonymous_constraints.py`, which hand-writes
  the real server DDL. Its fixture covers only the four tables that migration
  touched; a migration hitting other legacy tables needs its own DDL.
- After autogenerate, ALWAYS edit the revision: replace
  `app.db.models.UTCDateTime()` with `sa.DateTime()` and remove the
  `import app.db.models` line.
- `alembic.ini` and other config files must stay ASCII-only (the owner's
  Windows machine uses a GBK locale; em-dashes in configs crash it).
- The dedupe index on reminder_queue uses coalesce() because SQLite treats
  NULLs as distinct in unique indexes. Don't "simplify" it.
- SQLite's `trim()` strips only U+0020; Python's `str.strip()` is
  Unicode-aware. Any migration matching text the app wrote through `.strip()`
  must pass an EXPLICIT trim character set including U+3000 (the ideographic
  space) or the two disagree on exactly the Japanese data this app is full of
  -- `789bbcc95bc3`'s venue-name backfill does.

## Testing conventions

- Async tests via pytest-asyncio auto mode — `await` directly, never
  `run_until_complete` inside a test.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener
  (production does; cascades silently don't fire without it).
- Every page must have at least one logged-in GET render test — a missing
  one shipped a 500 once (template context drift).
- Discord is never imported in service tests; button/scheduler behavior is
  tested through service functions and fake bot objects.
- Slash-command cogs (`bot/cogs/*.py`) ARE tested directly (see
  `tests/test_bot_reminders.py`): call `Cog.command_name.callback(cog, ...)`
  (the `app_commands.Command` wrapper exposes the original coroutine as
  `.callback`) with a minimal fake `discord.Interaction` (just `.user.id`/
  `.name` and an async `.response.send_message` that records its args), and
  monkeypatch the cog module's `SessionMaker` to a real in-memory async
  engine -- same fixture shape as the service-layer tests, no Discord
  gateway involved.

## UI conventions

- Sentence case everywhere ("Add group", not "add group").
- The 🌐 language switcher is the concept demo's CYCLE CHIP (`base.html`'s
  `.langform`/`.langtoggle`): one bordered chip showing the current
  language; clicking posts the NEXT language in the EN → 中文 → 日本語 cycle
  as a plain `<form method="post" action="/language">`, so it works with JS
  disabled and renders on every page, signed in or out. A dropdown menu
  shipped first and was replaced at the owner's request -- don't bring it
  back. Language NAMES (EN/中文/日本語) are never translated -- a visitor
  picking their language needs to recognize it before they can read
  anything else.
- **Theming**: the full design-token layer (`--paper`, `--accent`, the
  `*-wash` set, `--raise`, `--chip`, `--shadow`, ...) lives in `style.css`'s
  `:root`. Dark mode is defined BOTH ways -- `@media (prefers-color-scheme:
  dark)` (the OS default when the user has never chosen) AND
  `:root[data-theme="dark"]`/`[data-theme="light"]` (the header toggle
  stamps `data-theme`, which wins on specificity and persists to
  `localStorage`). `base.html` stamps the saved theme in `<head>` before
  first paint -- do this in `<body>` or after CSS loads and every page
  flashes the wrong theme on load. Style new components against both
  directions, not just light.
- Deadline TIMES render as two lines via `fmt_dual_lines`/the `dual_lines`
  Jinja global -- a bold weekday+day+month line, then a "HH:MM JST, HH:MM
  local" time line -- never a flat one-line string on the web (that shape is
  `fmt_dual`, kept only for Discord embeds/plain text, which can't do two
  lines). Performance DATES (a concert's start day, not a deadline) use
  `fmt_day_month`/`day_month` instead: day-month only, no zone, no dual
  apparatus -- a date is a fact about the world, invariant 1 governs
  deadlines you must act by, not this.
- Tag chips are the universal element; "+ Add x" buttons share the exact
  chip silhouette. Pickers are native <dialog> white cards: header (title +
  ×), search, chip list; no footer; backdrop-click and Esc close --
  backdrop-close comes ONLY from base.html's global drag-safe handler; never
  add a local `e.target === dlg` click handler to a dialog (that shipped the
  drag-out-closes-the-dialog bug twice; a sweep test in
  `test_theme_and_tokens.py` now forbids it).
- Editor leg/round cards render through the shared partials
  `_editor_leg_card.html`/`_editor_round_card.html` -- concert_new,
  concert_edit and import_preview (their loops AND `<template>` blocks) all
  use them. Never hand-roll a card copy again; that six-site duplication is
  exactly what the coherence pass removed. Card anatomy: ja label on the
  top row, EN/中文 on the always-visible `.vary` row, fields below.
- Destructive card actions live in the kebab menu (`details.kebab`,
  top-right; menu items keep the `data-remove-leg`/`data-remove-round`
  hooks). It is the app's ONLY overflow menu and stays single-purpose:
  destructive actions only, never a place to bury regular controls. The
  inline × beside the Cancelled toggle was removed deliberately (owner
  call) -- do not reintroduce it. (2026-07-24: folding the concert header's
  Edit/Export into the kebab was proposed and rejected -- the rule stays.)
- Callouts come in two shapes (G2, 2026-07-24): `.edgecard` (raise ground,
  left edge in the tone colour -- ongoing state; `.dg`/`.ok`) and `.banner`
  (wash ground, full border -- needs attention; `.warn`/`.dgr`). Anatomy
  classes (`.standing`, `.next`, `.upgradebox`, `.feedbox`, `.danger`,
  `.danger-row`) compose the shape and keep only their layout. Don't invent
  a third callout shape. Radiuses: 3px default, 999px chips, 4px overlay
  cards, 50% circles, bottom sheets `14px 14px 0 0` (documented at the top
  of `style.css`). Type ramp is 400/600/700 only. Motion budget: one 150ms
  card-lift hover plus the functional `#hxbar` progress bar -- nothing
  decorative (owner ruling, 2026-07-24).
- **A `<details>` inside a swappable region carries a `data-fold` key.**
  These regions (`#concert-rounds`, `#deadline-rows`) are swapped whole by
  `outerHTML`, so every fold in one is a NEW element rendered from scratch
  and comes back closed -- a reader who expanded a leg's round history and
  then toggled a different leg off watched every fold on the page snap shut.
  `base.html` collects the open keys inside the request target on
  `htmx:beforeRequest` and reopens the matching folds on `htmx:afterSettle`
  (per-request, keyed off the detail object's `xhr`, since two requests can
  overlap). It only ever OPENS. Keys are server-rendered ids/event_ids --
  `leg-{day_id}`, `block-{event_id}`, `more-concerts` -- never user text.
  `open_round_id` is the OTHER half, not a duplicate: it is server-rendered,
  so it survives with JS off, and it reopens the fold that OWNS a round a
  capture press just wrote. The client half generalises to swaps that write
  no round (a leg opt-out) with no per-caller plumbing. Keep both; reaching
  for `open_round_id` to solve a general fold reset is the trap.
- The sentence-style reminder builders (welcome, Preferences) render
  through locale-ordered slot patterns: ONE translatable pattern msgid per
  builder (e.g. ja 「{anchor}の{offset}{direction}に通知。」), split by
  `domain/sentence.py:split_slots` (raises on unknown slots) and rendered
  by the `sentence_slots` Jinja global -- text parts escaped, only the
  server-built selects pass as Markup (invariant 7). Translators own the
  word order; option labels (offsets, anchors) are their own msgids
  translated per-request in `routes/welcome.py`. A dropped placeholder is
  caught by `test_i18n_catalogues.py`'s placeholder-integrity tests.
  Welcome's JS adds rows by cloning the server-rendered
  `<template id="remrule-template">` -- never by assembling English DOM.
- Tile display rules: franchise+group → "F · G"; group only → G; artists
  only → artist chips; >1 venue → "📍 Multiple".
- The concert page's **Performing panel is per-group clusters**
  (`.pcluster`): one block per attached GROUP -- its chip on a `.pclabel`
  row, that group's attached performers in the `.chiprow` beneath -- then an
  unlabelled trailer block for performers in no attached group. Three owner
  rulings hold it: a performer in several attached groups appears under EACH
  (the repetition is information), clusters never fold (the panel is
  reference, not a to-do), and the header's DISTINCT performer count
  disappears entirely at zero rather than reading "0 performers". A
  member-less group keeps its label row and emits NO `.chiprow` -- an empty
  one still pays `.pclabel`'s bottom margin, and `:empty` can't reach it past
  the template's own whitespace.
- **Draw a relationship only when BOTH of its ends are attached to this
  concert.** ONE rule, applied twice (2026-08-01), and both halves are derived
  per concert, never stored:
  - a CHARACTER and her seiyuu render as one **split pill** (`.mchip`, two
    halves each its own link) when she is attached too; the seiyuu is then
    dropped from the standalone list because she is rendered inside the pill.
  - a SUBUNIT nests under its parent (`.pcluster.sub` on the concert page,
    `.grow2.sub` on the Tags page) when the parent is an attached GROUP.
    "Attached GROUP", not "attached tag": a group's parent is usually a
    FRANCHISE, and asking the looser question made every group on a franchise
    bill read as depth 1, emptied `roots`, and made the whole panel VANISH.
  Either end alone renders exactly as it did before -- a lone character is a
  plain chip, a lone subunit an ordinary top-level cluster. That is why the
  split shape was chosen over an inline `如月千早（今井麻美）` gloss (owner, from
  four mockups): the merge is CONDITIONAL, and the shape makes the difference
  read as meaningful rather than as inconsistent styling. A seiyuu attached in
  her OWN right is listed as herself in the unlabelled trailer -- she is not in
  `members_by_group` once a group's members are characters, so the existing
  code already does the right thing. The pill's box is DERIVED from
  `.performers .chip`'s own padding/line-height/border rather than tuned to
  match it (measured: 28.72px both, both themes; a chip SETS `line-height: 1.5`
  and does not inherit it, so a half that inherits comes out 2.5px short), and
  a parity test compares rule to rule so moving the chip desyncs loudly.
- **The Tags directory walks `parent_id` in Python, not in Jinja**
  (`tag_directory_context` returns a FLAT, pre-ordered list of
  `(group, members, depth)`). A template recursion over a children map would
  HANG on a `parent_id` cycle, and a cycle is reachable — rows predate
  `would_create_tag_cycle`. The `walked` set terminates the walk, and a
  leftover pass appends whatever it never reached, which is the property that
  actually matters: **every group renders exactly once, whatever its
  `parent_id` says**. Before that walk existed a GROUP under a GROUP fell out
  of the chips directory entirely and took its members with it (they already
  counted as grouped, so `ungrouped_performers` skipped them too), and a
  signed-in non-editor saw neither anywhere on /tags.
- **Membership is resolved SERVICE-side**, in `db/service.py`'s
  `performer_clusters` (one batched `tag_members` query, pinned by a
  statement-count test), and awaited in the route -- never in the template.
  `Tag.members` is a lazy self-referential m2m and a lazy load during async
  template rendering is a `MissingGreenlet` 500, the failure this project has
  already shipped once. Its caller precondition is that `concert.tags` is
  already eager-loaded, for the same reason.
- Times always render dual: JST + the user's timezone.
- VENUE tags filter by `region` (sidebar groups venues into regions like
  "Kanto"/"Kansai"/"Other"; toggling a region (de)selects every venue tag id
  in it) — filtering by one exact venue was explicitly ruled out as unhelpful.
  A leg's venue IS a VENUE tag now (see the `src/app/db/` note above) and the
  concert's venue tags -- which is what this filter reads -- are the rollup of
  its legs', so a concert whose legs carry no venue tag shows no venue
  anywhere and is invisible to this filter.
- **Home vs Discover** -- the old combined index is split in two by the
  question each page answers. `/` (`home.html`, the handler in `web/app.py`)
  is Home: "where do I stand", personal and login-gated, four blocks in order
  -- Up next, the campaign board, Coming up, a Discover teaser. Signed out it
  is a real landing page instead -- hero, a "how it works" section, a static
  illustrative campaign board (sample data, not the viewer's, since there is
  no viewer), a real Discover taste pulling live public cards, and the
  sign-in CTA. The first block is headed "Up next", NOT "Closes
  next": it picks the soonest row with a round behind it whatever anchor that
  row carries, so the header must stay moment-agnostic while the body names
  the moment. Narrowing the pick to `Anchor.CLOSES` to justify the old header
  was considered and rejected -- an opening round or a results announcement
  genuinely needs attention, and filtering would hide real urgency.
  **Coming up is per-concert BLOCKS, not a flat list**: `my_deadline_blocks`
  collapses each round to its soonest future anchor and groups what is left
  under one block per concert -- a header naming the concert once, the LEAD
  row, and the rest behind a fold -- so the row budget counts concerts, not
  anchors. Which row leads is `_wants_you`, the SAME predicate the concert
  page's "Next for you" uses (`_needs_you` is a thin adapter over it): keep
  them one rule, don't grow a second. Both folds -- "+N more rounds" in a
  block, "+N more events" past `VISIBLE_BLOCKS` -- are native `<details>`
  and are presentation ONLY: every folded row is in the DOM with its
  capture form intact, since rendering on expand would need a round trip
  and would make the fold a second silent limit beside
  `DEADLINE_ROWS_LIMIT`. Discover's "Coming up soon" list stays FLAT on
  purpose -- it calls `upcoming_deadlines` directly, and chronological is
  the right shape for a catalogue nobody has standing on. `/discover` (`routes/discover.py` +
  `discover.html`) is the catalogue: "what's on", and it is **public** --
  `current_user`, not `require_user`, the only content page in the app an
  anonymous visitor can reach. Header nav is Home / Discover / Tags and
  nothing else; the active item carries `aria-current="page"`, which is also
  what the CSS styles off.
- **Capture actions live on Coming up rows, never on board cards.** A
  deadline row is exactly ONE round on ONE leg, where "I have applied" has a
  single meaning. A board card is a whole campaign with a multi-rung ladder,
  where the same button is ambiguous -- applied to which round? A destructive
  control sitting inside something you are scanning to read is also a mode
  error. Do not "improve" the board by adding buttons to it.
  Two gates govern WHICH buttons a row offers, both resolved in
  `service.my_deadline_rows` (round timing is not presentation) and both
  load-bearing because `upcoming_deadlines` emits one row per future ANCHOR,
  so a single round can produce three or four rows. `can_capture` -- the
  round has opened -- because you cannot have applied to a round that has
  not opened, and recording APPLIED is irreversible (`record_round_outcome`
  refuses to overwrite a starting state). `can_report_result` -- outcome is
  APPLIED and the results time (or, failing that, the close) has passed --
  which is the web's ONLY exit from APPLIED: WON/LOST are also DM buttons,
  but a `dm_blocked` user or a `bot_enabled=False` deploy has no DM to
  press, and without them the board's four columns are reachable as two.
  PAID stays offered only from WON. Never "fix" this by relaxing
  `record_round_outcome`'s sequence rule -- the gates belong on the read
  side.
- **The board CAPS its ladder and never expands it.** `domain/board.py`'s
  `visible_rungs` keeps at most `VISIBLE_RUNGS` (2) rungs -- the one whose
  STANDING explains the card's column (ranked by `column_for`'s own
  precedence, never by position) plus the next actionable one -- and the
  remainder renders as a plain `.rmore` count line, deliberately NOT a
  `<details>`: uniform card height is what makes four columns scan as a
  board, and nothing on a card is interactive anyway (capture stays off
  cards, per the rule above). Surviving rungs keep their ORIGINAL ladder
  numbers; a rung's mark IS its place in the full ladder.
- **The concert page folds settled rounds per LEG**, one
  `<details class="moreround">` each (`service._split_leg_rounds` decides
  what stays up; the summary carries `+N more round(s)` plus one `.fchip`
  per state). Its rule is `_wants_you`, which now drives THREE surfaces --
  Home's block lead, the concert page's "Next for you" strip, and this fold
  -- so change it in one place and check all three. A secured leg keeps its
  receipt visible; the fold is presentation only, so a folded round's
  capture form stays in the DOM and works.
- Discover carries **one** status pill per card, merging the event's round
  state with the viewer's standing (`service.discover_statuses`). The
  standing REPLACES the countdown rather than sitting beside it, and the tone
  says who owes the next move: ok = you are covered, danger = you owe an
  action, quiet = you have no standing. Signed out there is no standing to
  merge, so the pill is the event state alone.
- Discover's three filters -- the tag/region chips, the free-text search box
  (matches title, title_en, every attached tag's name, and a free-text-venue
  fallback when no VENUE tag exists, all case-insensitive), and the
  round-status facet (Open now / Opening soon / Not tracking) -- combine as
  AND, not OR: all three narrow the same tile set together, plus the "Coming
  up soon" deadline list below the grids (each `<li>` carries the same
  `data-tags`/`data-search` attributes a tile does; it has no `data-status`,
  so the facet never hides a row). All three follow one contract: the initial
  state is computed server-side so there is no flash of wrongly shown tiles,
  every subsequent change is client-side off `data-tags`/`data-search`/
  `data-status` with no round trip, and each control stays a real `<a href>`
  or a real GET `<form>` so it degrades to slower server-side filtering with
  JS disabled. Add a fourth filter the same way.
- **Mobile is a retrofit, not a second design**: every phone rule lives in
  a banner-commented `@media (max-width: 700px)` section at the end of the
  file -- desktop pixels stay untouched by construction, since nothing
  outside those blocks may change. There are TWO such top-level blocks, not
  one: the main retrofit section, and a second one right after it holding
  the `.fsheet` bottom-sheet presentation alone (its own banner explains
  why it is separate -- it used to be a 760px query, and moved to 700 when
  `.layout`'s collapse went to 1040). New phone rules go in the FIRST
  block; the second stays single-purpose. Both are counted by
  `test_theme_and_tokens.py`'s top-level query pin.
  **The tablet band (701-1040px) follows the same discipline**: one
  banner-commented `@media (min-width: 701px) and (max-width: 1040px)`
  section (just before the phone section) holds every band rule -- the
  compact one-row header (wordmark secondary hidden, username hidden,
  icon-only Preferences/Sign out via base.html's `.nav-lbl`/`.nav-ico`
  spans), the swipeable 280px-column campaign board (the phone board's
  mechanics at tablet numbers), the `.peek` 2-col, the coming-up rows'
  data-happens fold, and the inline filter-sheet panel. The old scattered
  1024/960 breakpoints died into it; the 900 (`.rnd2`) and 860 (`.plyt`)
  queries deliberately remain standalone (they serve phones too);
  `test_theme_and_tokens.py` pins the exact top-level max-width query
  count so scattered-breakpoint drift fails CI.
  The `.fsheet`/`.layout` coupling now sits at 1040/1041: `.layout`
  collapses at 1040 and the `.fsheet` summary-flip is `min-width: 1041`,
  so the two-column desktop and the sidebar presentation flip on the
  identical boundary -- the invariant is "same boundary", not any magic
  number. TWO further boundaries govern the sheet: discover.html's
  collapse-on-load JS runs at <=1040 (must match the summary-visible
  range), while the bottom-sheet OVERLAY presentation is phone-only at
  <=700 -- in-band the sheet opens as an inline panel.
  Narrow phones (<=380px) get a NESTED `@media (max-width: 380px)` inside
  that same 700px block rather than a second top-level one, so the retrofit
  stays one section. It currently holds a single rule: drop the header's
  `dekimasen.app` wordmark, keeping the `できません` mark. That exists for the
  language chip -- it is the one header item whose width follows the
  language (EN -> 中文 -> 日本語), and since `.site-in` is `nowrap` a
  too-narrow bar squeezes the auth cluster instead of overflowing it, so a
  CJK label breaks between characters and the chip folds onto a second line,
  growing the whole header a row (measured: 59px -> 77px at 365px, -> 112px
  at 320px). `nav.auth { flex: 0 0 auto }` + `white-space: nowrap` on the
  chip pin it; hiding the wordmark is what buys them the room. Don't remove
  any of the three.
  Three phone-only patterns recur: a fixed bottom `.tabbar` (Home/Discover/
  Tags/Me or Sign in, `aria-current` on the active tab exactly like the
  desktop nav, keyed off the same `nav_page`) replacing the header nav; an
  editor-only `.fab` ("+", bottom-right) replacing the header's "+ Add";
  and dialogs (`<dialog>`, including `.picker`/`.prune`/`.tagdlg`) becoming
  bottom sheets (anchored to the bottom edge, rounded top corners, `max-
  height: 78dvh`) instead of the desktop's centered card. `docs/superpowers/
  demo/dekimasen-mobile-demo.html` (static frames, reference CSS values)
  and `dekimasen-mobile-live.html` (interactions) are the mobile design
  reference, alongside the existing `dekimasen-demo.html`/`dekimasen-
  onboarding-demo.html` for desktop. The 3px-radius guard
  (`test_style_uses_3px_radius_not_6or8`) still applies inside the mobile
  section -- phone cards and chips use the same `border-radius: 3px` as
  desktop; only the bottom-sheet corners deliberately deviate (`14px 14px
  0 0`, a sheet-specific shape, never 6px or 8px).
- **Measure a layout bug; do not reason about it.** Before diagnosing any
  layout, overflow or breakpoint problem, put the real app in a real viewport
  at a real width and read the numbers off it -- a seeded dev server in an
  iframe harness, the way `docs/superpowers/demo/_tablet_harness.html` was
  used to build the 701-1040px band, and the way the header measurements
  above (59px -> 77px -> 112px) were obtained. Reasoning from the CSS
  shipped a confidently wrong fix twice. The tell is a sentence of the shape
  "this must be overflowing because..." with no measurement in it.

## Deploy

Server: Lightsail Ubuntu 24.04, app at `~/app`, systemd unit
`concert-reminder`, Caddy with a Cloudflare Origin cert. Ritual:
`cd ~/app && git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder`
Caddyfile changes additionally need:
`sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`
Logs: `journalctl -u concert-reminder -f`. Health: `/healthz` (UptimeRobot
keyword-monitors `"ok":true`; goes false after 3 missed scheduler ticks).
Nightly S3 backups via `deploy/backup.sh` (cron, 30-day lifecycle).
Full runbook: `docs/deploy.md`. Never commit `.env`; secrets live only on
the server and in the owner's local copy.

## Owner context

The owner is technically comfortable but writes little code day to day,
and works on Windows PowerShell 5.1 (no `&&` chaining — use `;` or separate
lines in any commands you give him). Explain the why behind non-obvious
changes. He cares about: correct JST handling, the tag semantics above, and
the UI staying clean — when in doubt about UX, ask, don't assume.
