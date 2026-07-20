# Onboarding Refactor (First-Run Capture Flow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Dependency: branch 4 MUST be merged first

This branch consumes branch 4's `ConcertSubscription` work (the
override-model follow/prune rows, the rewritten tracked-concert derivation,
and the Preferences "Run first-time setup again" link that targets
`GET /setup`). **Do not start until branch 4 is on `main`.** Task 1 begins by
reading branch 4's merged code and binding this plan's assumed names to the
real ones. If branch 4 exposes no way to write a concert-level opt-out or to
clear an override, or its tracked-set function does not fold overrides in —
**stop and flag it**; do not write `ConcertSubscription` rows directly and do
not add columns. Needing a schema change anywhere in this plan means the
dependency ordering is wrong, not that a migration task is missing.

**Goal:** After the `/welcome` wizard, walk a new user through a three-screen
capture flow — prune the concerts their tags imply, record which rounds they
already applied to, then reveal their board — so Home's first render matches
reality instead of converging on it over weeks of DMs.

**Architecture:** Three plain GET routes (`/setup`, `/setup/applications`,
`/setup/ready`) rendering purely from DB state, plus two batch POSTs. No
capture-flow step state exists anywhere — `onboarding_step` keeps its exact
current writers and meaning (0–4 wizard, ≥5 done); the only wizard change is
that `POST /welcome/advance` redirects to `/setup` when it crosses into done.
Pruning delegates to branch 4's override service functions; applications go
through the existing `record_round_outcome`. Re-running the flow (from
branch 4's Preferences link) needs no machinery: every screen renders current
truth and every write is an idempotent diff.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx (barely — the flow is
plain forms), SQLAlchemy async, SQLite, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-19-onboarding-refactor-design.md`

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean
  before every commit.
- Baseline: **638 passed, 1 failed**. The failure is
  `tests/test_crud.py::test_test_dm_when_bot_disabled` — the repo-root
  `.env` sets a real `DISCORD_TOKEN` while the test assumes empty.
  Pre-existing, local-only, CI green, **OUT OF SCOPE**. Verify against
  reality, not this plan's arithmetic (branch 4 will have moved the count).
- TDD: failing test first, run it, confirm it fails for the right reason,
  then implement.
- **No schema change, no migration.** (See the dependency note above.)
- `RoundOutcome` writes go through `record_round_outcome` only — no second
  write path (invariant 2). `ConcertSubscription` writes go through branch
  4's service functions only — never the table directly, and never with a
  locally invented queue-sync.
- `onboarding_step` is never read from user input and gains no new values.
  The capture flow has no step state; each screen is its own URL.
- Business logic in `db/service.py`; routes assemble context;
  `src/app/domain/` stays pure.
- Invariant 7: no user-controlled text in inline `on*` handlers; `| tojson`
  never `| safe` in inline scripts; `data-` attributes not named
  `data-name` (collides with `base.html`'s `filterChips()`).
- Times dual, JST first, via `fmt_dual` — including tile status lines,
  where the concept's bare "closes in 6h" is deliberately NOT copied for
  timestamps.
- Sentence case throughout. Every page gets a logged-in GET render test.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- `routes/imports.py` stays registered before `routes/concerts.py` in
  `web/app.py` (untouched here, but do not reorder while registering the
  new router).

## How to read this plan

The interactive concept at
`https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` is
the **reference implementation for markup, CSS and copy** — open the
**Setup** view via its header. Port the `.steps`/`.stepdot`, `.pick`/`.tile`,
`.bar`/`.tally`, and `.reveal`/`.tallies` styles and the screen copy from it;
do not redesign. Two deliberate deviations: tiles are checkbox `<label>`s
styled on `:checked` (not `aria-pressed` buttons driven by JS), and
timestamps render dual via `fmt_dual`.

This plan writes branch 4's override API as `set_concert_optout(session,
user_id, concert_id)` / `clear_concert_override(session, user_id,
concert_id)` and the tracked set as `tracked_concert_ids(session, user_id)`.
**Task 1 Step 1 binds these to the real merged names** — after that, use the
real names everywhere and note the mapping in your report.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/service.py` (modify) | `setup_prune_tiles`, `setup_application_rows`, `setup_tallies`, `apply_prune_selection`, `record_setup_applications`, and the screen-2 eligibility predicate. |
| `src/app/web/routes/setup.py` (new) | The five routes: three GETs, two POSTs. Thin shell. |
| `src/app/web/templates/setup.html` (new) | All three screens, branched on a server-set `screen` context value. |
| `src/app/web/static/style.css` (modify) | Tile/stepdot/reveal styles ported from the concept. |
| `src/app/web/app.py` (modify) | Register the router, inject `templates`. |
| `src/app/web/routes/welcome.py` (modify) | `advance` redirects to `/setup` on crossing into done. |
| `tests/test_setup_service.py` (new) | Service-layer: sets, predicate, tallies, writes. |
| `tests/test_setup.py` (new) | HTTP: renders, POST behaviour, re-run. |
| `tests/test_welcome.py` (modify) | The handoff redirect. |

---

### Task 1: Service layer — the three read shapes and the eligibility predicate

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_setup_service.py` (new — copy the engine/session fixture
  shape from `tests/test_service.py`, including the `PRAGMA foreign_keys=ON`
  listener)

**Interfaces:**
- Consumes: branch 4's tracked-set function and override rows;
  `is_round_cancelled`, `_round_is_open`, `_round_has_opened`,
  `_result_moment`, `_next_deadline` (all existing in `service.py` — reuse,
  do not copy); `Concert.tags` / `ConcertTag` / `TagSubscription` for the
  "because you follow X" attribution.
- Produces:
  - `@dataclass(frozen=True) SetupTile`: `concert: Concert`,
    `because: list[str]` (subscribed tag names that matched, group/franchise
    first), `kept: bool` (False iff an opt-out override exists),
    `venue: str | None` (same >1-venue → "Multiple" rule as
    `my_deadline_rows`), `starts_at_utc: datetime | None`,
    `next_round_label: str | None`, `next_round_anchor: Anchor | None`,
    `next_round_at_utc: datetime | None`.
  - `async def setup_prune_tiles(session, user_id, now=None) -> list[SetupTile]`
    — every tracked concert that is *upcoming* (a live day in the future,
    or any round anchor in the future), **including currently pruned ones**
    (they render unchecked; the tracked set alone would omit them, so this
    function unions the tag-implied set with existing overrides — verify
    how branch 4 exposes "pruned" rows and bind accordingly). Ordered
    soonest-next-moment first.
  - `def _round_asks_application(round_, outcome, now) -> bool` — the
    screen-2 predicate: no outcome recorded, `_round_has_opened`, at least
    one of opens/closes/results set, and `_result_moment` unset or future.
    Carry the branch-5 hook comment: "an open upgrade round will widen this
    to also ask about its qualifying closed round — branch 5."
  - `@dataclass(frozen=True) SetupAskRow`: `concert: Concert`,
    `round_: Round`, `status: str` (`"open"` iff `_round_is_open`, else
    `"awaiting"`), `moment_utc: datetime | None` (`closes_at_utc` when
    open, else `_result_moment`).
  - `async def setup_application_rows(session, user_id, now=None) -> list[SetupAskRow]`
    — over SURVIVING (non-pruned) tracked upcoming concerts only, rounds
    passing `is_round_cancelled` and the predicate. Ordered by
    `moment_utc`, None last.
  - `@dataclass(frozen=True) SetupTallies`: `tracking: int`, `applied: int`,
    `payment_due: int`, `next_deadline_utc: datetime | None`,
    `payment_concert: Concert | None` (soonest pending payment, for the
    reveal's narrative line).
  - `async def setup_tallies(session, user_id, now=None) -> SetupTallies`
    — per the spec's table, computed over surviving tracked upcoming
    concerts' live rounds.

- [ ] **Step 1: Bind the branch-4 API.** Read branch 4's merged service
  code. Record the real names for: the tracked-set function, the opt-out
  writer, the override clearer, and how an existing concert-level opt-out
  row is queried. If any is missing, STOP and flag (see the dependency
  note). Use the real names in every following step.
- [ ] **Step 2: Write the failing read-side tests** in
  `tests/test_setup_service.py`. Seed one user with subscriptions to a
  GROUP tag and a FRANCHISE tag, and concerts covering each case. Test
  cases:
  - `test_prune_tiles_cover_tracked_upcoming_concerts` — two tagged future
    concerts yield two tiles, each `kept=True`, `because` naming the
    subscribed tag.
  - `test_prune_tiles_exclude_past_concerts` — a concert whose only live
    day and every round anchor are in the past yields no tile.
  - `test_prune_tiles_include_pruned_concert_as_unkept` — write an opt-out
    via branch 4's function; the tile still appears with `kept=False`.
  - `test_prune_tiles_ordered_by_next_moment` — soonest deadline first.
  - `test_asks_open_round` — an open round with no outcome yields a row
    with `status == "open"` and `moment_utc == closes_at_utc`.
  - `test_asks_closed_round_awaiting_result` — closed, `results_at_utc`
    future → `status == "awaiting"`.
  - `test_does_not_ask_decided_round` — closed with `results_at_utc` past
    → absent (the middle-path rule).
  - `test_does_not_ask_unopened_round` — `opens_at_utc` future → absent.
  - `test_does_not_ask_round_with_outcome` — any existing `RoundOutcome`
    (including `NOT_APPLIED`) → absent.
  - `test_does_not_ask_rounds_of_pruned_concert` — prune the concert →
    its rounds absent.
  - `test_does_not_ask_cancelled_round` — every `applies_to` day cancelled
    → absent.
  - `test_tallies` — seed: 3 tracked upcoming concerts (one pruned → not
    counted), one `APPLIED`, one `WON` with a future payment deadline, and
    assert `tracking == 2`, `applied == 1`, `payment_due == 1`,
    `next_deadline_utc` equals the seeded soonest future anchor, and
    `payment_concert` is the WON round's concert.
- [ ] **Step 3: Run them, confirm they fail** for the right reason (the
  functions do not exist).
- [ ] **Step 4: Implement** the dataclasses and functions in
  `src/app/db/service.py`, in a new
  `# ── First-run capture flow (/setup) ──` section. Batch the queries the
  way `board_cards` does (one outcomes query, one rounds query — no
  per-concert N+1). Reuse the named helpers; write no second copy of any
  round-timing rule.
- [ ] **Step 5: Run the new tests, confirm pass.**
- [ ] **Step 6: Full suite + ruff, commit.**

```bash
git commit -m "Add the setup flow's read shapes and eligibility predicate"
```

---

### Task 2: Service layer — the two batch writes

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_setup_service.py`

**Interfaces:**
- Consumes: branch 4's opt-out writer + override clearer (Task 1's
  binding); `record_round_outcome` (existing); `setup_application_rows`
  (Task 1).
- Produces:
  - `async def apply_prune_selection(session, user_id, shown_ids: set[int], keep_ids: set[int], now=None) -> tuple[int, int]`
    — over `shown_ids ∩` the recomputed tracked-upcoming set (ids outside
    are ignored): unchecked and not already pruned → branch 4's opt-out
    writer; checked and currently pruned → branch 4's override clearer.
    Returns `(pruned, unpruned)` counts. Does NOT flush its own queue
    sync — that is inside branch 4's functions (invariant 2 at that
    boundary); assert it happens, don't reimplement it.
  - `async def record_setup_applications(session, user_id, round_ids: set[int], now=None) -> int`
    — recomputes the qualifying set via `setup_application_rows` and calls
    `record_round_outcome(session, user_id, rid, LotteryOutcome.APPLIED,
    now)` for each requested id inside it. Ids outside are ignored (this
    is what server-enforces the middle-path rule against forged ids).
    Returns the count recorded.

- [ ] **Step 1: Write the failing tests:**
  - `test_prune_selection_writes_optout_for_unchecked` — shown={A,B},
    keep={A} → B pruned (assert via branch 4's read surface, e.g. the
    tracked set no longer contains B and `setup_prune_tiles` shows B
    `kept=False`).
  - `test_prune_selection_clears_override_for_rechecked` — pre-prune A;
    shown={A}, keep={A} → A back to `kept=True`.
  - `test_prune_selection_ignores_untracked_ids` — an id for another
    user's world / a non-tracked concert in `shown` → no override row
    appears anywhere.
  - `test_prune_selection_is_idempotent` — running the same call twice
    returns `(1, 0)` then `(0, 0)`.
  - `test_prune_writes_resync_reminders` — a user with a rule on pruned
    concert B: after pruning, B's unsent `reminder_queue` rows are gone
    (this asserts branch 4's function does its invariant-2 job on our call
    path; if it fails, flag branch 4 — do not patch it here).
  - `test_setup_applications_records_applied` — qualifying open round
    checked → `RoundOutcome` `APPLIED` row exists, and
    `setup_application_rows` no longer returns it.
  - `test_setup_applications_skips_unchecked` — unchecked qualifying round
    → no row at all.
  - `test_setup_applications_ignores_forged_decided_round` — a closed,
    result-past round id in the request → no row.
  - `test_setup_applications_never_overwrites` — a round already `WON` in
    the request → outcome still `WON` (it was never qualifying, so it is
    ignored before `record_round_outcome` is even asked).
- [ ] **Step 2: Run, confirm they fail** (functions missing).
- [ ] **Step 3: Implement** both functions in the same service section.
- [ ] **Step 4: Run the new tests, confirm pass.**
- [ ] **Step 5: Full suite + ruff, commit.**

```bash
git commit -m "Add the setup flow's prune and application batch writes"
```

---

### Task 3: Routes, template and styles — screen 1

**Files:**
- Create: `src/app/web/routes/setup.py`
- Create: `src/app/web/templates/setup.html`
- Modify: `src/app/web/static/style.css`
- Modify: `src/app/web/app.py`
- Test: `tests/test_setup.py` (new — copy the client/login fixture shape
  from `tests/test_welcome.py`)

**Interfaces:**
- Consumes: `setup_prune_tiles`, `apply_prune_selection` (Tasks 1–2);
  `require_user`, `ensure_user`, `fmt_dual`.
- Produces: `GET /setup` (screen 1), `POST /setup/prune` (batch, 303 →
  `/setup/applications`). The route module exposes `templates = None`
  injected by `web/app.py`, same as every other route module. Screens are
  distinguished by a server-set `screen` context value
  (`"prune"` / `"applications"` / `"ready"`) — never by user input.

Route shape (thin shell — all logic is Task 1–2's functions):

```python
@router.get("/setup", response_class=HTMLResponse)
async def setup_prune(request, user=Depends(require_user), session=Depends(get_session)):
    db_user = await ensure_user(session, user.id, user.username)
    tiles = await setup_prune_tiles(session, user.id)
    because = sorted({name for t in tiles for name in t.because})
    return templates.TemplateResponse(request, "setup.html", {
        "user": user, "screen": "prune", "tiles": tiles,
        "because": because, "tz": db_user.timezone,
    })


@router.post("/setup/prune")
async def setup_prune_submit(
    user=Depends(require_user), session=Depends(get_session),
    keep: list[int] = Form([]), shown: list[int] = Form([]),
):
    await ensure_user(session, user.id, user.username)
    await apply_prune_selection(session, user.id, set(shown), set(keep))
    await session.commit()
    return RedirectResponse("/setup/applications", status_code=303)
```

Template, screen-1 branch: the concept's lede ("We found N upcoming
concerts for you" / "Because you follow …" — with the real tag names,
plain-text Jinja interpolation, no inline script), then the `.pick` grid.
Each tile is a `<label class="tile">` wrapping
`<input type="checkbox" name="keep" value="{{ t.concert.id }}" {% if t.kept %}checked{% endif %} hidden>`
plus the tick span, eyebrow (`t.because | join(" · ")`), title, venue +
`fmt_dual` date, and the nearest-round status line — all inside ONE
`<form method="post" action="/setup/prune">` that also emits
`<input type="hidden" name="shown" value="{{ t.concert.id }}">` per tile and
ends with the `.bar`: kept-count tally, a "Turn all off" button (static
inline `<script>` toggling checkboxes — it contains zero user-controlled
text, which is what invariant 7 actually forbids), and a Continue submit.
Empty state per the spec (link to `/discover`, Continue still proceeds).
CSS: port `.steps`/`.stepdot`, `.pick`, `.tile` (restated on
`.tile:has(input:checked)` / `.tile:not(:has(input:checked))`), `.bar`,
`.tally` from the concept into `style.css`. Steps header shows the flow's
own three dots (Your concerts · Applications · Ready) — the concept's
wizard-step dots are omitted because the flow is re-runnable from
Preferences, where they would be stale.

- [ ] **Step 1: Write the failing tests:**
  - `test_setup_requires_login` — 401.
  - `test_setup_renders_found_tiles` — two seeded tracked concerts: 200,
    both titles present, the subscribed tag name present, "We found 2"
    present, both `name="keep"` inputs checked.
  - `test_setup_renders_pruned_tile_unchecked` — pre-pruned concert's
    checkbox has no `checked`.
  - `test_setup_empty_state` — no tracked concerts: 200, `/discover` link
    present.
  - `test_prune_submit_writes_and_redirects` — POST keep/shown omitting
    one id → 303 to `/setup/applications`, and the omitted concert is
    pruned (assert through the service read, as in Task 2).
  - `test_prune_submit_ignores_forged_ids` — a bogus id in both fields →
    no error, no override.
- [ ] **Step 2: Run, confirm failure** (404 — routes missing).
- [ ] **Step 3: Implement** route file, template screen-1 branch, CSS.
- [ ] **Step 4: Register the router** in `web/app.py`: import
  `setup as setup_routes`, set `setup_routes.templates = templates`,
  `app.include_router(setup_routes.router)` next to the welcome router.
  Do not disturb the imports-before-concerts ordering.
- [ ] **Step 5: Run the new tests, confirm pass.**
- [ ] **Step 6: Full suite + ruff, commit.**

```bash
git commit -m "Add the setup flow's pruning screen"
```

---

### Task 4: Screens 2 and 3

**Files:**
- Modify: `src/app/web/routes/setup.py`, `src/app/web/templates/setup.html`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `setup_application_rows`, `record_setup_applications`,
  `setup_tallies` (Tasks 1–2).
- Produces: `GET /setup/applications` (screen 2),
  `POST /setup/applications` (303 → `/setup/ready`), `GET /setup/ready`
  (screen 3).

Screen 2 branch: lede per the concept ("Already applied to any of these?" /
"Only the rounds you could still be in — open now, or closed and waiting on
a result. A past round you lost does not change what happens next, so we do
not ask."). Tiles as unchecked checkboxes named `applied` valued
`row.round_.id`; tile shows eyebrow (artist), title, round label + status
("Still open" / "Awaiting result") with `fmt_dual(row.moment_utc, tz)` when
set. Bar tally: "Anything you skip, we will ask about by DM when its result
lands." Back link to `/setup`; Finish submits. Zero rows renders "Nothing
to ask right now" with Finish still present. POST calls
`record_setup_applications` then redirects. No `shown` field — unchecked
means "no write", so there is nothing to diff.

Screen 3 branch: the concept's `.reveal` — eyebrow "Setup complete", "Your
board is ready", the four `.tallies` (tracking / applied / payment due /
next deadline, the last as `fmt_dual` of `next_deadline_utc` or an em-dash
when None), the payment narrative line iff `payment_due > 0` (naming
`payment_concert.title`), and a "Go to my board" link to `/`.

- [ ] **Step 1: Write the failing tests:**
  - `test_applications_requires_login`, `test_ready_requires_login` — 401.
  - `test_applications_renders_only_qualifying_rounds` — seed one open
    round, one awaiting-result round, one decided round, one round on a
    pruned concert: 200, first two labels present with "Still open" /
    "Awaiting result", last two absent.
  - `test_applications_empty_state` — no qualifying rounds: 200, "Nothing
    to ask" present, Finish present.
  - `test_applications_submit_records_and_redirects` — POST one round id →
    303 to `/setup/ready`, `RoundOutcome` `APPLIED` exists for it, none for
    the unchecked one.
  - `test_applications_submit_ignores_decided_round_id` — forged id → no
    row, still 303.
  - `test_ready_renders_tallies` — the Task 1 tallies scenario over HTTP:
    200, the numbers 2/1/1 present in the tallies block, the payment
    concert's title in the narrative line.
  - `test_ready_without_payment_due_has_no_narrative` — narrative absent.
  - `test_rerun_reflects_prior_choices` — after a full pass (prune one,
    apply one): `GET /setup` shows the pruned tile unchecked;
    `GET /setup/applications` no longer lists the applied round; a second
    identical POST to both endpoints changes no rows.
- [ ] **Step 2: Run, confirm failure.**
- [ ] **Step 3: Implement** the three routes and two template branches.
- [ ] **Step 4: Run the new tests, confirm pass.**
- [ ] **Step 5: Full suite + ruff, commit.**

```bash
git commit -m "Add the setup flow's applications pass and reveal"
```

---

### Task 5: The wizard handoff

**Files:**
- Modify: `src/app/web/routes/welcome.py` (the `advance` route only)
- Test: `tests/test_welcome.py`

**Interfaces:**
- Consumes: `TOTAL_STEPS` (existing, same file). Nothing else in
  `welcome.py`, `auth.py`, or `skip_all` changes. `GET /welcome`'s
  done-redirect stays `/` — `test_welcome_redirects_to_index_once_done`
  must keep passing untouched.

In `advance`, after the increment: redirect to `"/setup"` when
`db_user.onboarding_step >= TOTAL_STEPS`, else `"/welcome"` as today. That
is the whole change — the reveal becomes the wizard's payoff, while
`skip-all` ("skip setup entirely") still lands on `/` and skips the capture
flow too.

- [ ] **Step 1: Write the failing tests** in `tests/test_welcome.py`:
  - `test_final_advance_hands_off_to_setup` — advance 5 times from a fresh
    login; the 5th response's location is `/setup`.
  - `test_earlier_advances_stay_on_welcome` — the 1st advance's location
    is `/welcome` (guards against redirecting every advance).
  - `test_skip_all_still_lands_on_index` — location `/` (exists already as
    `test_skip_all_jumps_straight_to_done`; extend it or assert alongside —
    do not weaken it).
- [ ] **Step 2: Run, confirm the first fails** (location is `/welcome`
  today) and the others pass trivially.
- [ ] **Step 3: Implement the two-line change.**
- [ ] **Step 4: Run `tests/test_welcome.py` fully, confirm pass** —
  including the untouched existing tests.
- [ ] **Step 5: Full suite + ruff, commit.**

```bash
git commit -m "Hand the wizard off to the capture flow at /setup"
```

---

### Task 6: Docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `WISHLIST.md`

- [ ] **Step 1: Update CLAUDE.md.** Run `uv run pytest -q` and read the
  real passing count from the output (do not hard-code this plan's
  arithmetic). Update the intro's test count and append the capture flow
  to the shipped-features list ("and a post-wizard first-run capture flow —
  prune tag-implied concerts, record existing applications, board reveal —
  at /setup, re-runnable from Preferences"). If the "Layout" section
  documents `routes/`, add one line for `routes/setup.py`.
- [ ] **Step 2: Update WISHLIST.md.** Move the onboarding-refactor entry
  (however the UI/UX-refactor branches are tracked there) to Shipped with
  today's date; do the full re-rank pass over remaining entries the file's
  own rules require; note that this closes the six-branch UI/UX refactor.
- [ ] **Step 3: Commit.**

```bash
git commit -m "Update CLAUDE.md and WISHLIST.md for the first-run capture flow"
```

---

## Verification

Beyond the suite: fresh login → wizard → confirm the final Continue lands on
`/setup`; prune one tile, Continue; check one application, Finish; confirm
the reveal's numbers, then "Go to my board" and confirm Home agrees (the
pruned concert absent, the applied round in the Applied column). From
Preferences, follow branch 4's "Run first-time setup again" link and confirm
the flow re-renders with those choices as the starting point.


## Resolved decisions (do not re-litigate)

- **Un-prune during setup (Resolved 2026-07-19 with the owner).** Toggling a pruned concert tile back ON CLEARS the opt-out
  override (deletes the row, back to tag-derived default) -- it does NOT write an explicit
  `subscribed` row. Toggling on then off during setup therefore leaves no trace. Consequence to
  preserve: a re-enabled concert follows its tag, so if the user later unfollows that tag the
  concert leaves too. This resolves open question 1 in this plan -- branch 4 must expose a
  "clear override" writer, and Task 1's bind-and-verify step confirms its real name.
