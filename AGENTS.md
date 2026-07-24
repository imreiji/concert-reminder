# AGENTS.md

Guidance for AI coding agents working in this repository. `CLAUDE.md` carries
the same content with more historical narrative; keep the two consistent when
you change conventions.

## Project overview

**dekimasen.app** (package name `concert-reminder`) is a Discord bot + web app
that tracks the deadline chain around Japanese concerts — lottery rounds
(先行抽選), serial-code eligibility sales, general sale, per-day stream tickets
— and DMs users N days before/after each deadline.

One Python process runs three things on a single asyncio loop:

- a discord.py bot,
- a FastAPI web UI (Jinja2 + htmx),
- a 60-second scheduler tick that delivers DMs.

State is a single SQLite file (`app.db`) via SQLAlchemy async + Alembic. It is
live at dekimasen.app on AWS Lightsail behind Cloudflare + Caddy.

## Tech stack

- Python >= 3.11, package/env manager: **uv** (`uv sync`, `uv run ...`)
- discord.py, FastAPI + uvicorn, Jinja2 + htmx, SQLAlchemy 2 (async) +
  aiosqlite, Alembic, pydantic-settings, httpx, BeautifulSoup4, PyYAML,
  itsdangerous, Babel/gettext i18n (English/Mandarin/Japanese)
- Dev tools: pytest + pytest-asyncio, ruff
- Entry point: `python -m app.main` (package lives under `src/`, hatchling
  build backend)

## Commands

- Install: `uv sync` (creates `.venv`)
- Configure: `cp .env.example .env`; leave `DISCORD_TOKEN` empty for web-only
  dev mode (bot and scheduler DMs disabled)
- Run: `uv run python -m app.main` → http://localhost:8000
- Tests: `uv run pytest -q` — **MUST pass before any commit**
- Single test: `uv run pytest tests/test_service.py::test_name -q`
- Lint: `uv run ruff check .` — **MUST be clean before any commit**
- New migration: `uv run alembic revision --autogenerate -m "msg"`, then edit
  it (see Migrations below), then `uv run alembic upgrade head`
- i18n catalogue update after changing translatable strings:
  `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .`, then
  `uv run pybabel update -i messages.pot -d src/app/translations -l ja` and
  again with `-l zh`, fill in the new/fuzzy msgstrs by hand, delete
  `messages.pot`. `.mo` files are never committed — `i18n.py` compiles `.po`
  in memory at first use.
- Bot dev: set `DEV_GUILD_ID` to a test server's ID so slash commands sync in
  seconds instead of the up-to-an-hour global sync.
- CI (`.github/workflows/ci.yml`) is exactly `uv sync`, `ruff check .`,
  `pytest -q` on push/PR to `main`.

## Code layout

```
src/app/
  config.py      env-driven settings; ADMIN_WHITELIST / EDITOR_WHITELIST live here
  main.py        single-process entrypoint (bot + web + scheduler)
  i18n.py        gettext plumbing (ContextVar locale, in-memory .mo compile)
  ops.py         health/ops checks backing /healthz
  domain/        pure logic, NO I/O, no discord/fastapi/sqlalchemy imports:
                 reminders.py (reminder math), timezones.py (JST↔UTC),
                 ingest.py (ramen.events HTML parsing, string in → draft out),
                 ics_export.py / yaml_export.py / yaml_import.py,
                 urls.py (clean_url, safe_next), board.py (campaign-board
                 column precedence), sentence.py, upgrades.py, translations.py
  db/            models.py, session.py, service.py — ALL business logic that
                 touches the DB lives in service.py; discord-free so it's testable
  bot/           thin shell: client.py, cogs/, messages.py (embeds), views.py (buttons)
  scheduler/     loop.py + heartbeat.py — the 60s tick that drains
                 reminder_queue + notifications
  web/           thin shell: app.py, auth.py (Discord OAuth), forms.py,
                 routes/ (concerts, imports, reminders, tags, preferences,
                 discover, outcomes, subscriptions, calendar, setup, welcome,
                 privacy, terms), templates/, static/
  translations/  ja/ and zh/ gettext catalogues (.po only)
tests/           pytest; domain logic is tested hardest
alembic/         migration environment + versions/
deploy/          setup.sh, concert-reminder.service (systemd), Caddyfile, backup.sh
docs/            deploy runbook + docs/superpowers/ design specs & plans
```

Key structural rules:

- Bot and web **never** contain business logic; they call `db/service.py`.
- `domain/` must stay I/O-free (no discord/fastapi/sqlalchemy imports).
- Route registration order matters: `routes/imports.py` must be registered
  before `routes/concerts.py` in `web/app.py`, or `GET /concerts/import` is
  swallowed by `GET /concerts/{event_id}` (FastAPI matches path templates
  before literal segments).
- Concert URLs use the editor-chosen unique `event_id` string; every FK
  targets the internal `Concert.id` PK. `"new"` and `"import"` are reserved
  `event_id` values.
- Venues live **on the leg, as a tag**: `ConcertDay.venue_tag_id` → `tags.id`
  (ON DELETE SET NULL). A concert's VENUE tags are derived by
  `service.sync_concert_venue_tags` (union of its legs' venues); callers must
  feed newly attached tags to `handle_newly_tagged` so venue-tag subscribers
  get their DM notice. The legacy free-text venue columns are gone — do not
  reintroduce them. `ConcertDay.venue_tag` is `lazy="raise"` on purpose: every
  path handing legs to a template must `selectinload` it (a lazy load during
  async rendering is a `MissingGreenlet` 500).
- Substantial features commit a date-prefixed design spec + plan under
  `docs/superpowers/specs/` and `docs/superpowers/plans/` before code.
  `docs/superpowers/demo/` holds the interactive HTML concept demos that are
  the **design source of truth** for the UI — review UI changes against them
  and update the demo when the shipped design deliberately moves.
- `WISHLIST.md` tracks potential features ranked by impact; read it before any
  feature-planning discussion, move shipped entries to its Shipped section,
  and re-rank the rest after each ship.

## Non-negotiable invariants

1. **Timezones.** The DB stores aware UTC only — the `UTCDateTime`
   TypeDecorator rejects naive datetimes. Forms enter times in JST
   (`parse_jst`/`jst_to_utc`); display is always dual via `fmt_dual`:
   "…JST (…user-tz)". All conversions go through `app/domain/timezones.py`,
   nowhere else. Never store or compare naive datetimes.
2. **Queue sync.** `reminder_queue` is a materialized outbox. Any edit to
   concerts/rounds/days/rules must call the relevant `sync_*` function.
   Re-planning is always safe: unsent rows update/delete freely; a deadline
   postponed after its reminder was sent re-arms it. Only successful DM
   delivery marks a row sent; `discord.Forbidden` drops it; transient errors
   retry next tick. A cancelled `ConcertDay` is flagged, never deleted; a
   round counts as cancelled when every leg in its `applies_to` is cancelled.
   `RoundOutcome` (per-user applied/won/lost/paid) layers a second per-user
   suppression pass (`_apply_outcome_suppression`). UPGRADE rounds derive
   per-user eligibility purely (`domain/upgrades.py:is_eligible`) from
   `round_qualifiers`; never persist eligibility.
3. **Group tag expansion** (owner-agreed): attaching a GROUP tag materializes
   its members at that moment only. Editors prune non-performers; removed
   members stay removed; detach + re-attach re-expands; group membership edits
   never rewrite existing concerts. Creation and `/concerts/{event_id}/duplicate`
   pass `expand=False`.
4. **Notifications.** New-event notices go through the `notifications` table
   (DB outbox drained by the scheduler) — never send DMs from web routes. One
   explicit exception: `POST /me/test-dm` sends synchronously as a manual
   diagnostic. Don't extend that carve-out without discussion.
5. **Auth.** Three tiers: admin (`ADMIN_WHITELIST` env only, restart to
   change), editor (`EDITOR_WHITELIST` env OR the DB `users.is_editor` flag,
   which admins toggle live via the preferences page or `/promote-editor` /
   `/demote-editor`; admins automatically pass editor checks), user. Sessions
   are DB-backed sha256 token hashes (revocable). Ownership checks 404, not
   403. Signed-out is not an error: `require_user` raises `LoginRequired` and
   `web/app.py` redirects to `/` — 303 (never 307), or `HX-Redirect` + 204 for
   htmx. Signed-in-but-unauthorized stays 403. The `?next=` return path: only
   GETs get one, htmx uses `HX-Current-URL`, and the value always passes
   `domain/urls.py:safe_next` (same-origin path or None); it rides to Discord
   inside our signed session cookie, never as an OAuth query param. Templates
   link sign-in via the `login_url(request)` global, never a bare
   `/auth/login`. No CSRF token system — mutating routes rely on
   `SameSite=Lax` cookies; that is deliberate. Personal secret links (the
   calendar feed is the first) follow one shape: `secrets.token_urlsafe`, only
   the SHA-256 hash stored, raw value shown exactly once. Self-serve erasure
   is `POST /me/delete`.
6. **event_id vs id** — see Code layout.
7. **Injection boundaries** (silent when broken):
   - Every editor-supplied URL goes through `form_url` (`web/forms.py`,
     wrapping `domain.urls.clean_url`) at the route boundary; the bot uses
     `safe_button_url` in `bot/messages.py`. A `javascript:` URL that slips
     past executes in-origin. The ramen.events importer is SSRF-guarded:
     https + `ramen.events` host only, re-checked on every redirect hop, body
     streamed under a byte cap — don't loosen any of these.
   - User-controlled data reaching inline `<script>` uses `| tojson` on a raw
     Python object, never `| safe`, never pre-`json.dumps`ed (that
     double-encodes and silently breaks the picker).
   - Never interpolate user-controlled text (including translated strings)
     into inline `on*` handlers — HTML attribute decoding defeats Jinja's
     escaping. Put the value in a `data-` attribute and read it via `dataset`.
     Use specific names (`data-tag-name`, not `data-name`, which collides with
     `filterChips()` in `base.html`).
8. **Concert subscriptions are overrides, not records.** "Tracked" is derived
   in exactly one place: `tracked_concert_ids`. Rule: a followed tag matches
   AND no `opted_out` row exists, OR a `subscribed` row exists. No row is the
   common case; `ConcertSubscription`/`LegOptOut` are never backfilled. A
   prune sticks across unfollow/re-follow. Any subscription/opt-out write
   re-syncs that user's rules via `reinstate_user_rules`. Opt-outs suppress
   informational reminders only, never delete a `RoundOutcome`. A per-leg
   opt-out suppresses a round only when every leg in its `applies_to` is
   opted out.

## Migrations (SQLite gotchas — these have bitten before)

- `Base.metadata` has a `NAMING_CONVENTION`; keep it. SQLite batch mode
  refuses unnamed constraints.
- **The live DB predates the convention, and tests cannot see it.** Older
  tables (`concerts`, `tags`) carry anonymous constraints; every test DB is
  built from `Base.metadata`, where everything is named. A migration calling
  `drop_constraint` passes locally and dies on the server. Any such migration
  must (a) pass `naming_convention=NAMING_CONVENTION` into
  `batch_alter_table`, and (b) be tested against a legacy-shaped fixture
  (hand-written server DDL), not a metadata-built one — see
  `tests/test_migration_legacy_anonymous_constraints.py`.
- After autogenerate, always edit the revision: replace
  `app.db.models.UTCDateTime()` with `sa.DateTime()` and remove the
  `import app.db.models` line.
- Column-DROP migrations reverse the deploy order: restart on new code BEFORE
  `alembic upgrade head`, so the old process cannot SELECT the dropped columns
  mid-deploy.
- `alembic.ini` and other config files must stay ASCII-only (the owner's
  Windows machine uses a GBK locale; em-dashes in configs crash it).
- The dedupe index on `reminder_queue` uses `coalesce()` because SQLite treats
  NULLs as distinct in unique indexes. Don't "simplify" it.
- SQLite's `trim()` strips only U+0020; Python's `str.strip()` is
  Unicode-aware. Migrations matching app-written text must pass an explicit
  trim character set including U+3000 (ideographic space).

## Testing conventions

- pytest + pytest-asyncio **auto mode** — `await` directly in tests, never
  `run_until_complete`.
- DB fixtures must register the `PRAGMA foreign_keys=ON` connect listener
  (production does; cascades silently don't fire without it).
- Every page must have at least one logged-in GET render test (a missing one
  shipped a 500 once).
- Discord is never imported in service tests; button/scheduler behavior is
  tested through service functions and fake bot objects.
- Slash-command cogs ARE tested directly (see `tests/test_bot_reminders.py`):
  call `Cog.command_name.callback(cog, ...)` with a minimal fake
  `discord.Interaction`, monkeypatching the cog module's `SessionMaker` to a
  real in-memory async engine.
- `tests/test_i18n_catalogues.py` extracts every msgid in-process and fails on
  anything untranslated (fuzzy counts as untranslated) and on dropped
  `{placeholders}` — editing English copy must keep the msgid byte-identical
  or update both `messages.po` files.

## i18n conventions

- Locales: `en` (NullTranslations — English output stays byte-identical;
  never assert a translated string in an EN test), `ja`, `zh`. Japanese is the
  UGC source of truth: `loc_field(obj, field, locale)` resolves en →
  `{field}_en`, zh → `{field}_zh`, ja → the original column; empty string
  falls through; no cross-locale chaining.
- Write translatable strings as `_("literal")` at the point of use. Module-level
  dicts keyed/valued by translatable text wrap each literal in `N_()` (extraction
  marker only); translation happens at lookup time, never at definition time.
  `gettext_in(locale, msg)` is the explicit-locale escape hatch.
- Three locale sources — choosing wrong is silent: `get_locale()` inside web
  requests; `user.language` for per-recipient text composed outside a request
  (scheduler DMs, `NoticeContext`); an explicit `locale: str | None` parameter
  where the caller must decide (`user_calendar_events` passes None
  deliberately so the `.ics` feed stays canonical). Beware the ~10 sites in
  `db/service.py` that copy a label into a dataclass before render — the
  locale must be right at the copy site.
- The `lang` cookie is a cache of `users.language`, never the source of truth;
  the single write path is public `POST /language`. The OAuth callback seeds
  the DB column from the cookie only at account creation.
- The language switcher is a cycle chip (EN → 中文 → 日本語), a plain POST form
  so it works without JS. Language names are never translated. A dropdown menu
  was replaced at the owner's request — don't bring it back.
- Trilingual UGC enforcement: every translatable field is filled in all three
  languages or none (422 at create boundaries, gap notices on edit).

## UI conventions

- Sentence case everywhere ("Add group", not "add group").
- Times render dual (JST + user tz). On the web, deadline TIMES render as two
  lines via the `dual_lines` Jinja global (bold weekday+day+month line, then
  "HH:MM JST, HH:MM local"); `fmt_dual`'s flat one-liner is for Discord
  embeds/plain text only. Performance dates use `fmt_day_month`/`day_month`
  (no zone).
- Design tokens live in `style.css`'s `:root`. Dark mode is defined both ways:
  `@media (prefers-color-scheme: dark)` AND `:root[data-theme=...]` (header
  toggle stamps `data-theme`, persisted to localStorage; `base.html` stamps it
  in `<head>` before first paint to avoid flashing). Style new components
  against both.
- Tag chips are the universal element; pickers are native `<dialog>` white
  cards (header + ×, search, chip list; no footer; backdrop-click and Esc
  close).
- Editor leg/round cards render ONLY through the shared partials
  `_editor_leg_card.html`/`_editor_round_card.html` — never hand-roll a copy.
  Destructive card actions live in the kebab menu (`details.kebab`), the app's
  only overflow menu, destructive-only. The inline × beside Cancelled was
  removed deliberately — do not reintroduce it.
- Sentence-style reminder builders render through locale-ordered slot
  patterns: one translatable pattern msgid per builder, split by
  `domain/sentence.py:split_slots`, rendered by the `sentence_slots` Jinja
  global (text parts escaped; only server-built selects pass as Markup).
  JS adds rows by cloning the server-rendered `<template>`, never by
  assembling English DOM.
- Home (`/`, login-gated, "where do I stand") vs Discover (`/discover`,
  public, "what's on" — the only content page reachable signed out). Capture
  actions ("I have applied" etc.) live on Coming up rows, never on board
  cards. Which buttons a row offers is resolved in `service.my_deadline_rows`
  via `can_capture` (round opened; APPLIED is irreversible) and
  `can_report_result` (APPLIED and results time passed — the web's only exit
  from APPLIED).
- Discover's filters (tag/region chips, free-text search, round-status facet)
  combine as AND, degrade to real links/GET forms without JS, and compute
  initial state server-side. VENUE tags filter by `region`, never by exact
  venue (explicitly ruled out).
- **Responsive design is a retrofit, not a second design**: all phone rules
  live in one `@media (max-width: 700px)` section at the end of `style.css`;
  all tablet rules in one `@media (min-width: 701px) and (max-width: 1040px)`
  section just before it (the `.layout`/`.fsheet` flip shares the 1040/1041
  boundary); a nested `@media (max-width: 380px)` inside the phone block
  handles narrow phones. `test_theme_and_tokens.py` pins the top-level
  breakpoint count — scattered breakpoints fail CI. Border-radius is 3px
  everywhere (bottom sheets' `14px 14px 0 0` is the deliberate exception;
  `test_style_uses_3px_radius_not_6or8` guards it).
- Tile display rules: franchise+group → "F · G"; group only → G; artists only
  → artist chips; >1 venue → "📍 Multiple".

## Deployment

- Server: Lightsail Ubuntu 24.04, app at `~/app`, systemd unit
  `concert-reminder`, Caddy with a Cloudflare Origin cert. Update ritual:
  `cd ~/app && git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder`
  (mind the column-DROP exception under Migrations).
- Caddyfile changes: `sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`.
- Logs: `journalctl -u concert-reminder -f`. Health: `/healthz`
  (keyword-monitored `"ok":true`; goes false after 3 missed scheduler ticks).
- Nightly S3 backups via `deploy/backup.sh` (cron, 30-day lifecycle).
- Full runbook: `docs/deploy.md`.

## Security considerations

- Never commit `.env`; secrets live only on the server and in the owner's
  local copy. `SESSION_SECRET` is required (32+ chars) when `BASE_URL` is
  https or the app refuses to start.
- Respect invariant 7's three injection boundaries (URL scheme validation,
  `| tojson` for inline script data, `data-` attributes instead of inline
  `on*` interpolation) — each is cheap and silent when broken.
- Don't loosen the importer's SSRF guards or the `safe_next` open-redirect
  guard.
- Session cookies are `SameSite=Lax`; that is the entire CSRF story, by design.
- Personal secret links store only SHA-256 hashes; the raw token is shown once
  and recovery means generating a new one.

## Owner context

The owner (imreiji) is a pilot, technically comfortable but rusty at coding,
on Windows PowerShell 5.1 (no `&&` chaining — use `;` or separate lines in any
commands you give him). Explain the why behind non-obvious changes. He cares
about correct JST handling, the tag semantics above, and a clean UI — when in
doubt about UX, ask, don't assume.
