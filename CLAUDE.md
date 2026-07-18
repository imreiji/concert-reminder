# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## dekimasen.app (concert-reminder)

Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets). One Python process runs three things on a
single asyncio loop: discord.py bot, FastAPI web (Jinja2 + htmx), and a 60s
scheduler tick. SQLite + SQLAlchemy async + Alembic. Live at dekimasen.app
(AWS Lightsail behind Cloudflare). 299 tests as of this writing (past the
Phase 12 roadmap in README.md — event_id/edit-page, venue regions, .ics
export, ramen.events import, a personal calendar-feed subscription,
free-text concert search, a personalized `/mydeadlines` Discord command, a
per-concert edit history, a per-leg cancelled status, a redesigned Tags
page (search, hierarchy, dialog-based editing, rename, retroactive
artist-to-active-events apply), an index-page reorg (open-and-upcoming
bucketing plus a global chronological deadline list), and surfaced
undeliverable-DM feedback (a sitewide banner plus a synchronous test-DM
diagnostic) have shipped since).

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
- CI (`.github/workflows/ci.yml`) runs `uv sync`, `ruff check .`, `pytest -q`
  on every push/PR to `main` — the same two gates as above, nothing extra.

## Layout

- `src/app/domain/` — pure logic, NO I/O, no discord/fastapi/sqlalchemy
  imports. Reminder math in `reminders.py`, JST↔UTC conversion in
  `timezones.py`, ramen.events HTML parsing in `ingest.py` (takes an HTML
  string, returns a draft — no httpx call itself), and `.ics`/YAML export
  formatting in `ics_export.py`/`yaml_export.py`.
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
  loosen any of them. Reminder-rule add/delete lives in
  `routes/reminders.py` (split out of `concerts.py`; renders via
  `concerts.render_rules_fragment`), and the `/me/timezone*` routes live in
  `routes/preferences.py` with the other per-user preference routes.
- `src/app/scheduler/` — the tick loop that delivers DMs.
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
- Bot and web NEVER contain business logic; they call `db/service.py`.
- `docs/superpowers/specs/` + `plans/` — date-prefixed design specs and
  implementation plans; each recent feature (cancelled legs, Tags redesign,
  index reorg) committed one of each before code. Follow that pattern for
  substantial features. `docs/codebase-review-2026-07-17.md` records a
  full-codebase review and the fixes it drove.

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
   `group_rounds_by_day()` and every `applies_to` consumer rely on the day
   row still existing. Rounds have no status of their own; a round counts
   as cancelled when every day in its `applies_to` is cancelled.
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
   No separate CSRF token: mutating routes rely on `SameSite=Lax` cookies
   (`web/app.py`'s `SessionMiddleware`). Deliberate for an app this size —
   don't read it as a gap to fill or bolt a token system onto.
   Any future personal-secret-link feature (the calendar feed is the first)
   should reuse the same shape: `secrets.token_urlsafe`, only the SHA-256
   hash stored (`User.calendar_token_hash` mirrors `WebSession.token_hash`),
   the raw value shown to the user exactly once and never persisted
   anywhere retrievable — recovery is "generate a new one," not "look up
   the old one."
6. **`event_id` vs `id`**: every FK targets `Concert.id` (internal PK), but
   URLs use the editor-chosen, unique `event_id` string instead. `"new"` and
   `"import"` are reserved and rejected as `event_id` values so they can
   never collide with the `/concerts/new` and `/concerts/import` routes.

## Migrations (SQLite gotchas — these have bitten before)

- `Base.metadata` has a NAMING_CONVENTION; keep it. SQLite runs migrations
  in batch (table-rebuild) mode which refuses unnamed constraints.
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
- Tag chips are the universal element; "+ Add x" buttons share the exact
  chip silhouette. Pickers are native <dialog> white cards: header (title +
  ×), search, chip list; no footer; backdrop-click and Esc close.
- Tile display rules: franchise+group → "F · G"; group only → G; artists
  only → artist chips; >1 venue → "📍 Multiple".
- Times always render dual: JST + the user's timezone.
- VENUE tags filter by `region` (sidebar groups venues into regions like
  "Kanto"/"Kansai"/"Other"; toggling a region (de)selects every venue tag id
  in it) — filtering by one exact venue was explicitly ruled out as unhelpful.
- The index page's tag filter and its free-text search box (matches title +
  title_en, case-insensitive) combine as AND, not OR — both narrow the same
  tile set together, AND the "Coming up soon" chronological deadline-list
  section below the tile grids (each `<li>` carries the same `data-tags`/
  `data-search` attributes a tile does). Same client-side-first pattern as
  tag filtering: every tile (and deadline-list row) carries a `data-search`
  attribute so typing re-filters instantly with no round trip; the search
  `<input>` still sits in a real GET `<form>` so it degrades to a normal
  server-side search with JS disabled.

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
