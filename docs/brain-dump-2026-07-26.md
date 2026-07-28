# dekimasen.app (concert-reminder) — Codebase Brain Dump

Date: 2026-07-26. Compiled from a full-codebase exploration (all of `src/`, tests,
migrations, templates, docs). Purpose: give another LLM everything it needs to
implement features, fix bugs, and refactor safely without re-reading the whole
tree. File:line pointers are as of commit `a9b98a0`.

---

## 1. What this is

A Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets, payment deadlines). Live at
https://dekimasen.app (AWS Lightsail Ubuntu 24.04 behind Cloudflare, Caddy
reverse proxy). ~1222 tests. Owner (imreiji) is a pilot, technically
comfortable but rusty; Windows PowerShell 5.1 (no `&&` — use `;`). He cares
about correct JST handling, tag semantics, and clean UI; when in doubt about UX,
ask.

**One Python process, one asyncio loop, three concurrent tasks** (`app/main.py`):

1. discord.py bot (skipped entirely in web-only dev mode when `DISCORD_TOKEN` is empty)
2. FastAPI web app (Jinja2 + htmx 2.0.4), uvicorn bound to localhost
3. Scheduler: 60-second tick that drains two DB outboxes into Discord DMs

`asyncio.wait(..., FIRST_COMPLETED)`: if any task exits, the rest are cancelled
and the process exits 1 for systemd to restart — deliberate rejection of
half-alive states (web up, bot silently dead).

**Stack**: Python 3.12, SQLite (WAL) + SQLAlchemy 2.0 async (aiosqlite) +
Alembic, FastAPI + Jinja2 + htmx, discord.py, babel gettext (en/ja/zh), uv.

**Commands**:
- Run: `uv run python -m app.main` (empty `DISCORD_TOKEN` in `.env` → web-only)
- Tests: `uv run pytest -q` — MUST pass before commit
- Lint: `uv run ruff check .` — MUST be clean before commit
- Migration: `uv run alembic revision --autogenerate -m "msg"` → hand-edit → `uv run alembic upgrade head`
- i18n: `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .` then
  `pybabel update -i messages.pot -d src/app/translations -l ja` (and `-l zh`),
  fill msgstrs by hand in both `.po` files, delete `messages.pot`. No `.mo`
  files ever committed — compiled in memory at first use.
- CI (`.github/workflows/ci.yml`): `uv sync`, `ruff check .`, `pytest -q` — nothing more.
- Local dev note (owner machine): an external serve.py can lock `.venv` — use
  `uv run --isolated`, never resync while it runs.

---

## 2. Layering rules (strict)

```
src/app/
  domain/     pure logic. NO I/O, no sqlalchemy/fastapi/discord/httpx imports.
  db/         models.py, session.py, service.py — ALL business logic touching
              the DB lives in service.py; discord-free so it's testable.
  web/        thin shell: routes, templates, static. Calls db/service.py.
  bot/        thin shell: cogs, embed builders, persistent buttons. Calls db/service.py.
  scheduler/  the 60s tick loop; calls db/service.py.
  i18n.py     top-level (file I/O at startup; bot imports it too, so not web/).
  config.py   pydantic-settings singleton; the ONLY reader of os.environ.
  ops.py      I/O half of health checks (domain/health.py is the pure half).
  main.py     process wiring.
```

Bot and web NEVER contain business logic. `app.ops` imports `db.models`, so
`db/service.py` imports it function-locally (service.py:3227) to avoid layer
inversion.

---

## 3. Non-negotiable invariants (agreed with the owner)

1. **Timezones.** DB stores aware-UTC only; the `UTCDateTime` TypeDecorator
   (models.py:43) raises `ValueError` on naive datetimes at bind time and
   re-attaches `tzinfo=UTC` on read. Forms enter times as naive JST
   (`parse_jst`/`jst_to_utc`); display is always dual JST + user tz.
2. **Queue sync.** `reminder_queue` is a materialized outbox. Any edit to
   concerts/rounds/days/rules must call the relevant `sync_*` function.
   Re-planning is always safe: unsent rows update/delete freely; a postponed
   deadline whose reminder was already sent re-arms (sent_at cleared) only if
   the new fire time is in the future. Only successful DM delivery marks a row
   sent; `discord.Forbidden` also marks sent (permanent) and sets
   `dm_blocked_since`; transient errors retry next tick untouched. Cancelled
   `ConcertDay`s are flagged, never deleted (rounds' `applies_to` ids must stay
   resolvable). A round counts as cancelled only when `applies_to` is non-empty
   AND every named leg is cancelled (`is_round_cancelled`, service.py:175).
3. **Group tag expansion.** Attaching a GROUP tag materializes its members AT
   THAT MOMENT only. Pruned members stay pruned; detach+re-attach re-expands;
   membership edits never rewrite existing concerts. The creation form and
   `duplicate_concert` pass `expand=False`.
4. **Notifications.** New-event/ops notices go through the `notifications`
   table (outbox drained by the scheduler) — never DM directly from web routes.
   The ONE exception: `POST /me/test-dm` (synchronous, user-initiated
   diagnostic). Do not extend the carve-out.
5. **Auth.** Three tiers: admin (env `ADMIN_WHITELIST` only), editor (env
   `EDITOR_WHITELIST` OR `users.is_editor` DB flag, admin-toggleable), user.
   Admins pass editor checks. Sessions: DB-backed sha256 token hashes,
   revocable. Ownership violations 404 (not 403). Signed-out is NOT an error:
   `require_user` raises `LoginRequired` (not HTTPException) → handler sends
   303 to `/` with `?next=<path>` (GETs only), or `HX-Redirect` + 204 for htmx.
   Signed-in-but-unauthorized IS an error → 403. `next` always passes
   `safe_next`; it rides to Discord in our signed session cookie, never as an
   OAuth query param. New accounts always land on `/welcome` regardless of
   `next`. No CSRF token — `SameSite=Lax` cookies, deliberate for this size.
   Personal secret links (calendar feed): `secrets.token_urlsafe`, store only
   the SHA-256 hash, show raw value exactly once; recovery = regenerate.
6. **`event_id` vs `id`.** FKs target `Concert.id`; URLs use editor-chosen
   unique `event_id` strings. `"new"` and `"import"` are reserved
   (`RESERVED_EVENT_IDS`, concerts.py:91).
7. **Injection boundaries.** (a) Every editor URL goes through `form_url`
   (web/forms.py) at the route boundary → 422 on bad scheme; the bot uses
   `safe_button_url` (bot/messages.py:55) wrapping `clean_url` directly.
   (b) User-controlled data reaching inline `<script>` uses `| tojson` on raw
   Python objects — never `| safe`, never pre-`json.dumps` (double-encodes).
   (c) Never interpolate user text into inline `on*` handlers (browser
   HTML-decodes before JS-parsing); use `data-` attributes + `dataset`. Use
   `data-tag-name`/`data-preset-name`, NOT `data-name` (collides with
   `filterChips()` in base.html). Translated strings count as user-controlled.
8. **Concert subscriptions are OVERRIDES, not records.** "Tracked" is derived
   in exactly one place — `tracked_concert_ids` (service.py:946):
   `(tag_matched − opted_out) ∪ subscribed`. No row is the common case.
   `ConcertSubscription`/`LegOptOut` hold only explicit edits, never
   backfilled. A prune sticks across unfollow/re-follow. Any subscription/leg
   write re-syncs rules via `reinstate_user_rules` — skip it and a pruned
   concert keeps reminding. Opt-out suppresses reminders, never deletes a
   `RoundOutcome`. Per-leg opt-out suppresses a round only when EVERY leg in
   its `applies_to` is opted out.

---

## 4. Configuration (`config.py`)

`Settings(BaseSettings)` reading `.env`; singleton `settings = get_settings()`
(lru_cache). Fields (env upper-snake): `discord_token` (empty → web-only mode
via `bot_enabled` property), `discord_client_id/secret`, `dev_guild_id` (set →
instant guild-scoped slash sync), `editor_whitelist`/`admin_whitelist`
(comma-sep Discord ids; `editor_ids`/`admin_ids` frozenset properties;
`is_editor()`/`is_admin()`), `base_url` (default `http://localhost:8000`),
`session_secret` (validator rejects weak/`change-me`/<32 chars ONLY when
base_url is https — fresh clones run over http unmodified), `web_host/port`,
`database_url` (default `sqlite+aiosqlite:///./app.db`), `backup_marker_path`,
`default_timezone` (`America/Moncton`), `privacy_contact_discord/email`.

Final editor status = `users.is_editor OR editor_whitelist OR admin_whitelist`
— resolved in `auth.current_user` and baked into the frozen `SessionUser`.

---

## 5. Data model (`db/models.py`, 646 lines)

SQLAlchemy 2.0 typed style. `Base.metadata` carries `NAMING_CONVENTION`
(models.py:63) — deterministic constraint names so SQLite batch migrations
work. Every enum column uses `Enum(E, values_callable=lambda e: [m.value for m
in e])` (stores `.value` strings). `_now()` = `datetime.now(UTC)` default for
timestamps. All datetime columns are `UTCDateTime`.

Engine (`db/session.py`): `create_async_engine(settings.database_url)`; a
`connect` listener on `engine.sync_engine` sets `PRAGMA journal_mode=WAL`,
`PRAGMA foreign_keys=ON` (SQLite defaults OFF per-connection — this listener is
what makes every `ondelete=` real), `PRAGMA busy_timeout=5000`.
`SessionMaker = async_sessionmaker(engine, expire_on_commit=False)`.

### Tables (columns abridged to the load-bearing; see models.py for full)

- **users** (`User`): PK `discord_id` BigInteger (no autoincrement); `username`,
  `avatar_hash`, `timezone` (default America/Moncton), `tz_auto` bool,
  `language` (default "en"), `is_editor` bool, `calendar_token_hash` (unique,
  nullable), `dm_blocked_since` (nullable), `onboarding_step` int, `created_at`.
  Relationship `rules` has `passive_deletes=True` — required because
  `reminder_rules.user_id` is NOT NULL + CASCADE; without it, user deletion
  breaks (ORM tries SET NULL first).
- **web_sessions** (`WebSession`): `token_hash` (sha256, unique), `user_id` FK
  CASCADE, `expires_at`, `revoked_at`.
- **concerts** (`Concert`): `id` PK; `event_id` String(100) unique (URL
  handle); `title` NOT NULL + `title_en`/`title_zh`; `kind`
  Enum(ConcertKind) nullable; `organizer`, `categories`, `eventernote_url`,
  `official_url`, `source_url`, `performers_text`, `franchise` (legacy free
  text), `notes`/`notes_en`/`notes_zh`; `created_by` FK users **SET NULL**
  (erasure keeps concerts, anonymizes author). Relationships: `days` (cascade
  delete-orphan, ordered by starts_at), `rounds` (cascade), `tags` (secondary
  concert_tags), `audits` (cascade, newest first), `creator`.
- **concert_audit** (`ConcertAudit`): `concert_id` FK CASCADE indexed,
  `edited_by` FK SET NULL, `edited_at_utc`, `changes` JSON (list of
  `{field,before,after}`; enums as `.value`). One row per edit, top-level
  scalar fields only.
- **tags** (`Tag`): `name` unique (canonical, ja); `name_en`/`name_zh` (not
  unique); `kind` Enum(TagKind); `parent_id` self-FK SET NULL (GROUP →
  FRANCHISE parent); venue-only: `location_url`, `region`,
  `city`/`city_en`/`city_zh`, `address` (deliberately no locale variants —
  it's for pasting into maps); `eventernote_url`; `created_by` SET NULL.
  `members`: self-referential m2m via `tag_members` (group ⊇ member, no
  nesting).
- **tag_members** (`TagMember`): composite PK (group_tag_id, member_tag_id),
  both CASCADE.
- **concert_tags** (`ConcertTag`): composite PK (concert_id, tag_id), both
  CASCADE; `tag_id` also has its own index (composite PK only covers leftmost
  prefix; Discover's tag filter queries by tag_id alone).
- **concert_days** (`ConcertDay`) — "legs": `concert_id` FK CASCADE indexed;
  `label` + `label_en`/`label_zh`; **`venue_tag_id` FK tags.id ON DELETE SET
  NULL, indexed — the ONLY venue field anything reads** (legacy free-text venue
  columns were DROPPED by migration `ce43bfcfcae3`; do not reintroduce them or
  name-matching); `doors_at_utc` nullable, `starts_at_utc` NOT NULL;
  `cancelled` bool (flag, never delete). **`venue_tag` relationship is
  `lazy="raise"` ON PURPOSE** — a lazy load during async template rendering is
  a `MissingGreenlet` 500 (shipped once); every path handing legs to a template
  must `selectinload` it or query tags by id separately.
- **rounds** (`Round`): `concert_id` FK CASCADE indexed; `kind`
  Enum(RoundKind); `label` + `label_en`/`label_zh` (`label_en` predates i18n —
  it USED to render as an English gloss to everyone; now it's a true locale
  variant via `loc_field`); four optional anchors `opens_at_utc`,
  `closes_at_utc`, `results_at_utc`, `payment_deadline_at_utc`; `applies_to`
  JSON (list of day ids; empty/None = all legs); `url`, `notes`. `qualifiers`:
  self-referential m2m via `round_qualifiers` — meaningful only for
  `RoundKind.UPGRADE`; empty = any secured ticket on the concert qualifies.
- **round_qualifiers** (`RoundQualifier`): composite PK
  (upgrade_round_id, qualifying_round_id), CASCADE.
- **round_label_phrases** (`RoundLabelPhrase`): trilingual phrase library;
  unique index across (label, label_en, label_zh) jointly; `used_count`,
  `last_used_at`.
- **round_outcomes** (`RoundOutcome`): unique (user_id, round_id); `outcome`
  Enum(LotteryOutcome); sequence APPLIED → (WON|LOST) → PAID enforced in
  `record_round_outcome`, not the DB.
- **concert_subscriptions** (`ConcertSubscription`): unique (user_id,
  concert_id); `state` Enum(SubscriptionState). No row = tag default.
- **leg_opt_outs** (`LegOptOut`): unique (user_id, concert_day_id); presence
  alone = opted out.
- **reminder_rules** (`ReminderRule`): `user_id` FK CASCADE; exactly one of
  `concert_id`/`round_id` set (convention, not DB-enforced); `anchor`
  Enum(Anchor); `offset_days` (negative = before), `offset_hours`; `channel`
  Enum(Channel) default DM; `channel_id`.
- **reminder_presets** (`ReminderPreset`) + **preset_items** (`PresetItem`):
  named offset bundles; `is_default` bool (one per user by convention).
  PresetItem has NO minutes column (wishlist item).
- **tag_subscriptions** (`TagSubscription`): unique (user_id, tag_id);
  `preset_id` FK SET NULL (auto-apply), `notify` bool.
- **notifications** (`Notification`): DM outbox — `body` (plain-text
  fallback), `concert_id` FK CASCADE nullable (set → rich embed), `kind`
  ("new_event", "leg_cancelled", "ops_alert"), `sent_at_utc`.
- **reminder_queue** (`ReminderQueue`): the materialized outbox. `rule_id` FK
  CASCADE, `round_id`/`day_id` nullable FKs CASCADE, `anchor`, `fire_at_utc`,
  `sent_at_utc`. Dedupe unique index uses
  `coalesce(round_id,0)`/`coalesce(day_id,0)` because SQLite treats NULLs as
  distinct in unique indexes — don't "simplify." Plus `(sent_at_utc,
  fire_at_utc)` due-scan index.
- **ops_check_state** (`OpsCheckState`): PK `name`; mirrors
  `domain.health.StoredState` (ok, changed_at, last_notified_at, pending_ok,
  pending_since).

### Enums (`domain/types.py`, all StrEnum)

- `RoundKind`: LOTTERY_ROUND, ELIGIBILITY_ITEM_SALE, STREAM_TICKET_SALE,
  GENERAL_SALE (free-entry lottery, NOT first-come), RESULT_ANNOUNCEMENT,
  PAYMENT_DEADLINE, FCFS_SALE (true first-come-first-served), TOUR_PACKAGE
  (overseas hotel+ticket bundle), UPGRADE (nested campaign for
  ticket-holders), OTHER.
- `ConcertKind`: CONCERT, TOUR, FESTIVAL, RELEASE, MEET_GREET, FAN_MEETING,
  TALK, STAGE, SCREENING, GOODS, STREAM, OTHER.
- `TagKind`: FRANCHISE, ARTIST, VENUE, GROUP.
- `Anchor`: OPENS, CLOSES, RESULTS, PAYMENT (round fields), EVENT_START
  (day.starts_at_utc).
- `Channel`: DM, CHANNEL.
- `LotteryOutcome`: NOT_APPLIED, APPLIED, WON, LOST, PAID.
- `SubscriptionState`: SUBSCRIBED, OPTED_OUT.

---

## 6. Domain layer (`src/app/domain/` — pure, no I/O; verified)

- **reminders.py** — the planner. Frozen dataclasses `RoundInfo`, `DayInfo`,
  `RuleInfo`, `PlannedReminder`. `anchor_time(round, anchor)` (single source
  for anchor→field; EVENT_START returns None). `plan_for_rule(rule, rounds,
  days, now)`: EVENT_START + round_id → `[]` (contradiction, plan nothing);
  EVENT_START → one reminder per day; round anchors → per round (narrowed by
  rule.round_id), skipping rounds missing that anchor; only future fire times
  emitted. Never raises. Callers rely on the queue dedupe index for upserts.
- **timezones.py** — `JST = ZoneInfo("Asia/Tokyo")`. `jst_to_utc(naive)`
  (raises on aware input), `utc_to_local` (raises on naive), `utc_to_jst`.
  `fmt_dual(dt, tz, locale)` one-line "Sat 2026-08-01 19:00 JST (07:00 ADT)"
  (ja/zh wrap hand-built CJK weekday chars; Discord/plain-text only).
  `fmt_dual_lines` → `(date_line, time_line)` two-line web shape ("Sat 1 Aug",
  "19:00 JST · 07:00 ADT"); day number hand-built because `%-d`/`%#d` aren't
  portable (owner is on Windows). `fmt_day_month` day-month only, for
  performance DATES (not deadlines) — "12 Oct" / "10月12日".
- **ingest.py** — ramen.events HTML → `ParsedConcert` (takes a string; caller
  fetches). Regexes `DAY_LINE` ("Day N: date, Doors HH:MM / Starts HH:MM" or
  "When:"), `VENUE_LINE`, `APPLY_WITHIN`. `_KIND_KEYWORDS` ordered
  first-match-wins heading classifier → RoundKind. `IngestError` only for
  "not a ramen post at all" (no title / no `section.gh-content` body);
  everything else degrades to warnings. Datetimes stay naive JST (flow through
  the same `parse_jst` boundary as manual entry). Official-site hrefs pass
  `clean_url`; unsafe ones become warnings, never propagate.
- **ics_export.py** — RFC 5545. `build_ics` single zero-duration VEVENT (a
  deadline is a point, no DTEND); `build_calendar(events, now_utc=None)`
  multi-VEVENT feed sharing one DTSTAMP. Raises ValueError on naive datetimes.
- **yaml_export.py / yaml_import.py** — the two-way draft vocabulary. Export
  (`concert_to_yaml`) emits: `slug, title, title_en, title_zh, kind,
  organizer, categories, series:{franchises,groups,artists}, venues,
  performers, eventernote_url, official_url, source_url, performances:[{label,
  label_en, label_zh, city, venue, venue_address, doors_jst, starts_at_jst}],
  rounds:[{label, label_en, label_zh, kind, applies_to, apply_opens_jst,
  apply_closes_jst, results_jst, payment_deadline_jst, url, notes}], notes,
  notes_en, notes_zh` — all timestamps as JST 'YYYY-MM-DD HH:MM' strings.
  Import (`parse_draft`): `yaml.safe_load` ONLY; `DraftError` raised only when
  no preview is possible (not YAML / not a mapping / no `title:` — the single
  required key / RecursionError on hostile nesting); everything else →
  warnings and blank fields (unknown keys, bad kinds, malformed datetimes,
  bare dates without time — never guesses midnight). `_text()` rejects
  lists/dicts without stringifying (YAML-alias bomb defense). Names in drafts,
  never DB ids.
- **urls.py** — `clean_url(raw)`: edge-trims C0+space, deletes interior C0
  (defeats `java\tscript:`), requires http(s) scheme + netloc (blocks
  scheme-relative `//evil.com`), else raises `UnsafeURLError`; empty → None.
  `safe_next(raw)`: never raises; same trimming; ≤512 chars; must start `/`;
  **backslash-folding check** — first two chars with `\`→`/` must not be `//`
  (browsers fold `/\evil.com` into scheme-relative); returns path+query only.
- **board.py** — `Column` StrEnum (OPEN/APPLIED/WON/SECURED).
  `column_for(outcomes: list[(LotteryOutcome, is_upgrade)], has_open_round)`:
  precedence upgrade-WON(unpaid, rank 4) > PAID→SECURED(3) > WON(2) >
  APPLIED(1) > OPEN (no outcomes + open round) > None (off board). LOST and
  NOT_APPLIED place nothing. `pill_tone(column, next_deadline, now)`: WON
  column always `p-danger` (money owed); else ≤1d danger / ≤7d off / quiet.
  `OPEN_COLUMN_LIMIT = 12` (applied by `service.board_cards`).
- **upgrades.py** — `is_upgrade_eligible(qualifying_round_ids,
  user_secured_round_ids)`: empty qualifiers → any secured ticket;
  else non-disjoint. "Secured" (WON/PAID) classification is the caller's job.
- **sentence.py** — `split_slots(pattern, slots)` → list of
  `("text",...)/("slot",name)` tuples; unknown slot raises ValueError (a
  translator typo must fail in catalogue tests, not render blank).
- **health.py** — pure alert state machine. `backup_is_stale` (36h),
  `disk_is_low` with hysteresis (trip 10%/1GB, clear 15%/1.5GB — caller
  supplies last confirmed verdict), `should_alert(stored, observed_ok, now)`:
  needs two consecutive agreeing observations to confirm a transition;
  re-alerts every 24h while broken; first-ever healthy observation adopts the
  baseline silently.
- **draft.py** — mutable dataclasses `ParsedDay`, `ParsedRound`,
  `ParsedConcert` shared by both preview producers (ramen parser fills a
  subset; draft path fills more; route-resolved fields like
  `matched_venue_tag_id`, `leg_keys` default empty).
- **translations.py** — `missing_variants(base, en, zh, mandatory=False)`:
  the all-three-or-none rule; base IS the ja value; whitespace-only = blank;
  all-blank non-mandatory → ok. `SLOT_LABEL = {"ja":"日本語","en":"English",
  "zh":"中文"}` (deliberately untranslated). The rule is intentionally
  duplicated in JS at `_variant_guard.html` (a 422 would lose typed input).

---

## 7. Service layer (`db/service.py`, 3751 lines) — the heart

Comment-delimited sections; function-by-function highlights. `_now()` wraps
`datetime.now(UTC)`; everything takes `now: datetime | None = None`.

### Users (l.67)
`ensure_user` (get-or-create, refreshes username), `set_editor`,
`list_editors` (DB ∪ env, env-locked flagged), `delete_user` (single
`session.delete`; CASCADE for personal data, SET NULL for shared catalogue;
needs FK pragma ON), `record_dm_outcome` (sets/clears `dm_blocked_since`).

### Queue sync (l.189) — invariant 2 lives here
- `_apply_outcome_suppression(session, user_id, rounds, anchor)` (l.192): four
  passes — (1) per-leg opt-out (drop if every covered leg opted out; empty
  applies_to immune); (2) "secured elsewhere" (drop non-UPGRADE round whose
  every leg is secured WON/PAID by another round); (3) UPGRADE rounds skip
  pass 2 but require `is_upgrade_eligible` (holding a ticket is the
  prerequisite to see them); (4) same-round anchor pruning (RESULTS +
  NOT_APPLIED drop; PAYMENT + LOST/PAID/NOT_APPLIED drop).
- `record_round_outcome(session, user_id, round_id, outcome)` (l.333) — THE
  single write path for outcomes (web route + DM buttons + /setup all funnel
  here). NOT_APPLIED/APPLIED only set a first outcome (never overwrite);
  WON/LOST overwrite; PAID only from WON. Then `reinstate_user_rules` for the
  whole concert; on LOST additionally `_auto_arm_next_round` (creates a real
  OPENS ReminderRule for the next non-UPGRADE round sharing a leg, using the
  default preset's OPENS offset, else 0/0).
- **`sync_rule(session, rule, now)`** (l.453) — the reconciliation engine:
  gathers rounds/days in scope, filters cancelled + concert-opt-out +
  outcome suppression BEFORE the pure planner, calls `plan_for_rule`, diffs
  against queue rows keyed `(round_id or 0, day_id or 0, anchor)`:
  insert / move fire_at / re-arm sent rows if new time future / delete
  unplanned.
- `sync_concert(session, concert_id)` (l.543) — re-syncs every rule touching
  the concert, then re-runs `_auto_arm_next_round` for every LOST outcome
  (catches next-rounds that didn't exist at loss time). Call after ANY
  concert edit.
- `notify_newly_cancelled_legs` (l.576) — **call BEFORE `sync_concert`**
  (which deletes the queue rows it inspects); queues one Notification per
  user losing every unsent reminder on the concert.
- `reinstate_user_rules(session, user_id, concert_id)` (l.651) — re-sync (not
  recreate) one user's rules on a concert; shared by outcome recording,
  subscription writes, and the DM Reinstate button.

### Scheduler retrieval (l.675)
`DueReminder` frozen dataclass (includes `user_language`).
`due_reminders(session, now, limit=100)` — batch fetch + one query per entity
type; labels resolved via `loc_field(..., user.language)` (per-recipient, NOT
get_locale — built outside any request). `mark_sent`. `upcoming_rounds`
(horizon list for /upcoming). `LABEL_BY_ANCHOR` / `LABEL_BY_ROUND_KIND`
(N_()-marked dicts, translated at lookup). `UpcomingDeadline` — **one row per
set anchor field** (a round with close+payment = two rows). `upcoming_deadlines
(session, now, limit=10, concert_ids=None)` — global chronological list;
narrows BEFORE sort/limit; locale = `get_locale()` (request path), labels
copied into the dataclass at build time.

### Personal board (l.943)
- `tracked_concert_ids` (l.946) — THE derivation (invariant 8).
- `set_concert_subscription` / `clear_concert_subscription` (l.1034/1060) —
  upsert/delete the override + `reinstate_user_rules` (mandatory).
- `set_leg_opt_out` (l.1079) — row-presence toggle; no resync needed
  (suppression is a planner-side read).
- `board_cards(session, user_id, now, concert_ids=None)` (l.1164) → `(columns:
  dict[Column, list[BoardCard]], open_total)`; outcomes batched in one query;
  OPEN truncated to `OPEN_COLUMN_LIMIT` soonest-first, `open_total` pre-cap
  for "+N more".
- `DEADLINE_ROWS_LIMIT = 10` (l.1300) shared by GET / and the outcome POST so
  htmx swaps don't change list length.
- `DeadlineRow` with `can_capture`/`can_report_result` resolved server-side.
- **`capture_gates(round_, outcome, now, qualifies=True)`** (l.1403) — THE
  definition of the two gates: `can_capture` = round opened AND qualifies;
  `can_report_result` = can_capture AND outcome APPLIED AND result moment
  (or close) passed. Used by Home rows AND concert-page rows. Never relax
  `record_round_outcome` instead — gates belong on the read side (this is
  the web's only exit from APPLIED for dm_blocked users).
- `my_deadline_rows` (l.1434) — decorates deadlines with outcomes/gates,
  batched; drops UPGRADE rows the viewer is ineligible for (eligibility
  scoped per-concert).

### First-run capture flow /setup (l.1549)
Stateless by design — each screen renders DB truth; tamper-safe and
re-runnable. `_tracked_upcoming_concerts` (shared working set: tracked ∪
opted-out, upcoming only). `setup_prune_tiles` (screen 1),
`setup_application_rows` (screen 2; `_round_asks_application`: no outcome,
opened, has apply/result stamp, result not yet passed), `setup_tallies`
(screen 3). Writers `apply_prune_selection` / `record_setup_applications`
recompute the candidate set server-side (forged ids only affect the forger)
and delegate to `set/clear_concert_subscription` / `record_round_outcome`
exclusively. Unpruning clears the override; never writes SUBSCRIBED.

### Concert page rounds (l.1902)
`concert_round_rows(session, user_id, concert, now)` → `(per-leg LegRounds
list, all-legs RoundRow list)`; a round is "all-legs" if applies_to
empty/None OR covers every live (non-cancelled) leg. `upgrade_locked` +
`qualifier_labels` for ineligible viewers. `_qualifiers_by_upgrade_round`
reads the association table directly (avoids lazy-load MissingGreenlet).
`concert_next_moment` picks the "Next for you" round (or None → no panel).

### Discover (l.2132)
`discoverable_concert_criterion()` — SQL criterion (needs
`.correlate(Concert)`): hides concerts whose every existing leg is cancelled;
no-days concerts still show. `discover_peek` (Home teaser).
`discover_statuses(session, concerts, user_id=None, now)` — one merged pill
per card; caller must eager-load days/rounds; precedence WON-upgrade-collapse
→ SECURED → WON → APPLIED → event-state-only (signed-out gets pure event
state); optional second `upgrade_text` accent pill. `_humanize_until` rounds
in the alarming direction.

### Calendar feed (l.2503)
`generate_calendar_token` (hash stored, raw returned once),
`get_user_by_calendar_token`, `user_calendar_events(session, user_id, now,
locale=None)` — sources from `ReminderQueue` (already encodes scope);
**`locale=None` is DELIBERATE for the .ics feed** (canonical/untranslated,
stable bytes); the `/mydeadlines` cog passes the recipient's language
explicitly. The one explicit-locale-param function.

### Edit history (l.2632)
`TRACKED_CONCERT_FIELDS` (top-level scalars only). `snapshot_concert(concert)`
→ dict BEFORE mutation; `record_concert_edit(session, concert, edited_by,
before)` AFTER (one audit row per edit; None on no-op); `concert_audit_log`.
Get the order wrong and every diff reads unchanged.

### Tags (l.2694)
`find_tag_by_name[_and_kind]` (case-insensitive), `group_members`,
`resolve_group_member` (validates the pair before retroactive-apply fan-out),
`active_concerts_missing_member`, `tag_directory_context` (whole Tags page in
one pass; untranslated count via `missing_variants(mandatory=True)` on name).
`match_venue_tag_id(name, venue_tags)` (l.2943) — canonical-name only,
Python-side strip (catches U+3000), silent-bind so deliberately narrow.
`match_tag_ids_by_name(names, tags)` (l.2976) — draft path; matches
name/name_en/name_zh (visible to editor, so looser); returns (ids, unmatched
names). `tag_picker_context` (raw dicts; template applies `| tojson`).
**`attach_tag(session, concert_id, tag, expand=True)`** (l.3047) — invariant 3
lives here; returns NEWLY attached tags → feed to `handle_newly_tagged`.
`detach_tag`.

### Presets & subscriptions (l.3087)
`apply_preset` (idempotent per item; syncs each rule).
**`handle_newly_tagged(session, concert, new_tags)`** (l.3127) — the
notify-and-auto-apply pipeline: per subscribed user — skip if they already
have rules on the concert; else apply earliest subscription's linked preset +
queue Notification kind="new_event" if any sub has notify=True.
`due_notifications` / `mark_notification_sent` (outbox drain pair).

### Ops alerts (l.3211)
`evaluate_and_alert(session, results, now)` — folds check results into
`OpsCheckState` via `domain.health.should_alert`, queues owner Notifications
(kind="ops_alert") through the outbox; suppressed when `not bot_enabled`
(before the state write, so `last_notified_at` isn't advanced for unsent
alerts).

### DM button actions (l.3299)
`get_default_preset`, **`create_preset_from_rules`** (the ONLY preset write
shape — welcome wizard uses it; direction encoded as offset sign),
`set_default_preset`, `apply_default_preset` → status
'no_default'|'already_covered'|'applied', `remove_user_rules`,
`snooze_reminder(queue_id, user_id, days=1)` → 'snoozed'|'too_close'
(capped so it never sleeps past the anchor)|'not_yours'|'gone'.
`notice_context` / `leg_cancelled_context` — embed data built per recipient;
locale = `user.language` explicitly (bug site once).

### Venue rollup (l.3518)
**`sync_concert_venue_tags(session, concert_id)`** — rewrites the concert's
VENUE ConcertTag rows as the union of legs' `venue_tag_id`s. Touches VENUE
rows ONLY (invariant 3 protects the rest). Returns newly attached tags —
**every caller MUST pass them to `handle_newly_tagged`** (venue followers are
owed the same DM as any tag attach, invariant 4). Callers: create route,
edit route, import_commit.

### Round-label phrases (l.3577)
`record_round_label_phrase` — complete trilingual triples only; bumps
used_count; contains the app's ONLY `try/except IntegrityError`
(`begin_nested()` savepoint so a two-editor race doesn't roll back the whole
concert save). `round_label_phrases` (most-used first), `forget_...` (never
touches rounds — suggestions, not FKs).

### Variant gaps (l.3661)
`concert_variant_gaps` / `tag_variant_gaps` — advisory edit-page notices,
grouped by language; never block edits.

### The three locale patterns (choose wrong and it's SILENT)
1. `get_locale()` — anything inside a web request (upcoming_deadlines,
   board_cards, concert_round_rows, my_deadline_rows, discover, setup).
2. `user.language` — per-recipient text composed outside a request
   (due_reminders, notice_context, leg_cancelled_context, scheduler, views).
3. Explicit `locale` param — `user_calendar_events` only (None = canonical
   .ics).
Beware: ~10 sites in service.py COPY label strings into dataclasses — the
field resolves at the copy site, not render time, so locale must be right
there.

---

## 8. Web layer

### app.py (345 lines)
- Middleware: `SessionMiddleware` (signed cookie, SameSite=Lax, https_only
  from base_url, 30 days) holding `sid`/`user` display dict/`oauth_state`/
  `oauth_next`; then a locale middleware — `lang` cookie if valid, else
  `i18n.negotiate(Accept-Language)`, else en → `set_locale` ContextVar.
- `LoginRequired` handler: htmx → 204 + `HX-Redirect` (target from
  `HX-Current-URL` through `safe_next` — never the fragment endpoint's own
  URL); full navigation → 303 to `/` with `?next=` for GETs only.
- Jinja globals/filters: `dual`/`dual_lines`/`day_month` (close over
  `get_locale()` — templates never pass locale), `jst`, `deadline_label`/
  `round_kind_label`, `current_locale`, `sentence_slots` (renders translated
  slot patterns; text escaped, only server-built selects pass as Markup),
  `loc`/`loc_name` (→ `loc_field`, display only), `static_url` (content-hash
  cache-bust), `login_url` (single source for every sign-in CTA — a bare
  `/auth/login` link silently drops `next`).
- **Router order is load-bearing**: `imports` router registered BEFORE
  `concerts` (else `GET /concerts/import` is swallowed by
  `/concerts/{event_id}`). Belt+suspenders: `RESERVED_EVENT_IDS`.
- `GET /` — signed out: real landing (hero, counts, static sample board,
  live `discover_peek`); signed in: resolves `tracked_concert_ids` ONCE and
  shares it between `board_cards` and `my_deadline_rows` (was a duplication
  bug); re-keys `columns` dict from Column members to `.value` strings (enum
  member ≠ value for hashing — template `columns["open"]` misses otherwise;
  same re-keying in outcomes.py and discover.py); `up_next` = soonest row
  with a round, whatever anchor (header "Up next" stays moment-agnostic —
  narrowing to CLOSES was rejected).
- `POST /language` — the single locale write path: always sets the cookie;
  updates `users.language` when signed in; validates `next` against the real
  route table.
- `GET /healthz` — public JSON; UptimeRobot keyword-monitors `"ok":true`
  (false after 3 missed scheduler ticks via `heartbeat.status`).

### auth.py
Discord OAuth code grant, scope=identify. `login`: mints `state`, stores
`oauth_state`+`oauth_next` (safe_next'ed; always assigned even when empty so a
stale destination can't outlive an abandoned login). `callback`: pops state
unconditionally, CSRF-checks, `exchange_code`/`fetch_identity` (module-level →
monkeypatchable), detects `is_new_user` BEFORE `ensure_user`, seeds
`users.language` from the lang cookie ONLY at account creation, mints
`WebSession` (sha256 hash stored; expired rows swept opportunistically),
redirects `/welcome` if new else `next` or `/`. `current_user`: validates
cookie sid against the DB row (revoked/expired/user-mismatch → pops cookie,
returns None); returns frozen `SessionUser` with `is_editor` (env OR admin OR
DB flag) and `dm_blocked` pre-resolved. `require_user` raises `LoginRequired`;
`require_editor`/`require_admin` are 403 HTTPExceptions on top.
`revoke_session` shared by logout and account deletion.

### Route table (method path → handler; auth; response)
- POST `/language` — set_language; public; 303 + lang cookie.
- GET `/healthz` — public JSON.
- GET `/` — home; optional user; home.html.
- GET `/auth/login`, `/auth/callback`, `/auth/logout` — public.
- GET `/concerts/new` — editor; concert_new.html. POST `/concerts` — editor;
  303 PRG.
- GET `/concerts/{event_id}` — user; concert_detail.html.
- GET/POST `/concerts/{event_id}/edit` — editor; PRG (POST never returns a
  fragment).
- GET `/concerts/{event_id}/export.yaml` — user; yaml attachment.
- POST `/concerts/{event_id}/duplicate` — editor; 303 → new /edit.
- POST `/concerts/{event_id}/delete` — editor; 303 /.
- GET `/rounds/{round_id}/ics` — user; .ics attachment.
- POST `/round-phrases/{id}/forget` — editor; 204.
- GET `/concerts/import` — editor; import_form.html. POST `.../preview`,
  `.../draft` — editor; import_preview.html (render-only). GET
  `.../skill.zip` — editor. POST `.../commit` — editor; 303 (the ONLY import
  write path).
- POST `/concerts/{event_id}/rules`, POST `/rules/{rule_id}/delete` — user;
  `_rules.html` fragment.
- POST `/concerts/{event_id}/subscription`, POST
  `/concerts/{event_id}/legs/{day_id}/opt-out` — user; fragment if
  HX-Request else 303 (JS-off dual mode).
- GET `/tags` — user. POST `/tags` — editor; 303. POST `/tags/venue/quick`,
  `/tags/quick` — editor; JSON (409 on dupes; `/tags/quick`'s 409 carries
  structured `{message,id,name}` so the dialog can offer select-existing).
  POST `/tags/{id}/edit|delete`, `/tags/{id}/members`, GET+POST
  `/tags/{gid}/members/{mid}/retroactive-apply`, POST `.../delete` — editor.
- GET `/discover` — PUBLIC (the only anonymous content page); optional user.
- GET `/welcome` + POST `/welcome/preset|advance|skip-all` — user; step
  counter `users.onboarding_step`; advance past last step → 303 `/setup`.
- POST `/rounds/{round_id}/outcome` — user; hx-target `_deadline_rows` (Home)
  or `_round_rows` (concert page) + OOB `#board`/`#board-summary` or
  `_standing_strip`; raw `get_template().render()` concatenation — OOB
  fragments must stay top-level, never wrapped.
- GET `/preferences` + ~15 POSTs (presets CRUD, subscriptions CRUD,
  admin editor toggles, timezone, test-dm, delete account) — user
  (admin routes admin).
- GET `/setup`, `/setup/applications`, `/setup/ready` + POST `/setup/prune`,
  `/setup/applications` — user.
- POST `/me/calendar-feed` — user; GET `/calendar/{token}.ics` — **no session
  dep, token IS the credential**.
- GET `/privacy`, `/terms` — optional user.

### concerts.py mechanics (1576 lines — the hairiest file)
- Both create and edit: scalars → tag diff → flush (days need ids) → days →
  flush (round legs need day ids) → rounds → flush → qualifiers → venue
  rollup → sync_concert → commit → 303.
- `create_concert_row` (l.507) shared by manual create AND import_commit.
  **VENUE tags are validated then DROPPED here** — `sync_concert_venue_tags`
  is the sole VENUE writer (attaching early would fire handle_newly_tagged
  for a tag the rollup might delete in the same transaction).
- `parse_round_legs` (l.278): per-round-row `round_legs` field of
  space/comma tokens — day ids (existing legs) or `day_key` strings ("d0",
  legs created in the same submit, no id yet). Unknown tokens silently
  dropped. Returns None (not []) for empty = "General/all legs".
- `key_to_day_id`: built in the same loop iteration that creates each day,
  flushed, `setdefault` (first duplicate key wins).
- `parse_round_qualifiers` (l.326): same encoding, no provisional keys
  (only saved rounds can qualify); drops self-id/dupes/unknowns; [] = "any
  ticket qualifies".
- `concert_rounds_context` (l.803): everything `_round_rows.html` needs;
  eager-loads everything (venue tags via targeted id query, not name match);
  shared by concert GET, outcome POST, and leg-opt-out POST — three callers
  must agree.
- **Padding asymmetry (footgun!)**: free-text parallel arrays (labels, notes,
  doors) are end-padded when wholly omitted; BINDING arrays (`day_key`,
  `day_venue_tag_id`, `round_legs`, `round_qualifiers`) are padded ONLY on
  total omission — a partial array deliberately fails the `zip(strict=True)`
  with a loud 500 instead of silently sliding a binding onto the wrong row.
  Same pattern in create, edit, import_commit; import_commit additionally
  end-pads `*_label_en/zh` for a legacy minimal-client contract.
- `edit_concert` (l.1080): `before = snapshot_concert()` FIRST; scalars; tag
  diff excludes VENUE on both sides (fixes two shipped bugs:
  attach-then-rollup-delete same transaction; detach+re-attach double DM);
  days reconciled BY ID (update/insert/delete — never delete-and-recreate,
  or delivered reminders re-arm); `record_concert_edit` after mutations;
  qualifiers delete-then-insert per round; `notify_newly_cancelled_legs`
  BEFORE `sync_concert_venue_tags` → `handle_newly_tagged` → `sync_concert`.
- `duplicate_concert` (l.1449): clones scalars + non-VENUE tags with
  `expand=False`; NO days/rounds (new edition = new dates); NO VENUE tags
  (derived); deliberately NOT a variant-enforcement boundary (legacy records
  must stay duplicable; the edit page's gap notice surfaces it).
- Variant enforcement (`forms.require_variants` → 422 with row-numbered
  messages): CREATE boundaries only — create_concert, import_commit,
  tags create + venue/quick (mandatory) — NOT quick tag create (optional by
  design), NOT edit routes, NOT duplicate. Client-side `_variant_guard.html`
  blocks first; the 422 is the JS-off backstop.

### imports.py
- SSRF guards, three layers, don't loosen: (1) `_check_host` — https +
  hostname exactly `ramen.events`; (2) the same check re-run on EVERY redirect
  hop via an httpx response event hook (+ max_redirects=5); (3) body streamed
  with a 2 MB cap checked per-chunk (never buffered-then-checked). Plus 10s
  timeout. `import_commit` re-validates `source_url` via `form_url` (it
  round-trips through the browser as a hidden field).
- `import_preview`: fetch → `asyncio.to_thread(parse_ramen_event, ...)` (the
  loop also drives the Discord gateway — never parse inline); errors
  re-render import_form.html with the message; venue name →
  `match_venue_tag_id` stamped on every day.
- `import_draft` (POST /concerts/import/draft): pasted YAML ≤200k chars →
  `parse_draft` → same import_preview.html fully prefilled; per-leg venue
  resolution; `match_tag_ids_by_name` for pickers; unmatched names become
  quick-create chips (never dropped); `applies_to_labels` → `day_key`
  resolution with warnings for unresolvable labels. Render-only.
- `import_commit`: loop-built create with the same padding rules; no
  qualifiers field (parser never emits UPGRADE); `event_id` auto via
  `generate_event_id` (slugify + numeric suffix; slugify of Japanese-only
  titles collapses to "concert" — wishlist #1 wants title_en preference).
- `_PreviewLeg` namedtuple gives parsed days the same attribute surface as
  ORM days for shared partials.

### Other route files
- `reminders.py` — rule add/delete rendering via
  `concerts.render_rules_fragment`.
- `subscriptions.py` — follow/unfollow + leg opt-out; branches on HX-Request
  header (fragment vs 303); redirect target from HX-Current-URL then Referer.
- `outcomes.py` — the web half of outcome capture; shares
  `record_round_outcome`; returns MULTIPLE top-level fragments (hx-target +
  OOB) via raw template rendering — do not wrap in a container (htmx only
  honours top-level OOB). Sends `HX-Trigger` toast events.
- `welcome.py` — five steps in `users.onboarding_step`; the preset step
  writes through `create_preset_from_rules` (no second path); three template
  rule sets (Relaxed/Standard/On the ball) + sentence-style fine-tune.
  Offsets days+hours only (PresetItem has no minutes).
- `setup.py` — three GETs + two POSTs; no step state anywhere.
- `calendar.py` — feed mint + tokened .ics (no require_user by design).
- `preferences.py` — presets/subscriptions/timezone/test-dm/delete-account/
  admin editor toggles.
- `web/forms.py` — `form_url` (422 wrapper over clean_url),
  `require_variants`. Own module so concerts/tags/imports can all import it
  cheaply.

---

## 9. Bot layer

### client.py
`ReminderBot(commands.Bot)`, default intents, singleton `bot`. `setup_hook`:
`add_dynamic_items(*DYNAMIC_ITEMS)` (11 persistent button/modal classes),
loads cogs ping/reminders/admin, syncs slash commands (guild-scoped if
`DEV_GUILD_ID` set — seconds; else global — up to 1h).

### Cogs (all replies ephemeral)
- `/ping` — latency + role (admin/editor/viewer).
- `/upcoming [days=14]` (1–90) — global rounds horizon via `upcoming_rounds`.
- `/mydeadlines [count=10]` (1–25) — via `user_calendar_events(...,
  locale=get_locale())` — same source as the .ics feed.
- `/remindme concert anchor days_before` — creates a ReminderRule
  (offset_days = -days_before) + `sync_rule`; concert autocomplete = last 20
  by created_at, ilike filter.
- `/myreminders` — lists caller's rules.
- `/promote-editor member`, `/demote-editor member` (refuses env-whitelisted),
  `/list-editors` — guarded by `_reject_if_not_admin` (no decorator).
Cog testing pattern: `Cog.command.callback(cog, fake_interaction, ...)` with
a monkeypatched module `SessionMaker` — no gateway.

### messages.py
`KIND_EMOJI`, `ANCHOR_VERB` (N_-marked). `relative_phrase` ("in 2 days" /
"3 hours ago"). **`safe_button_url`** — clean_url wrapper returning None on
UnsafeURLError, because a bad button URL makes Discord 400 → classified
TRANSIENT → the queue row would wedge forever; dropping the button is safer.
Builders: `format_reminder` (plain text), `build_new_event_message` (embed +
state-aware view: Apply-default OR Remove, + Show-deadlines, + site link),
`build_leg_cancelled_message` (+ Reinstate), `build_reminder_message`
(outcome buttons gated by anchor+current outcome: CLOSES+None →
Applied/NotApplied; RESULTS+None|APPLIED → Won/Lost; PAYMENT+WON → Paid;
trailing RemindLater (CLOSES) or Snooze). All use ambient `get_locale()` —
callers set it first.

### views.py — persistent buttons
`custom_id` namespace: `dk:apply|remove|deadlines|snooze|reinstate|applied|
notapplied|won|lost|paid|remindlater:{id}`. Each is a
`DynamicItem[Button]` with a regex template + `from_custom_id` — identity
lives entirely in the custom_id, so buttons survive restarts with no
per-message state. Every callback: open session → `_apply_locale` (per-click,
per-Task ContextVar — race-free) → re-check state at click time (never trust
the label) → call the service function (`apply_default_preset`,
`remove_user_rules`, `reinstate_user_rules`, `snooze_reminder`,
`record_round_outcome` via shared `_handle_outcome_click`) → reply.
`RemindLaterModal` builds labels per-instance so they localize per click.

---

## 10. Scheduler (`scheduler/loop.py`, `heartbeat.py`)

`TICK_SECONDS=60`, `SEND_CONCURRENCY=5` (semaphore bounds Discord sends only —
DB work on the one shared session stays sequential), `HEALTH_EVERY_N_TICKS=5`.

`DeliveryOutcome`: SUCCESS (mark sent, clear dm_blocked), FORBIDDEN (mark
sent — permanent — and set dm_blocked_since), TRANSIENT_FAILURE (touch
nothing; retries next tick forever). `deliver()` is pure Discord I/O (no
session) → safe to run concurrently; sets locale from `item.user_language`,
resets to "en" in finally.

`tick(bot)` order: (1) increment `_tick_count` first (a raising tick must not
freeze health cadence); (2) drain `due_reminders` — gather sends, then mark
sent / record dm outcome; (3) drain `due_notifications` — DB context prep
sequential, sends concurrent; `_notification_context` dispatches on
`note.kind` (leg_cancelled vs notice vs plain body); (4) **commit delivery
bookkeeping**; (5) every 5th tick, `evaluate_and_alert(run_checks(...))` in
its own try/except + own commit — after the delivery commit, because DMs are
already on the wire and rolling back "sent" flags would double-send.

`reminder_loop(bot)`: `bot is None` (web-only) → beat + sleep forever (queues
accumulate). Else wait_until_ready, then forever: `heartbeat.beat()` BEFORE
tick (freshness decoupled from tick success), tick in try/except (a dead loop
is the one unacceptable outcome), sleep.

`heartbeat.status()`: healthy if last beat (or process start, grace) < 180s.
Consumed by `/healthz` — the pull path is the real scheduler-death detector;
`ops.check_scheduler` is `alerting=False` because from inside its own tick
it's structurally always fresh.

`ops.py` REGISTRY: backup (alerting, marker file + 36h staleness + startup
grace), disk (alerting, hysteresis via OpsCheckState row), scheduler
(non-alerting), dms (non-alerting; count never exposed in public detail).
`_redacted()` returns exception TYPE only (healthz is public; SQLAlchemy error
strings leak schema). Checks never raise (`safe_run`).

---

## 11. i18n (`i18n.py`)

`SUPPORTED = ("en","zh","ja")`. Locale = asyncio `ContextVar`
(`get_locale`/`set_locale`; setter silently falls back to en on unsupported).
`en` is `NullTranslations` — identity; no `translations/en/` dir; EN output is
byte-identical to pre-i18n (no EN test asserts translated strings). ja/zh
`.po` files compile to `.mo` **in memory at first use** (`use_fuzzy=False` —
fuzzy = untranslated), cached per process; no .mo on disk ever.

API: `gettext`/`_`, `ngettext`, `N_` (no-op extraction marker for
module-level dicts — translate later at lookup, never at definition),
`gettext_in(locale, msg)` (explicit-locale escape hatch for text composed
before a recipient is known), `loc_field(obj, field, locale)` (en/zh →
`{field}_{locale}` if truthy, else the base column = the ja original; empty
counts unfilled; NO cross-locale chaining), `negotiate(accept_language)`
(primary subtags, q-values ignored), `reset_catalog_cache()` (tests).

Cookie/DB contract: the `lang` cookie is a CACHE of `users.language`, never
the source of truth. Single write path `POST /language` (cookie always; DB
column when signed in — Discord DMs read the column). OAuth callback seeds
the column from the cookie ONLY at account creation.

Editing existing English copy: msgid must stay byte-identical or both
catalogues silently lose the translation; update BOTH .po files.
`tests/test_i18n_catalogues.py` extracts every msgid in-process and fails on
anything untranslated, and checks placeholder integrity (`{name}`/`%(name)s`
sets must match between msgid and msgstr).

`babel.cfg`: `[python: src/app/**.py]` + `[jinja2:
src/app/web/templates/**.html]`.

---

## 12. Templates & frontend

33 templates: base + 14 pages + 19 `_partials`. Every page:
`{% extends "base.html" %}` + `block title` + `block content` only. No
separate JS files — all JS inline in templates. htmx 2.0.4 from unpkg CDN.

**base.html**: no-flash theme stamp in `<head>` (reads localStorage.theme,
stamps `data-theme` on `<html>` BEFORE CSS — move it to body and every page
flashes); header (mark できません + wordmark, nav Home/Discover/Tags(-if-user),
theme toggle, language cycle chip, auth cluster); dm_blocked banner;
phone-only `.tabbar` + editor `.fab`; shared scripts: dialog
backdrop-click-close, `filterChips` (filters by `data-name` — hence the
data-name collision rule), confirm-via-dataset helpers, countdown refresher
(60s + htmx:afterSwap), `#hxbar` progress, toast listener on HX-Trigger
events, auto-timezone detect (posts `/me/timezone/auto` while tz_auto).

**Language chip**: cycle EN → 中文 → 日本語, plain POST form to `/language`
(works with JS off). Language names never translated. A dropdown shipped once
and was replaced at the owner's request — don't bring it back.

**htmx conventions**: every htmx form also carries plain
`method="post" action=...` (JS-off dual mode). Fragment responses come from
per-partial context-builder functions (`concert_rounds_context`,
`following_toggle_context`, `render_rules_fragment`) shared by every route
rendering that partial — the pattern preventing fragment/page drift. OOB
swaps: outcome POST returns hx-target fragment + `#board`/`#board-summary`
(Home) or `_standing_strip` (concert page) with `hx-swap-oob` — top-level
elements only. POST-only-rendered pages set `lang_next_url` so the language
chip has a GET-routable next.

**Editor cards**: `_editor_leg_card.html` / `_editor_round_card.html` macros
are the ONLY leg/round card source — concert_new, concert_edit,
import_preview loops AND their `<template>` clone blocks all use them (a
six-site duplication existed and was removed; never hand-roll a card).
Anatomy: ja label top row, EN/中文 on the always-visible `.vary` row, fields
below. Destructive actions live in the kebab menu (`details.kebab` — the
app's ONLY overflow menu, destructive-only; the inline × was removed by owner
ruling; folding Edit/Export into it was proposed and rejected). Blank venue
option must stay first in the leg select.

**Tag picker** (`_tag_picker_script.html`): dialogs per kind
(franchise/group/artist; venue is never picked at concert level — derived
from legs). JS keeps Sets + `artistExcluded`/`artistManual` so group
selection auto-adds members but pruning sticks; `syncHidden()` rebuilds
hidden inputs. `window.pickerAddAndSelect(kind, tag)` is the hook quick-create
dialogs call; create-dialog scripts must be included AFTER the picker script.
Context passed as raw Python dicts + `| tojson` (escapes `</script>` as
\uXXXX).

**Design tokens** (`style.css`, 1870 lines): `:root` block (`--paper --raise
--ink --dim --line --ok --off --danger --accent --*-wash --chip --shadow`),
defined BOTH via `@media (prefers-color-scheme: dark)` AND
`:root[data-theme=...]` (toggle wins on specificity). Radii: 3px default,
999px pills, 4px overlay cards, 50% circles, bottom sheets `14px 14px 0 0` —
pinned by tests (6px/8px asserted absent). Type ramp 400/600/700 only.
Motion budget: one 150ms card lift + `#hxbar` — nothing decorative (owner
ruling 2026-07-24). Callout shapes: `.edgecard` (raise ground, tone left
edge — ongoing state) and `.banner` (wash ground, full border — needs
attention); never invent a third.

**Breakpoints** (count pinned by test at exactly 6 top-level max-width
blocks): 1040 (`.layout` collapse; paired `min-width:1041` fsheet flip — the
invariant is "same boundary"), 900 (`.rnd2`), 860 (`.plyt`), the tablet band
`701–1040` (one banner-commented section: compact header via
`.nav-lbl`/`.nav-ico`, swipeable 280px board rail, `.peek` 2-col,
data-happens fold, inline filter panel), the phone section `≤700` (one
section at the end holding ALL phone rules: tabbar, fab, bottom-sheet
dialogs at 78dvh, board carousel — with a NESTED `≤380` block hiding the
wordmark so the CJK language chip doesn't wrap the header), and a second
700 block for the `.fsheet-js` overlay (JS-gated so no-JS phones get the
in-flow fallback). Discover's collapse-on-load JS runs at ≤1040; the
bottom-sheet overlay presentation is ≤700 only.

**Display rules**: deadline times = two lines via `dual_lines` (bold
weekday+day+month, then "HH:MM JST, HH:MM local"); `fmt_dual` one-liner is
Discord-only. Performance dates = `day_month` (no zone, no dual). Tiles:
franchise+group → "F · G"; group only → G; artists only → chips; >1 venue →
"📍 Multiple". Sentence case everywhere. Discover: one merged status pill
per card; three filters (chips, search incl. tag names + free-text-venue
fallback, round-status facet) AND-combine; initial state server-computed,
subsequent changes client-side off `data-tags`/`data-search`/`data-status`,
controls stay real links/forms for JS-off. Capture actions live on Coming-up
rows, never board cards (a board card is a whole campaign — "applied to
which round?" is ambiguous).

**Design sources of truth**: `docs/superpowers/demo/dekimasen-demo.html`
(Home/Discover/concert/editor/tags/preferences/setup),
`dekimasen-onboarding-demo.html` (landing/welcome/import/legal),
`dekimasen-mobile-demo.html` + `dekimasen-mobile-live.html` (phone). Review
UI changes against the matching demo; if the shipped design deliberately
moves, update the demo.

Note: `static/_tablet_harness.html` is a leftover dev measurement tool marked
"delete before committing" — not part of the app.

---

## 13. Migrations (Alembic + SQLite)

`alembic/env.py`: url from settings (strips `+aiosqlite`),
`render_as_batch=True` both paths (SQLite can't ALTER much; batch = table
rebuild). 30 migrations; head `ce43bfcfcae3` (drop legacy venue columns).

Gotchas (each has bitten):
- Batch mode refuses unnamed constraints → NAMING_CONVENTION on
  `Base.metadata`; keep it.
- **The live DB predates the convention and tests can't see it**: old tables
  (`concerts`, `tags`, `concert_days`, `rounds`) carry anonymous constraints
  on the server; every test DB built from metadata is fully named, so
  `drop_constraint("fk_...")` passes locally and dies in prod (shipped once).
  Any migration touching drop_constraint must (a) pass
  `naming_convention=NAMING_CONVENTION` into `batch_alter_table` (every
  existing migration does this explicitly) and (b) be tested against a
  hand-written legacy-DDL fixture
  (`tests/test_migration_legacy_anonymous_constraints.py` — covers only the
  four tables that migration touched; new legacy tables need their own DDL).
- After autogenerate, ALWAYS edit: replace `app.db.models.UTCDateTime()` with
  `sa.DateTime()` and drop the models import.
- `alembic.ini` and configs must stay ASCII-only (owner's GBK locale crashes
  on em-dashes).
- SQLite `trim()` strips only U+0020; Python `.strip()` is Unicode-aware —
  any migration matching app-written text needs an explicit trim charset
  including U+3000 (see `789bbcc95bc3`'s `_TRIM_CHARS`).
- Column-DROP migrations reverse the deploy order: restart on new code
  BEFORE `alembic upgrade head` (so the old process can't SELECT dropped
  columns mid-deploy) — `ce43bfcfcae3` did this.

---

## 14. Testing conventions

- pytest-asyncio auto mode — `await` directly.
- `conftest.py` is tiny: one autouse fixture resetting
  `scheduler.loop._tick_count` (module-level counter would leak health-check
  cadence across tests). Everything else is per-file, hand-copied:
  in-memory `create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)`
  + **the `PRAGMA foreign_keys=ON` connect listener (REQUIRED — cascades
  silently don't fire without it; a few older files omit it)** +
  `Base.metadata.create_all` + `async_sessionmaker(expire_on_commit=False)`.
- Web tests: `create_app()` + `dependency_overrides[get_session]` +
  `TestClient`. Login helper monkeypatches `auth.fetch_identity`/
  `auth.exchange_code` and drives the REAL /auth/login → /auth/callback flow
  (extracting `state` from the redirect) so real cookie machinery runs.
- Every page needs at least one logged-in GET render test (a missing one
  shipped a 500 from context drift).
- Discord is never imported in service tests. Cogs are tested via
  `Cog.command.callback(cog, FakeInteraction, ...)` + monkeypatched cog
  `SessionMaker`. Fake bot objects for scheduler tests.
- Guard tests pinning conventions (don't break them casually):
  `test_theme_and_tokens.py` (token names, both dark-mode directions, 3px
  radius, `[hidden]` !important rule, exact breakpoint count = 6, tablet
  band section), `test_i18n_catalogues.py` (catalogue completeness both
  locales, fuzzy = untranslated, placeholder integrity),
  `test_migration_legacy_anonymous_constraints.py`,
  `tests/fixtures/ramen_*.html` (captured real pages for ingest tests),
  `test_yaml_import.py::test_skill_example_draft_parses_clean` (pins the
  add-concert skill's example draft to the parser).
- Subagent/process note (owner workflow): run test suites in the foreground
  when delegating to implementation subagents; background runs stall them.

---

## 15. Deploy & ops

Lightsail Ubuntu 24.04, app at `~/app`, systemd unit `concert-reminder`,
Caddy with a Cloudflare Origin cert. Ritual:
`cd ~/app && git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder`
(reversed for column-DROP migrations — restart first). Caddyfile changes:
`sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`.
Logs: `journalctl -u concert-reminder -f`. Health: `/healthz` (UptimeRobot
keyword `"ok":true`; false after 3 missed ticks). Nightly S3 backups
(`deploy/backup.sh`, cron, 30-day lifecycle; success marker file is the
app's only backup evidence — IAM is PutObject-only). Never commit `.env`.
`deploy/`: backup.sh, Caddyfile, concert-reminder.service, setup.sh.
Full runbook: `docs/deploy.md`.

---

## 16. How to do common tasks safely (checklists)

**Add/modify a concert-editing surface**: use the shared partials
(`_editor_leg_card`/`_editor_round_card`); respect the padding asymmetry;
after any day/round/tag mutation call `notify_newly_cancelled_legs` (if legs
newly cancelled) → `sync_concert_venue_tags` → feed its return to
`handle_newly_tagged` → `sync_concert`; snapshot before / record-edit after;
selectinload `venue_tag` for anything reaching a template.

**Add a new reminder-affecting write**: never touch `reminder_queue`
directly — go through `sync_rule`/`sync_concert`/`reinstate_user_rules`.
Outcome writes ONLY through `record_round_outcome`.

**Add a route**: pick the right auth dependency (LoginRequired vs 403
semantics); mutating POSTs redirect 303 (PRG) unless htmx fragment; fragments
get a context-builder function shared with the page route; `next` handling
via `login_url`/`safe_next`; editor URLs through `form_url`; new-event DMs
through the notifications outbox.

**Add translatable copy**: `_("literal")` at render/lookup time; `N_()` for
module-level dicts; run the pybabel extract/update cycle; fill BOTH `.po`
files (fuzzy counts as missing); keep existing msgids byte-identical;
placeholders must survive translation. Choose the right locale source
(request → `get_locale()`; per-recipient outside request → `user.language`;
.ics → explicit None).

**Add a migration**: autogenerate → replace UTCDateTime with sa.DateTime →
batch mode + naming_convention for any constraint work → legacy-DDL test if
dropping constraints → ASCII only → consider deploy order for drops →
explicit trim charset for text matching.

**Add UI**: check the matching demo file first; both theme directions; 3px
radius; phone rules only inside the single ≤700 section (nested ≤380);
tablet rules only inside the 701–1040 section; chips are the universal
element; dialogs are native `<dialog>` → bottom sheets on phone; sentence
case; no new overflow menus; no decorative motion.

**Feature planning**: read `WISHLIST.md` first (impact-ordered; Shipped and
Rejected sections maintained; every ship triggers a re-rank pass). Follow
the spec+plan pattern in `docs/superpowers/specs|plans/` for substantial
features. Current top wishlist entries: (1) event_id slugs should prefer
title_en; (2) agent-import review-debt batch; (3) minute-level reminder
offsets (PresetItem/ReminderRule lack offset_minutes; FCFS made it more
relevant); (4) Eventernote actor-page discovery feeding the draft import;
(5) collapse a round's multiple Coming-up rows (Home emits one row per
anchor; concert page already collapses via `_primary_anchor`).

---

## 17. Bug-shipped-once list (the scars behind the rules)

- Lazy load during async render → `MissingGreenlet` 500 (hence
  `lazy="raise"` on `ConcertDay.venue_tag`, eager-loading everywhere).
- `drop_constraint` passing locally, dying in prod on anonymous legacy
  constraints (hence the legacy-DDL migration test).
- Free-text venue name-matching leaving a re-pointed leg rendering its old
  venue forever (hence venue-as-FK and no `find_venue_tag`).
- Missing page render test shipping a 500 (hence one logged-in GET test per
  page).
- VENUE tag attach in the normal diff path double-firing new-event DMs and
  attaching-then-deleting in one transaction (hence VENUE exclusion +
  rollup-as-sole-writer).
- Duplicated `tracked_concert_ids` derivation on Home (hence resolve-once
  and share).
- `[hidden]` losing to `.upgradebox{display:grid}` (hence the `!important`
  rule + its pin test).
- Locale resolved from the wrong source rendering text in somebody else's
  language, silently (hence the three-pattern doctrine and per-site
  comments).
- CPython 3.12.0–3.12.4 comprehension variable leak shadowing the gettext
  `_` alias (hence `_label` loop names in discover.py).
- `%-d`/`%#d` strftime non-portability on Windows (hence hand-built day
  numbers in timezones.py).
