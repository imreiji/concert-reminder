# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## dekimasen.app (concert-reminder)

Discord bot + web app tracking Japanese concert deadlines (lottery rounds,
serial-code sales, stream tickets). One Python process runs three things on a
single asyncio loop: discord.py bot, FastAPI web (Jinja2 + htmx), and a 60s
scheduler tick. SQLite + SQLAlchemy async + Alembic. Live at dekimasen.app
(AWS Lightsail behind Cloudflare). ~2,500 tests.

**Two companion documents. Read on demand, not every session:**

- `docs/architecture.md` — per-module detail: why each module is shaped the
  way it is, and which reasonable-looking edits would undo a measurement or
  re-open a fixed incident. **Read a module's entry before changing it.**
- `docs/ui-conventions.md` — the full design detail behind the short rules
  under "UI conventions" below.

The running feature history that used to sit here is in `WISHLIST.md`'s
Shipped section (dated, with the reasoning that produced each one) and
`README.md`'s roadmap. It was a duplicate of both, and a list of what already
works is the one thing a reader can always recover from the code itself.

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

One line per module. The reasoning, the measurements and the "do NOT simplify
this" warnings live in `docs/architecture.md` — **read a module's entry there
before you change it.** Almost every one of them records either a measurement
or a shipped incident, and most are things a reasonable-looking edit would
undo.

- **`src/app/domain/` — pure logic, NO I/O.** No discord/fastapi/sqlalchemy
  imports, ever. Reminder math (`reminders.py`), JST↔UTC (`timezones.py`),
  board column precedence (`board.py`), URL safety (`urls.py` — `clean_url`
  for editor input, `safe_next` for the post-login redirect), sentence slot
  patterns (`sentence.py`), evidence grounding (`round_evidence.py`), LLM
  prompts (`triage_prompts.py`), and the parsers/formatters: `ingest.py`
  (ramen.events), `eventernote.py`, `ics_read.py`, `ics_export.py`,
  `yaml_export.py`/`yaml_import.py`, `tags_yaml.py`, `tags_diff.py`,
  `discovery_message.py`, `page_text.py`, `prune_list.py`.
  Two habits hold across the whole package: a parser takes a STRING and never
  fetches, and it warns-and-skips rather than raising, so a third party's
  redesign degrades to "found nothing" instead of crashing a scheduler tick
  every day.
- **`src/app/db/` — the DB layer, discord-free so it's testable.**
  `service.py` is a FACADE re-exporting the whole layer; `core.py` is the
  engine (queue sync, retrieval, the personal board, the concert page,
  Discover status, DM button actions, presets/subscriptions, users,
  adapters); beside it sit `tags`, `venues`, `drafts`, `discovery_events`,
  `setup_flow`, `calendar_feed`, `rehearsal`, `delivery`, `broadcast`,
  `ops_alerts`, `audit`, `phrases`, `translation_gaps`, and `quiet_ladders`
  (round watch: which concerts in the catalogue hold no future deadline).
  Dependencies point ONE way: feature modules import `core`, `core` imports
  none of them, and the facade imports everything and is imported by nothing.
  **Add a name to a module and you must add it to `service.py` too** —
  `tests/test_service_facade.py` fails if they disagree. A feature module
  must NEVER import the facade; that is a cycle which surfaces or not
  depending on which module a process imports first.
- **`src/app/bot/` and `src/app/web/` are thin shells** — cogs, embed
  builders, routes, templates. They contain NO business logic; they call
  `db/service.py`. Keep importing from the facade, not from `core` or a
  feature module: the facade is the seam. `web/routes/api.py` is the
  read-only agent API at `/api/v1`, bearer-token authenticated, GET only —
  see `docs/agent-api.md` for usage and
  `docs/superpowers/specs/2026-08-08-agent-read-api-design.md` for the design.
- `src/app/scheduler/` — the 60s tick that delivers DMs.
- `src/app/fetching.py` — the ONE outbound HTTP fetch, SSRF-guarded, shared
  by the importer, the discovery sweep, the calendar feeds and triage. Takes
  a host POLICY (`PinnedHost` / `ApprovedPublicHosts`). Don't add a third
  policy or a bypass.
- `src/app/discovery.py` (the sweep runner), `src/app/calendars.py` (which
  `.ics` feeds it reads), `src/app/llm.py` (the one DeepSeek call),
  `src/app/triage.py` and `src/app/draft_completion.py` (the AI passes),
  `src/app/ops.py` (health checks), `src/app/i18n.py` (gettext plumbing).
  All sit ABOVE `db/`: they import it, nothing in it imports them.

### The few footguns worth keeping in front of you

- **`routes/imports.py` MUST be registered before `routes/concerts.py`** in
  `web/app.py`, or `GET /concerts/import` is swallowed by
  `GET /concerts/{event_id}` — FastAPI matches path templates before literal
  segments.
- **Starlette hard-caps every `Form(...)` field at 1MB**, whatever an
  app-level constant says. This applies to every form in the codebase; any
  large-paste feature hits it as an opaque failure well before its own limit.
- **Venues live on the LEG, as a tag.** `ConcertDay.venue_tag_id` is the
  structured venue and the only one anything reads. A concert's VENUE tags
  are DERIVED by `sync_concert_venue_tags`, never typed, and every caller
  must feed its newly-attached tags to `handle_newly_tagged` (invariant 4).
  `ConcertDay.venue_tag` is `lazy="raise"` on purpose — a lazy load during
  async template rendering is a `MissingGreenlet` 500. The legacy free-text
  venue columns are GONE; do not reintroduce them.
- **A CHARACTER is a tag, and `Tag.voiced_by_tag_id` says who plays her** —
  not `parent_id`, which means "the broader thing I belong to" and is what
  the Tags page renders its hierarchy from. Only an ARTIST may voice a
  character, checked at both write boundaries. A tag's KIND is immutable.
- **`i18n` has THREE locale sources and picking the wrong one is SILENT** —
  nothing raises, the text just comes out in someone else's language.
  `get_locale()` inside a web request; `user.language` for per-recipient text
  composed outside one (scheduler DMs); an explicit `locale` parameter where
  the caller must decide (only `user_calendar_events`, where `None` keeps the
  `.ics` canonical). This bites hardest in `db/`, where ~10 sites copy a
  label into a dataclass before it ever reaches a template — the field
  resolves at the COPY site, not at render time.
- Editing existing English copy must keep the msgid byte-identical, or both
  catalogues silently lose that translation, and must update both `.po`
  files. `tests/test_i18n_catalogues.py` fails on anything untranslated.

- `docs/superpowers/specs/` + `plans/` — date-prefixed design specs and
  implementation plans; substantial features commit one of each before code.
  `docs/codebase-review-2026-07-17.md` records a full-codebase review.

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

9. **A TAG follow carries a preset, and `TagSubscription.preset_id = NULL` is
   OVERLOADED.** This is the tag side of invariant 8 and a different table --
   don't merge the two. Following a tag links the follower's default preset
   (`get_default_preset`) whenever the form supplies none; before 2026-08-13
   `subscribe` wrote `preset_id or None` and the chip forms sent nothing, so
   EVERY follow linked no preset at all and the per-tag preset UI had nothing
   flowing into it. NULL now means BOTH "never configured" AND "auto-apply
   deliberately off" -- `/subscriptions/{id}/settings` still writes it for the
   dialog's "none" -- and the owner ruled (2026-08-13) that the app WARNS about
   that ambiguity rather than growing a column for it. So
   `POST /presets/apply-to-following` fills NULLs ONLY, never overwrites,
   reports BOTH counts (filled, and left alone), and says in its confirmation
   that it re-arms auto-apply where you had switched it off. **Never widen that
   fill**: overwriting a preset somebody deliberately set is silent,
   irreversible, and looks exactly like success.
   `handle_newly_tagged` prefers a NON-default preset over the default, ties
   earliest-first -- a preset chosen for ONE tag beats the catch-all. That is
   not cosmetic and not the old rule with extra steps: reverting it to plain
   earliest-created-wins re-opens a measured regression (offsets went -1 to -3
   once the fill put the default on the oldest rows), and it changes behaviour
   for every user, not only those who press the fill. The whole four-phase
   rework shipped with ZERO migrations; `TagSubscription.preset_id`/`notify`
   and `ReminderPreset.is_default` carry all of it.

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
- **Use `tests/conftest.py`'s shared `db` / `session` fixtures; do not write
  a new one.** `db` yields an `async_sessionmaker`, `session` yields one open
  `AsyncSession` on the same database, and both give a fresh in-memory DB per
  test with `PRAGMA foreign_keys=ON` registered (production does; cascades
  silently don't fire without it -- and a missing cascade makes a test PASS,
  so nothing reports it). These replaced 81 hand-copies of one fixture across
  79 files, two of which had already lost that pragma. Override locally only
  to ADD setup, and derive from `db` when you do -- see
  `test_fetch_domain_service.py`, which seeds the users its FKs need. The
  schema is built from statements compiled once per process rather than by
  `create_all`; `test_conftest_fixtures.py` diffs the two schemas so that
  optimisation cannot drift.
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

The hard rules. The reasoning, the exact token values, the per-surface
anatomy and the mobile/tablet detail are in `docs/ui-conventions.md` — read
it before changing the interface. The **design source of truth** is the
concept demos in `docs/superpowers/demo/` (inventory in that file); when the
shipped design deliberately moves, update the matching demo so it stays the
reference.

- **Measure a layout bug; do not reason about it.** Before diagnosing any
  layout, overflow or breakpoint problem, put the real app in a real viewport
  at a real width and read the numbers off it. Reasoning from the CSS shipped
  a confidently wrong fix twice. The tell is a sentence of the shape "this
  must be overflowing because..." with no measurement in it.
- Sentence case everywhere ("Add group", not "add group").
- **Times always render dual: JST + the user's timezone.** On the web that is
  `fmt_dual_lines`/`dual_lines` (two lines); `fmt_dual` is the one-line shape
  and is for Discord embeds only. Performance DATES use
  `fmt_day_month`/`day_month` — day-month, no zone, no dual apparatus. A date
  is a fact about the world; invariant 1 governs deadlines you must act by.
- **Theming**: design tokens live in `style.css`'s `:root`. Dark mode is
  defined BOTH ways — `@media (prefers-color-scheme: dark)` and
  `:root[data-theme="dark"]`. `base.html` stamps the saved theme in `<head>`
  before first paint; do it later and every page flashes the wrong theme.
  Style new components against both directions.
- **Radiuses: 3px default, 999px chips, 4px overlay cards, 50% circles,
  bottom sheets `14px 14px 0 0`.** Never 6px or 8px — there is a sweep test.
  Type ramp is 400/600/700 only. Motion budget: one 150ms card-lift hover
  plus the `#hxbar` progress bar, nothing decorative.
- **Two callout shapes and no third**: `.edgecard` (raise ground, coloured
  left edge — ongoing state) and `.banner` (wash ground, full border — needs
  attention).
- Tag chips are the universal element. Pickers are native `<dialog>` white
  cards. **Backdrop-close comes ONLY from `base.html`'s global drag-safe
  handler** — never add a local `e.target === dlg` handler; that shipped the
  drag-out-closes-the-dialog bug twice and a sweep test now forbids it.
- Editor leg/round cards render through the shared partials
  `_editor_leg_card.html`/`_editor_round_card.html` — never hand-roll a copy.
  Destructive actions live in the kebab menu, which is the app's only
  overflow menu and stays destructive-only.
- **A `<details>` inside a swappable region carries a `data-fold` key**, or
  it snaps shut whenever the region is swapped by htmx.
- **The 🌐 language switcher is a CYCLE CHIP, not a dropdown** (a dropdown
  shipped first and was replaced at the owner's request). Language NAMES
  (EN/中文/日本語) are never translated.
- **Home vs Discover**: `/` is personal and login-gated ("where do I stand");
  signed out it is the real landing page. `/discover` is the public
  catalogue ("what's on") and is the only content page an anonymous visitor
  can reach. Header nav is Home / Discover / Tags and nothing else.
  `/following` manages the tags you follow and is deliberately NOT in the nav
  -- it is reached from Preferences' "Manage →" and from `/tags`' "See what
  you follow". `/tags` is the surface where you START following (chips toggle
  follow; an explicit edit-mode toggle switches every chip's click back to
  the tag editor for editors); `/following` is where you TUNE it, one dialog
  per followed tag. Preferences keeps only a fixed-height summary of both.
- **Capture actions live on Coming up rows, never on board cards.** A
  deadline row is one round on one leg, where "I have applied" has a single
  meaning; a board card is a whole campaign, where the same button is
  ambiguous. Do not "improve" the board by adding buttons to it.
- Discover's filters (tag/region chips, free-text search, round-status facet)
  combine as AND. Each computes its initial state server-side, filters
  client-side with no round trip, and stays a real `<a href>`/GET form so it
  degrades with JS off. Add a fourth the same way.
- **Mobile is a retrofit, not a second design**: every phone rule lives in
  banner-commented `@media (max-width: 700px)` blocks at the end of
  `style.css`, and the tablet band (701–1040px) in its own block before them.
  Desktop pixels stay untouched by construction. `test_theme_and_tokens.py`
  pins the top-level query count so scattered-breakpoint drift fails CI.

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
