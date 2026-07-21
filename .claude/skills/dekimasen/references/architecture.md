# Architecture and layout — the full map

Per-directory and per-file detail, including the warnings attached to specific files. The short map in CLAUDE.md tells you where things live; this file tells you what will break if you move them.


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
- `src/app/i18n.py` — gettext plumbing. Everything about it (locale resolution, `N_()`, `loc_field`, the catalogue ritual) lives in `i18n.md` in this directory — read that before touching any translatable string.
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

