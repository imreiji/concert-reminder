# Per-Leg Outcome Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record lottery wins per performance day, stop asking users about rounds their standing already covers, and replace the concert page's "all legs" section with per-leg rendering — per spec `docs/superpowers/specs/2026-07-26-per-leg-outcomes-design.md`.

**Architecture:** `RoundOutcome` stays the per-(user, round) campaign state; a new `round_outcome_days` table records per-day WON/LOST with a no-rows-means-all convention (zero backfill). One shared secured-days derivation feeds both the reminder planner (`_apply_outcome_suppression`) and the new read-side "covered" gates. Discord capture is progressive (each press edits the same message down to the unresolved remainder); the web mirrors it via the shared `_capture_actions.html` macro plus a catch-up dialog on the concert page.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, Alembic (batch mode), FastAPI + Jinja2 + htmx, discord.py DynamicItems, babel gettext (ja/zh catalogues).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Run test suites in the FOREGROUND (background runs stall the loop).
- All datetimes aware-UTC in the DB (`UTCDateTime` raises on naive); JST enters only via forms/`jst_to_utc`.
- Every new user-visible string is `_()`-wrapped (or `N_()`-marked in dicts / DynamicItem call sites) and gets a hand-filled msgstr in BOTH `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po` — `tests/test_i18n_catalogues.py` fails otherwise, and fuzzy entries count as untranslated. Placeholders (`{day}`, `{n}`) must survive translation byte-identically.
- Outcome writes funnel EXCLUSIVELY through `record_round_outcome` / the new `record_round_day_result` / `record_remaining_days_lost`; leg opt-outs through `set_leg_opt_out`. No second writer (invariant 2).
- Templates: `| tojson` never `| safe` for user-controlled data; user text reaches JS via `data-*` + `dataset`, never inline `on*` interpolation; never `data-name` (collides with `filterChips()`); sentence case; radius 3px.
- Migrations: after autogenerate, replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and delete the `import app.db.models` line. This migration is purely additive (no `drop_constraint`), so the legacy-anonymous-constraint fixture is not required.
- After each task's steps: commit on branch `per-leg-outcomes` with the shown message plus the standard `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.

---

### Task 1: `LegResult` enum, `RoundOutcomeDay` model, migration

**Files:**
- Modify: `src/app/domain/types.py` (after `LotteryOutcome`)
- Modify: `src/app/db/models.py` (immediately after `class RoundOutcome`, models.py:445)
- Create: `alembic/versions/<autogen>_round_outcome_days.py`
- Test: `tests/test_schema.py` (append), `tests/test_migration_round_outcome_days.py` (new)

**Interfaces:**
- Consumes: existing `Base`, `UTCDateTime`, `_now`, `LotteryOutcome`.
- Produces: `app.domain.types.LegResult` (StrEnum: `WON = "won"`, `LOST = "lost"`); `app.db.models.RoundOutcomeDay` with columns `id, user_id, round_id, day_id, result, updated_at` and unique index `uq_round_outcome_day (user_id, round_id, day_id)`. Later tasks import both.

- [ ] **Step 1: Write the failing schema test** (append to `tests/test_schema.py`, using that file's existing engine fixture pattern)

```python
async def test_round_outcome_day_unique_per_user_round_day(session):
    from sqlalchemy.exc import IntegrityError

    from app.db.models import RoundOutcomeDay
    from app.domain.types import LegResult

    user = await make_user(session, 1)          # this file's existing helpers
    concert = await make_concert(session)
    day = await make_day(session, concert)
    round_ = await make_round(session, concert)
    session.add(RoundOutcomeDay(
        user_id=user.discord_id, round_id=round_.id, day_id=day.id,
        result=LegResult.WON,
    ))
    await session.flush()
    session.add(RoundOutcomeDay(
        user_id=user.discord_id, round_id=round_.id, day_id=day.id,
        result=LegResult.LOST,
    ))
    with pytest.raises(IntegrityError):
        await session.flush()
```

(If `tests/test_schema.py` has no `make_*` helpers, inline the three-object setup the way that file's neighboring tests do — copy its exact construction style.)

- [ ] **Step 2: Run it** — `uv run pytest tests/test_schema.py -q` — expect FAIL (`ImportError: LegResult`).

- [ ] **Step 3: Implement.** In `domain/types.py` after `LotteryOutcome`:

```python
class LegResult(enum.StrEnum):
    """Per-day resolution of a multi-leg round's lottery. Deliberately NOT
    LotteryOutcome: a day only ever resolves won-or-lost; applied and paid
    stay round-level concepts (see the 2026-07-26 per-leg-outcomes spec)."""

    WON = "won"
    LOST = "lost"
```

In `models.py` directly after `RoundOutcome` (import `LegResult` alongside the other `app.domain.types` imports at the top):

```python
class RoundOutcomeDay(Base):
    """Per-day resolution of one user's round outcome. No-rows-means-all
    convention (mirrors applies_to / round_qualifiers): a round outcome of
    WON with zero rows here means every covered day was won; rows exist only
    when resolution is explicit/partial. "Not going" is NOT stored here --
    that is LegOptOut's job."""

    __tablename__ = "round_outcome_days"
    __table_args__ = (
        Index("uq_round_outcome_day", "user_id", "round_id", "day_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    day_id: Mapped[int] = mapped_column(ForeignKey("concert_days.id", ondelete="CASCADE"))
    result: Mapped[LegResult] = mapped_column(
        Enum(LegResult, values_callable=lambda e: [m.value for m in e])
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)
```

- [ ] **Step 4: Generate + hand-edit the migration.** `uv run alembic revision --autogenerate -m "round_outcome_days"`. In the revision: replace any `app.db.models.UTCDateTime()` with `sa.DateTime()`, remove the models import, confirm the unique index and three FKs (CASCADE) are present.

- [ ] **Step 5: Migration test.** Create `tests/test_migration_round_outcome_days.py` copying the structure of `tests/test_migration_round_outcomes.py` (stamp previous head, `alembic upgrade`, assert table + index exist via `PRAGMA index_list('round_outcome_days')`, insert a row, `DELETE FROM users` under `PRAGMA foreign_keys=ON`, assert the row cascaded away).

- [ ] **Step 6: Run** `uv run alembic upgrade head`, then `uv run pytest tests/test_schema.py tests/test_migration_round_outcome_days.py -q` — expect PASS. Then full `uv run pytest -q` + `uv run ruff check .`.

- [ ] **Step 7: Commit** — `feat: RoundOutcomeDay table + LegResult enum (per-leg outcomes, task 1)`

---

### Task 2: shared secured-days / covered-rounds derivation; suppression uses it

**Files:**
- Modify: `src/app/db/service.py` (new helpers above `_apply_outcome_suppression` at service.py:192; refactor its pass-2 body, service.py:274-309)
- Test: `tests/test_lottery_outcomes.py` (append), existing suppression tests stay green

**Interfaces:**
- Consumes: `RoundOutcomeDay`, `LegResult` (Task 1).
- Produces (exact signatures, used by Tasks 3-6):

```python
def _covered_day_ids(round_: Round, all_day_ids: set[int]) -> set[int]
    # applies_to ∩ existing days, or all_day_ids when applies_to empty/None.

async def secured_day_ids_by_round(
    session: AsyncSession, user_id: int, concert_id: int
) -> dict[int, set[int]]
    # round_id -> EXACT secured day ids, for rounds whose outcome is WON/PAID:
    # the round's WON RoundOutcomeDay rows if any exist, else every day it
    # covers (no-rows-means-all).

async def covered_round_ids(
    session: AsyncSession, user_id: int, concert_id: int
) -> set[int]
    # Non-UPGRADE round ids whose every covered day is secured by some OTHER
    # round -- the single "stop asking about this" definition shared by the
    # planner and every read surface.
```

- [ ] **Step 1: Failing tests** (append to `tests/test_lottery_outcomes.py`, reusing its fixtures/builders):

```python
async def test_secured_days_no_rows_means_all(session):
    # 2-day concert, round covering both, outcome WON, zero day rows
    ...build concert/two days/round(applies_to=[d1.id, d2.id])...
    await record_round_outcome(session, UID, round_.id, LotteryOutcome.WON)
    secured = await secured_day_ids_by_round(session, UID, concert.id)
    assert secured[round_.id] == {d1.id, d2.id}


async def test_secured_days_partial_win_is_exact(session):
    ...same setup...
    await record_round_outcome(session, UID, round_.id, LotteryOutcome.WON)
    session.add(RoundOutcomeDay(user_id=UID, round_id=round_.id,
                                day_id=d1.id, result=LegResult.WON))
    await session.flush()
    secured = await secured_day_ids_by_round(session, UID, concert.id)
    assert secured[round_.id] == {d1.id}


async def test_covered_round_ids_partial_win_does_not_cover_other_day(session):
    # round A covers d1+d2, partially won (d1 only); round B covers d2 only.
    # B must NOT be covered -- d2 was not actually secured.
    ...
    assert await covered_round_ids(session, UID, concert.id) == set()


async def test_covered_round_ids_full_win_covers_single_day_round(session):
    # round A covers d1+d2 and is WON with no day rows; round B covers d1.
    ...
    assert round_b.id in await covered_round_ids(session, UID, concert.id)
```

- [ ] **Step 2: Run** — expect FAIL (`ImportError`).

- [ ] **Step 3: Implement the three helpers** in the `# ── Queue sync ──` section. `secured_day_ids_by_round`: one query for the concert's rounds, one for the user's outcomes, one for the user's `RoundOutcomeDay` rows over those rounds, one for the concert's day ids — never per-round queries. `covered_round_ids`: build `secured_by = await secured_day_ids_by_round(...)`, then for each non-UPGRADE round with a non-empty covered set, covered iff `covered <= union(secured_by[other] for other != round.id)` — the exact shape of today's pass 2 (service.py:302-309), exact instead of over-approximated.

- [ ] **Step 4: Refactor `_apply_outcome_suppression`** pass 2: replace the inline `secured_by` block (service.py:274-277) with `secured_by = await secured_day_ids_by_round(session, user_id, concert_id)` and keep the per-round exclusion loop unchanged. Delete nothing else — passes 1, 3, 4 are untouched.

- [ ] **Step 5: Run the whole affected surface** — `uv run pytest tests/test_lottery_outcomes.py tests/test_upgrade_suppression.py tests/test_leg_opt_out_suppression.py tests/test_service.py -q` — all green (behavior identical when no day rows exist), new tests PASS. Full suite + ruff.

- [ ] **Step 6: Commit** — `feat: exact secured-days derivation shared by planner (task 2)`

---

### Task 3: per-day write path — `record_round_day_result`, `record_remaining_days_lost`, per-day auto-arm

**Files:**
- Modify: `src/app/db/service.py` (after `record_round_outcome`, service.py:374; extend `_next_round_for_leg` at :377 and `_auto_arm_next_round` at :409)
- Test: `tests/test_lottery_outcomes.py` (append)

**Interfaces:**
- Consumes: Task 2 helpers, existing `record_round_outcome`, `set_leg_opt_out`, `reinstate_user_rules`, `get_default_preset`, `sync_rule`.
- Produces (exact signatures, used by Tasks 6 and 8):

```python
async def unresolved_day_ids(
    session: AsyncSession, user_id: int, round_: Round
) -> list[int]
    # Covered, non-cancelled days with neither a RoundOutcomeDay row nor a
    # LegOptOut row for this user, ordered by starts_at_utc then id.

async def record_round_day_result(
    session: AsyncSession, user_id: int, round_id: int, day_id: int,
    result: LegResult, now: datetime | None = None,
) -> None

async def record_remaining_days_lost(
    session: AsyncSession, user_id: int, round_id: int,
    now: datetime | None = None,
) -> None
```

`_next_round_for_leg(session, lost_round, day_ids: set[int] | None = None)` and `_auto_arm_next_round(session, user_id, lost_round, now=None, day_ids=None)` gain an optional narrowing param: when `day_ids` is given it replaces `lost_legs` in the overlap check; existing callers pass nothing and behave identically.

**Behavioral contract for `record_round_day_result`:**
1. Missing round, or `day_id` not in `_covered_day_ids` → silent no-op (forged/stale ids only affect nothing, same rule as `/setup`).
2. Upsert the `RoundOutcomeDay` row (update `result` in place if it exists).
3. `result is WON` → `record_round_outcome(session, user_id, round_id, LotteryOutcome.WON, now)` (flips APPLIED/None → WON, re-syncs rules — existing semantics do the rest).
4. `result is LOST` → if no WON rows exist for this (user, round) AND every covered day is now resolved (day row) or leg-opted-out → `record_round_outcome(..., LotteryOutcome.LOST, ...)` (which re-syncs and whole-round auto-arms). Otherwise (partial win in progress): `reinstate_user_rules(session, user_id, round_.concert_id, now)` then `_auto_arm_next_round(session, user_id, round_, now, day_ids={day_id})`.

**Behavioral contract for `record_remaining_days_lost`:** write LOST rows for every id in `unresolved_day_ids`, then apply rule 4's terminal check once (LOST if no WON rows, else reinstate + arm for each newly lost day's ids as one `day_ids` set).

- [ ] **Step 1: Failing tests** — cover at minimum:

```python
async def test_won_day_flips_round_to_won(session): ...
async def test_lost_day_partial_keeps_round_won_and_arms_next_round_for_that_day(session):
    # rounds: A (d1+d2, results passed), B (d2 only, opens later).
    # Won d1 then lost d2 -> outcome stays WON, a ReminderRule(OPENS, B) exists.
async def test_all_days_lost_flips_round_to_lost(session): ...
async def test_forged_day_id_is_noop(session): ...
async def test_not_going_day_counts_as_resolved_for_lost_terminal(session):
    # d2 leg-opted-out + d1 LOST row -> round outcome LOST.
async def test_remaining_days_lost_writes_rows_and_settles(session): ...
async def test_unresolved_day_ids_excludes_cancelled_resolved_and_opted_out(session): ...
```

- [ ] **Step 2: Run** — expect FAIL. **Step 3: Implement** per the contracts. **Step 4: Run** the file + full suite + ruff — PASS.

- [ ] **Step 5: Commit** — `feat: per-day outcome write path with per-day auto-arm (task 3)`

---

### Task 4: read-side covered gates + per-leg concert rows (service layer)

**Files:**
- Modify: `src/app/db/service.py` — `DeadlineRow` (:1303), `my_deadline_rows` (:1434), `RoundRow`/`concert_round_rows` (:1905/:1963), `setup_application_rows` (:1760)
- Test: `tests/test_concert_rows.py`, `tests/test_home.py`, `tests/test_setup_service.py` (append/adjust)

**Interfaces:**
- Consumes: `covered_round_ids`, `secured_day_ids_by_round`, `unresolved_day_ids` (Tasks 2-3).
- Produces (Tasks 5-6 render these):
  - `DeadlineRow` gains `capture_days: tuple[tuple[int, str], ...] = ()` (unresolved (day_id, viewer-locale label) pairs, non-empty ONLY when the row's round covers ≥2 live days and result reporting is live) and `any_day_won: bool = False`.
  - `RoundRow` gains `covered: bool = False`, `leg_result: LegResult | None = None` (this leg's resolution for the viewer), `capture_days` and `any_day_won` with the same meaning.
  - `concert_round_rows` return type UNCHANGED (`tuple[list[LegRounds], list[RoundRow]]`) but the second list is now non-empty ONLY for a concert with zero days (the fallback group); every round otherwise appears under EACH live leg it applies to — including all-legs rounds — with a per-leg `RoundRow` instance (`leg_result` differs per leg). Cancelled legs keep their groups.
  - New tiny helper: `def _can_resolve_days(round_: Round, outcome: LotteryOutcome | None, now: datetime, unresolved: list[int]) -> bool` — True iff `len(covered live days) >= 2`, `outcome in (LotteryOutcome.APPLIED, LotteryOutcome.WON)`, `_result_moment(round_)` unset-or-passed, and `unresolved` non-empty.

**Rules:**
- `my_deadline_rows`: after the existing eligibility filtering, compute `covered = await covered_round_ids(session, user_id, cid)` per distinct concert id among the rows (≤10 rows, loop is fine) and `continue` past any row whose round id is covered — Coming up and the DM planner now agree.
- `concert_round_rows`: compute `covered` once for the concert; a covered round still renders (quiet state), it is NOT dropped there. Populate `leg_result` from the viewer's `RoundOutcomeDay` rows (one query, keyed `(round_id, day_id)`), falling back for a WON/LOST round with no day rows to `LegResult.WON`/`LegResult.LOST` on every covered leg (no-rows-means-all made visible). `capture_days` labels resolve via `loc_field(day, "label", locale)` with the request locale already in scope (:1985).
- `setup_application_rows`: skip rounds in `covered_round_ids` for that concert (add alongside the `_round_asks_application` check at :1787).

- [ ] **Step 1: Failing tests**, including at minimum:

```python
async def test_coming_up_drops_secured_elsewhere_round(session): ...
async def test_concert_rows_all_legs_round_appears_under_each_live_leg(session): ...
async def test_concert_rows_fallback_group_only_when_no_days(session): ...
async def test_concert_rows_leg_result_reflects_partial_win(session): ...
async def test_concert_rows_covered_round_renders_quiet(session):
    # covered=True, can_capture False via template gate -- assert the flag.
async def test_setup_skips_covered_round(session): ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** run those three files, then FULL suite — existing `test_concert_rows.py` all-legs assertions will need updating to the new grouping (update them to assert the new contract, not to preserve the old one). Ruff.

- [ ] **Step 5: Commit** — `feat: covered gates on read side; rounds render per leg (task 4)`

---

### Task 5: templates — covered state, per-day web buttons, all-legs section removal

**Files:**
- Modify: `src/app/web/templates/_capture_actions.html`, `_round_rows.html`, `_deadline_rows.html` (only if it passes rows straight through — verify the macro call sites still typecheck against the new fields)
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `.../zh/.../messages.po`
- Test: `tests/test_concert_page.py`, `tests/test_home.py` (render assertions)

**Interfaces:**
- Consumes: Task 4's row fields (`covered`, `capture_days`, `any_day_won`, `leg_result`).
- Produces: the macro contract Task 6's dialog reuses — forms POST `result` + `day_id` to `/rounds/{round_id}/day-result` (route lands in Task 6; template and route are committed together via Task 6's test run — see Step 5).

- [ ] **Step 1:** Extend `_capture_actions.html`. New first branch and a per-day branch replacing the flat won/lost pair when `row.capture_days` is non-empty:

```jinja
{% if row.covered %}
<span class="done ok">{{ _("Covered — you already hold this day") }}</span>
{% elif not row.can_capture %}
...existing "Not open yet" branch unchanged...
{% elif row.outcome is none %}
...existing applied/not-applying branch unchanged...
{% elif row.capture_days %}
{# Multi-leg result capture: one form per unresolved day + the shortcuts.
   Same write path as Discord's progressive buttons (invariant 2). #}
{% if not row.any_day_won %}
<form hx-post="/rounds/{{ round_id }}/outcome" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/outcome">
  <input type="hidden" name="outcome" value="won">
  <button class="act yes" type="submit">{{ _("Won (all)") }}</button>
</form>
{% endif %}
{% for day_id, day_label in row.capture_days %}
<form hx-post="/rounds/{{ round_id }}/day-result" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/day-result">
  <input type="hidden" name="day_id" value="{{ day_id }}">
  <input type="hidden" name="result" value="won">
  <button class="act yes" type="submit">{{ _("Won — {day}").format(day=day_label) }}</button>
</form>
<form hx-post="/rounds/{{ round_id }}/day-result" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/day-result">
  <input type="hidden" name="day_id" value="{{ day_id }}">
  <input type="hidden" name="result" value="lost">
  <button class="act no" type="submit">{{ _("Lost — {day}").format(day=day_label) }}</button>
</form>
<form hx-post="/rounds/{{ round_id }}/day-result" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/day-result">
  <input type="hidden" name="day_id" value="{{ day_id }}">
  <input type="hidden" name="result" value="skip">
  <button class="act no" type="submit">{{ _("Not going — {day}").format(day=day_label) }}</button>
</form>
{% endfor %}
{% if row.any_day_won %}
<form hx-post="/rounds/{{ round_id }}/day-result" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/day-result">
  <input type="hidden" name="result" value="lost_rest">
  <button class="act no" type="submit">{{ _("Lost the rest") }}</button>
</form>
{% else %}
<form hx-post="/rounds/{{ round_id }}/outcome" hx-target="{{ target }}" hx-swap="outerHTML"
      method="post" action="/rounds/{{ round_id }}/outcome">
  <input type="hidden" name="outcome" value="lost">
  <button class="act no" type="submit">{{ _("Lost (all)") }}</button>
</form>
{% endif %}
{% elif row.can_report_result %}
...existing single-leg I won / I lost branch unchanged...
```

The existing `row.outcome.value == "won"` Paid branch stays; a WON round with unresolved `capture_days` hits the per-day branch first (order above), so Paid appears once days settle.

- [ ] **Step 2:** `_round_rows.html`: delete the all-legs section markup; render the second returned list only under a `{% if not legs %}` fallback heading (`_("Rounds")`). Keep every `data-*` hook and the kebab/status classes as they are.

- [ ] **Step 3:** Catalogue update: `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .` → `pybabel update` for ja and zh → hand-fill every new msgstr (the strings introduced in this task: "Covered — you already hold this day", "Won (all)", "Lost (all)", "Won — {day}", "Lost — {day}", "Not going — {day}", "Lost the rest", "Rounds" if new) → delete `messages.pot`.

- [ ] **Step 4:** Render tests: assert a covered round's page shows the Covered text and no forms; a partial-win round shows `Lost — Day 2` and `Lost the rest`; the all-legs section is gone (round label appears once per leg group).

- [ ] **Step 5:** Run `uv run pytest tests/test_concert_page.py tests/test_home.py tests/test_i18n_catalogues.py -q` — the day-result forms 404 until Task 6, so any test that PRESSES them belongs in Task 6; this task's tests only assert markup. Full suite + ruff. **Commit** — `feat: capture UI renders covered state and per-day results (task 5)`

---

### Task 6: web routes — `/rounds/{id}/day-result`, catch-up dialog on the concert page

**Files:**
- Modify: `src/app/web/routes/outcomes.py` (extract the fragment-response tail of `record_outcome` (:114-184) into `async def _outcome_response(request, session, user, outcome_value: str)`; add the new route)
- Modify: `src/app/web/routes/concerts.py` (`concert_detail` at :926 adds pending-capture context), `src/app/web/templates/concert_detail.html`
- Create: `src/app/web/templates/_result_capture_dialog.html`
- Modify: both `messages.po`
- Test: `tests/test_outcome_routes.py`, `tests/test_concert_page.py`

**Interfaces:**
- Consumes: `record_round_day_result`, `record_remaining_days_lost`, `set_leg_opt_out`, `unresolved_day_ids`, `LegResult` (Task 3); Task 5's form contract.
- Produces: `POST /rounds/{round_id}/day-result` accepting form fields `result: str` in `{"won", "lost", "skip", "lost_rest"}` and `day_id: int | None` (required unless `lost_rest`; anything else 422). htmx answers reuse `_outcome_response` verbatim (same fragments, same `HX-Trigger` toast keyed `"outcome": "won"|"lost"`); non-htmx 303s back via `_concert_event_id` exactly as `record_outcome` does. Also `pending_capture: RoundRow | None` in `concert_detail`'s template context — the soonest round with `capture_days` non-empty or (single-leg) `can_report_result` and outcome APPLIED.

- [ ] **Step 1: Failing route tests** (this file's existing login/client fixtures):

```python
async def test_day_result_won_records_day_and_flips_round(client, db): ...
async def test_day_result_skip_writes_leg_opt_out(client, db): ...
async def test_day_result_lost_rest_settles_round(client, db): ...
async def test_day_result_bad_result_value_422(client, db): ...
async def test_day_result_forged_day_id_is_committed_noop_not_500(client, db): ...
async def test_day_result_htmx_from_concert_page_returns_round_rows_plus_oob_strip(client, db): ...
```

- [ ] **Step 2: Implement the route** — same shape as `record_outcome`: 404 on missing round, `ensure_user`, dispatch on `result` to the three service writers, commit, then `_outcome_response`. `skip` maps to `set_leg_opt_out(session, user.id, day_id, True)` followed by `reinstate_user_rules` is NOT needed (leg opt-out is read-side; match `set_leg_opt_out`'s existing contract).

- [ ] **Step 3: Dialog.** `_result_capture_dialog.html`: a `<dialog class="prune" id="resultDlg">` titled `_("Results are out — how did it go?")`, naming the concert + round label (`loc` filter), body = `{{ capture_actions(pending_capture, pending_capture.round_.id, "#round-rows") }}` reusing the Task 5 macro, plain-POST fallback intact. Include from `concert_detail.html` under `{% if pending_capture %}`, plus the auto-open snippet (no user text interpolated — invariant 7):

```html
<script>document.getElementById("resultDlg")?.showModal?.();</script>
```

- [ ] **Step 4:** `concert_detail` computes `pending_capture` from the rows `concert_rounds_context` already builds (no new queries): first row by result moment where `capture_days` or (`can_report_result` and not `covered`). Render test: page with a pending multi-leg round contains `id="resultDlg"`; page with everything resolved does not.

- [ ] **Step 5:** Catalogue for the dialog strings; full suite + ruff. **Commit** — `feat: day-result route + concert-page catch-up dialog (task 6)`

---

### Task 7: `DueReminder.covered_days` for the scheduler path

**Files:**
- Modify: `src/app/db/service.py` — `DueReminder` (:678), `due_reminders` (:704)
- Test: `tests/test_service.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `DueReminder.covered_days: tuple[tuple[int, str], ...] = ()` — for a RESULTS-anchor row whose round covers ≥2 live days: (day_id, label) pairs ordered by starts_at, labels resolved via `loc_field(day, "label", user.language)` (recipient language, NOT `get_locale()` — this is the scheduler path). Empty for every other row. Task 8 consumes it.

- [ ] **Step 1: Failing test** — a queued RESULTS reminder on a 2-leg round yields `covered_days == ((d1.id, "Day 1"), (d2.id, "Day 2"))`; a CLOSES reminder on the same round yields `()`; a ja-language recipient gets `label` (ja source) regardless of ambient locale.
- [ ] **Step 2-4:** Implement inside `due_reminders`' existing batched loads (days are already fetched there — extend, don't add per-row queries), run, full suite + ruff.
- [ ] **Step 5: Commit** — `feat: DueReminder carries covered days for results capture (task 7)`

---

### Task 8: Discord progressive capture buttons

**Files:**
- Modify: `src/app/bot/views.py` (8 new DynamicItems + two helpers; extend `DYNAMIC_ITEMS` at :403 and the module docstring's namespace table at :10-21)
- Modify: `src/app/bot/messages.py` (`build_reminder_message` RESULTS branch, :196-198)
- Modify: both `messages.po`
- Test: `tests/test_bot_views.py`, `tests/test_messages.py`

**Interfaces:**
- Consumes: `record_round_day_result`, `record_remaining_days_lost`, `record_round_outcome`, `set_leg_opt_out`, `unresolved_day_ids`, `LegResult` (Task 3), `DueReminder.covered_days` (Task 7).
- Produces: custom_id namespace additions —

```
dk:wonall:{round_id}                dk:lostall:{round_id}
dk:wonday:{round_id}:{day_id}       dk:lostday:{round_id}:{day_id}
dk:skipday:{round_id}:{day_id}      dk:lostrest:{round_id}
dk:paidnow:{round_id}               dk:paylater:{round_id}
```

Two-id templates use `template=r"dk:wonday:(?P<rid>\d+):(?P<did>\d+)"` etc. Constructors take `(round_id, day_id, label: str | None = None)`; `from_custom_id` passes `label=None` and a generic fallback label — the label only matters when WE build a view, which always happens with DB access in hand.

- [ ] **Step 1:** Two view-builder helpers in `views.py` (async, take an open session so labels/locale resolve at build time):

```python
async def _result_state(session, user_id: int, round_):
    """(unresolved [(day_id, label)...], any_won: bool, outcome) snapshot --
    every progressive render derives from THIS, never from message labels."""

def build_result_followup_view(round_id, unresolved, any_won) -> discord.ui.View
    # one row per unresolved day: WonDay/LostDay/SkipDay; then LostRest if
    # any_won else LostAllButton.

def build_payment_view(round_id) -> discord.ui.View
    # PaidNowButton + PayLaterButton.
```

- [ ] **Step 2: Callbacks** — every one follows the file's existing shape (open session → service write → `_apply_locale` → commit) then, instead of `send_message`, **edits the message**: recompute `_result_state`; if unresolved remain → `interaction.response.edit_message(content=_("{day} recorded — and the other days?").format(day=...), view=build_result_followup_view(...))`; elif any_won and outcome is not PAID → `edit_message(content=_("All resolved — pay by card already?"), view=build_payment_view(...))`; else final text (`_("All recorded — congrats!")` / `_("Sorry to hear it — I'll let you know when the next round opens if there is one.")`). `PaidNowButton` → `record_round_outcome(..., PAID)` → `edit_message(content=_("Marked as paid — all set!"), view=None)`. `PayLaterButton` → no write → `edit_message(content=_("OK — I'll remind you before the payment deadline."), view=None)`. `WonAllButton`/`LostAllButton` → `record_round_outcome(WON/LOST)` then the same terminal dispatch. A press on an already-resolved state (stale message) re-renders the current truth instead of double-writing — which the service no-ops guarantee anyway.

- [ ] **Step 3:** `build_reminder_message`: in the RESULTS branch (messages.py:196-198), when `item.covered_days` has ≥2 entries build `WonAllButton(item.round_id)`, one `WonDayButton(item.round_id, did, label=_("Won — {day}").format(day=lbl))` per pair, `LostAllButton(item.round_id)`; otherwise keep `WonButton`/`LostButton` exactly as now. Register all 8 new classes in `DYNAMIC_ITEMS`.

- [ ] **Step 4: Tests.** Extend the fake-interaction pattern with an `edit_message` recorder:

```python
class FakeResponse:
    def __init__(self):
        self.sent = None
        self.edited = None
    async def send_message(self, *a, **k): self.sent = {"args": a, "kwargs": k}
    async def edit_message(self, *a, **k): self.edited = {"args": a, "kwargs": k}
```

Cover: WonDay press on a 3-leg round edits to a view containing the two other days' buttons; LostRest settles and edits to the payment view; PaidNow records PAID; WonAll on a stale already-PAID round re-renders without error; `build_reminder_message` with `covered_days` of length 2 emits `dk:wonall`/`dk:wonday` custom_ids and with length 0 emits `dk:won`.

- [ ] **Step 5:** Catalogue for every new label/content msgid, full suite + ruff. **Commit** — `feat: progressive per-day result capture in Discord DMs (task 8)`

---

### Task 9: closing sweep

**Files:** none new — verification and docs only.

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) and `uv run ruff check .` — both clean.
- [ ] **Step 2:** Manual smoke in web-only dev mode (`uv run python -m app.main` with empty `DISCORD_TOKEN`): create a 2-leg concert with one all-legs round, record APPLIED, pass the results moment (edit the round's times), confirm the concert-page dialog appears, resolve Won Day 1 → Lost Day 2, confirm the board shows WON, Coming up drops the covered follow-up round.
- [ ] **Step 3:** Update the spec's Status line to implemented; note any deviation discovered during implementation in the spec (not silently).
- [ ] **Step 4: Commit** — `chore: per-leg outcomes closing sweep (task 9)` — then hand off to review (`superpowers:finishing-a-development-branch` decides merge/PR with the owner).
