# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## dekimasen.app (concert-reminder)

Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets). One Python process runs three things on a
single asyncio loop: discord.py bot, FastAPI web (Jinja2 + htmx), and a 60s
scheduler tick. SQLite + SQLAlchemy async + Alembic. Live at dekimasen.app
(AWS Lightsail behind Cloudflare). 196 tests as of this writing (past the
Phase 12 roadmap in README.md — event_id/edit-page, venue regions, .ics
export, and ramen.events import have shipped since).

## Commands

- Run everything: `uv run python -m app.main` (dev: leave `DISCORD_TOKEN`
  empty in `.env` → web-only mode, bot and scheduler DMs disabled)
- Tests: `uv run pytest -q` — MUST pass before any commit
- Single test: `uv run pytest tests/test_service.py::test_name -q`
- Lint: `uv run ruff check .` — MUST be clean before any commit
- New migration: `uv run alembic revision --autogenerate -m "msg"`, then
  review it (see Migrations below), then `uv run alembic upgrade head`
- Demo data: `uv run python scripts/seed_demo.py`
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
  before literal segments.
- `src/app/scheduler/` — the tick loop that delivers DMs.
- Bot and web NEVER contain business logic; they call `db/service.py`.

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
3. **Group tag expansion** (agreed with the owner, do not change): attaching
   a GROUP tag to a concert materializes its members AT THAT MOMENT only.
   Editors prune non-performers; removed members stay removed; detach +
   re-attach re-expands; group membership edits never rewrite existing
   concerts. The creation form passes `expand=False` because its explicit
   artist list is authoritative.
4. **Notifications**: new-event notices go through the `notifications`
   table (DB outbox drained by the scheduler) — never send DMs directly
   from web routes.
5. **Auth**: three tiers — admin, editor, user. Admins = `ADMIN_WHITELIST`
   env (Discord IDs), env-only by design (no runtime UI; edit `.env` +
   restart). Editors = `EDITOR_WHITELIST` env (permanent bootstrap/
   break-glass set) OR the `users.is_editor` DB flag, which admins can
   toggle live from the preferences page or `/promote-editor` /
   `/demote-editor` Discord commands. Admins automatically pass editor
   checks too. Sessions are DB-backed sha256 token hashes (revocable).
   Ownership checks 404, not 403, on other users' presets/subscriptions.
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
