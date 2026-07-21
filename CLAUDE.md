# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## dekimasen.app (concert-reminder)

Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets). One Python process runs three things on a
single asyncio loop: discord.py bot, FastAPI web (Jinja2 + htmx), and a 60s
scheduler tick. SQLite + SQLAlchemy async + Alembic. Live at dekimasen.app
(AWS Lightsail behind Cloudflare). 897 tests as of this writing (past the
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
localized dates, and parallel-column UGC translation -- have shipped since).

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
  raises `UnsafeURLError`; see invariant 7).
- `src/app/db/` — models, session, and `service.py` (all business logic that
  touches the DB; discord-free so it's testable).
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
  loosen any of them. Its preview (`import_preview.html`) is built in the
  same day-card/round-card/leg-chip vocabulary as `concert_new.html`/
  `concert_edit.html`, and `import_commit` binds a parsed round's
  `applies_to` to legs via the same `round_legs`/`day_key`/
  `parse_round_legs`/`key_to_day_id` path `create_concert` uses -- before
  this, the flat import form could not express a round spanning more than
  one leg. Reminder-rule add/delete lives in
  `routes/reminders.py` (split out of `concerts.py`; renders via
  `concerts.render_rules_fragment`), and the `/me/timezone*` routes live in
  `routes/preferences.py` with the other per-user preference routes.
  `web/forms.py` holds the HTTP-boundary wrappers around domain validators
  (currently `form_url`) -- its own module so routes/concerts.py,
  routes/tags.py and routes/imports.py can all import it cheaply.
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
  time. `gettext_in(locale, msg)` is the explicit-locale escape hatch for
  text composed before a per-recipient locale is known (e.g. `NoticeContext`,
  built once for many recipients up front). `loc_field(obj, field, locale)`
  resolves a UGC field's viewer-locale variant: en → `{field}_en`, zh →
  `{field}_zh`, ja → the original column (Japanese IS the source of truth,
  there's no `_ja` column); an empty string counts as unfilled and falls
  through; there is no cross-locale chaining (zh never falls back through en
  to the original). Editing existing English copy must keep the msgid
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
  and the **design source of truth** for it: `dekimasen-demo.html` (the
  reconciliation reference for Home/Discover/concert/editor/tags/preferences/
  setup) and `dekimasen-onboarding-demo.html` (the signed-out landing, the
  new-user flow, import + import preview, retroactive-apply, legal). Review
  UI/UX changes against the matching demo; when the shipped design
  deliberately moves, update that demo so it stays the reference. Both are
  self-contained single-file mockups on the same design tokens the app ships.

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
   `RoundOutcome` (per-user, per-round lottery progress) layers a second,
   per-user suppression pass onto the same `sync_rule` candidate-list
   filtering, orthogonal to cancellation — see
   `db/service.py`'s `_apply_outcome_suppression`.
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
4. **Notifications**: new-event notices go through the `notifications`
   table (DB outbox drained by the scheduler) — never send DMs directly
   from web routes. One narrow, explicit exception: `POST /me/test-dm`
   (`web/routes/preferences.py`) sends synchronously and reports the
   result inline — a manual, user-initiated, low-volume diagnostic
   action is a different animal from a system-initiated notice, which
   must still go through the outbox for its retry/ordering/audit
   properties. Don't extend this carve-out to anything else without
   discussing it first.
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
   it and a pruned concert keeps reminding. An opt-out suppresses informational
   reminders only; it never deletes a `RoundOutcome`, so opting out of a won
   ticket forfeits the reminder, not the record (the UI gates that with a heavy
   confirmation naming the loss). Per-leg opt-out suppresses a round only when
   EVERY leg in its `applies_to` is opted out -- the per-user analogue of the
   every-leg cancellation rule, folded into `_apply_outcome_suppression`.

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
  ×), search, chip list; no footer; backdrop-click and Esc close.
- Tile display rules: franchise+group → "F · G"; group only → G; artists
  only → artist chips; >1 venue → "📍 Multiple".
- Times always render dual: JST + the user's timezone.
- VENUE tags filter by `region` (sidebar groups venues into regions like
  "Kanto"/"Kansai"/"Other"; toggling a region (de)selects every venue tag id
  in it) — filtering by one exact venue was explicitly ruled out as unhelpful.
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
  genuinely needs attention, and filtering would hide real urgency. `/discover` (`routes/discover.py` +
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
  `style.css`'s single `@media (max-width: 700px)` section at the end of
  the file (banner comment marks its start) -- desktop pixels stay
  untouched by construction, since nothing outside that block may change.
  The one documented exception is `.fsheet` (Discover's filter sheet),
  which switches at 760px instead because it must track `.layout`'s own
  collapse point exactly (also 760px) -- splitting the two breakpoints
  would open a 701-760px band where `.layout` has already stacked to one
  column but `.fsheet` still thinks it's in two-column desktop mode.
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

The owner (imreiji) is a pilot, comfortable technically but rusty at
coding, on Windows PowerShell 5.1 (no `&&` chaining — use `;` or separate
lines in any commands you give him). Explain the why behind non-obvious
changes. He cares about: correct JST handling, the tag semantics above, and
the UI staying clean — when in doubt about UX, ask, don't assume.
