# Ladder Declutter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap the board card's ladder, fold the concert page's settled rounds per leg, and pay down the query debt #98 amplified — per `docs/superpowers/specs/2026-07-27-ladder-declutter-design.md`.

**Architecture:** Rung selection becomes a pure function in `domain/board.py`. The concert page's per-leg visibility split is computed in `db/service.py` on the rows it already builds, driven by `_wants_you` (its third consumer, unchanged). `covered_round_ids` gains a batched sibling and keeps its single-concert signature. No schema change, no new write path.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, FastAPI + Jinja2 + htmx, babel gettext (ja/zh).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Suites run in the FOREGROUND. Accepted baseline: exactly 2 pre-existing env failures (`test_test_dm_when_bot_disabled`, `test_healthz`).
- Branch is `ladder-declutter` (off `main`). Commit there; never switch branches.
- `domain/board.py` is PURE: no ORM, no I/O, no sqlalchemy/fastapi/discord imports. It receives already-built objects.
- `capture_gates`, `capture_actions`, which button shows when, the per-leg grouping, cancelled-leg rendering, upgrade locking, Home's blocks, Discover, and the DM flow are OUT of scope.
- `_wants_you` is CONSUMED, never redefined or copied. `_covered_from_secured` (shared by the planner and the read side) is untouched.
- New user-visible strings: `{% trans %}`/`{% pluralize %}` or `ngettext`, hand-filled ja+zh, no fuzzy, plural forms intact; `tests/test_i18n_catalogues.py` enforces. Run the pybabel extract/update cycle and delete `messages.pot`.
- Any CSS goes in the main body (desktop) or INSIDE the existing `@media (max-width: 700px)` / `701-1040px` sections — no new top-level media query (guard pins 6). Radius 3px, existing tokens, both themes.
- Invariant 7: `| tojson` never `| safe`; no user text in inline `on*`; never `data-name`.
- Commit messages as given, plus `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Cap the board card ladder

**Files:**
- Modify: `src/app/domain/board.py` (beside `column_for`/`pill_tone`)
- Modify: `src/app/web/templates/_board.html`
- Modify: both `messages.po`
- Test: `tests/test_domain_board.py`, `tests/test_board_queries.py` or `tests/test_home.py` (render assertion — use whichever already renders a board)

**Interfaces:**
- Consumes: `Rung` (a plain frozen dataclass in `db/service.py` with `round_id, label, state, detail`; `state` ∈ `"lost" | "applied" | "won" | "paid" | "live" | "todo"`). The function takes the built list — do NOT import it into `domain/`; type the parameter loosely (`list` / `Sequence`) or use a `TYPE_CHECKING`-only import so the purity rule holds.
- Produces:

```python
VISIBLE_RUNGS = 2

def visible_rungs(rungs) -> tuple[list[tuple[int, object]], int]:
    """(numbered visible rungs, hidden count)."""
```

**Numbering matters:** `_board.html`'s `rung(r, i)` macro renders `i` as the mark for `live`/`todo` states, so a rung must keep its ORIGINAL 1-based ladder position after filtering. Hence the `(position, rung)` pairs.

**Selection rule:** keep the rung that explains the card's column — the LAST rung whose state is not `"todo"` — and the next actionable one after it — the FIRST rung after that whose state is `"live"` or `"todo"`. Preserve ladder order, de-duplicate, and cap at `VISIBLE_RUNGS`. If the ladder is already `<= VISIBLE_RUNGS` long, return every rung and `0`. If no rung is non-`todo` (nothing has happened yet), keep the first `VISIBLE_RUNGS`.

- [ ] **Step 1: Write the failing tests** in `tests/test_domain_board.py` (that file tests pure functions; follow its style — build lightweight stand-ins for `Rung`, e.g. a local `namedtuple("R", "round_id label state detail")`, since importing the ORM-side dataclass would breach purity):

```python
def test_visible_rungs_returns_everything_when_short():
    rungs = [R(1, "1次", "lost", None), R(2, "2次", "live", None)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [1, 2]
    assert hidden == 0


def test_visible_rungs_keeps_the_state_rung_and_the_next_actionable():
    rungs = [
        R(1, "最速", "lost", None), R(2, "1次", "lost", None),
        R(3, "2次", "applied", None), R(4, "一般", "todo", None),
        R(5, "FCFS", "todo", None),
    ]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [3, 4]      # original positions kept
    assert [r.label for _, r in visible] == ["2次", "一般"]
    assert hidden == 3


def test_visible_rungs_all_settled_ladder():
    rungs = [R(1, "1次", "lost", None), R(2, "2次", "lost", None),
             R(3, "一般", "paid", None)]
    visible, hidden = visible_rungs(rungs)
    assert [r.label for _, r in visible] == ["一般"]
    assert hidden == 2


def test_visible_rungs_nothing_recorded_yet_keeps_the_head():
    rungs = [R(i, f"r{i}", "todo", None) for i in range(1, 6)]
    visible, hidden = visible_rungs(rungs)
    assert [p for p, _ in visible] == [1, 2]
    assert hidden == 3
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_domain_board.py -q` — FAIL (ImportError).

- [ ] **Step 3: Implement** `visible_rungs` in `domain/board.py` with a docstring explaining the two-rung choice and why positions are preserved (the mark numbering).

- [ ] **Step 4: Template.** In `_board.html`, replace the rung loop with the capped output. The card currently iterates `card.rungs` with `loop.index` — switch to the pairs, and render the count line as plain text (NOT a `details` — owner decision 3):

```jinja
{% set vis, hidden = visible_rungs(card.rungs) %}
{% for pos, r in vis %}{{ rung(r, pos) }}{% endfor %}
{% if hidden %}
<span class="rmore">{% trans count=hidden %}+{{ count }} earlier round{% pluralize %}+{{ count }} earlier rounds{% endtrans %}</span>
{% endif %}
```

Register `visible_rungs` as a Jinja global in `web/app.py` beside the other domain helpers (`dual`, `day_month`, …) so the template can call it; the pure function stays the single definition.

- [ ] **Step 5: CSS.** Add a `.rmore` rule in the main body near the existing `.rung` rules — quiet (`--dim`), small, non-interactive (no pointer, no hover). If the phone/tablet sections need a counterpart it goes inside them; do not add a media query.

- [ ] **Step 6: Catalogues** for the new plural pair; delete `messages.pot`.

- [ ] **Step 7: Render test** — a board card with 5 rounds renders exactly 2 `class="rung"` spans plus one `.rmore`, and the `.rmore` is not a `<details>`/`<summary>`.

- [ ] **Step 8: Run** those files, then the FULL suite + ruff. **Commit** — `feat: cap the board card ladder at the rungs that matter (task 1)`

---

### Task 2: Batch `covered_round_ids`, re-measure the query pin

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py` (the existing query-count pin), `tests/test_lottery_outcomes.py` (equivalence)

**Interfaces:**
- Consumes: `secured_day_ids_by_round`, `_covered_from_secured`, `_covered_day_ids` (all unchanged).
- Produces:

```python
async def covered_round_ids_by_concert(
    session: AsyncSession, user_id: int, concert_ids: set[int]
) -> dict[int, set[int]]
```

`covered_round_ids(session, user_id, concert_id)` becomes a thin wrapper returning `(await covered_round_ids_by_concert(session, user_id, {concert_id})).get(concert_id, set())`, so every existing call site is untouched and there is exactly one derivation.

**Method:** one pass loading the rounds, day ids, outcomes and `RoundOutcomeDay` rows for ALL the given concerts (each a single `.in_(concert_ids)` query), then group per concert and run the existing `_covered_from_secured` fold per concert in memory. Do NOT reimplement the fold. Opted-out and cancelled day sets must be gathered the same way `covered_round_ids` gathers them today — batched over the whole set.

- [ ] **Step 1: Failing tests.** Equivalence first, in `tests/test_lottery_outcomes.py`:

```python
async def test_batched_covered_matches_the_single_concert_helper(session):
    """The batched helper is the same derivation, not a second one."""
    # two concerts with different shapes: one with a partial win + opt-out,
    # one with a full win covering a later round.
    ...
    batched = await covered_round_ids_by_concert(session, UID, {c1.id, c2.id})
    assert batched[c1.id] == await covered_round_ids(session, UID, c1.id)
    assert batched[c2.id] == await covered_round_ids(session, UID, c2.id)


async def test_batched_covered_skips_concerts_with_no_standing(session):
    # a concert where the user holds nothing yields an empty set (or is absent)
    ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** wire `my_deadline_rows`' per-concert loop to one `covered_round_ids_by_concert` call over its secured concert set.

- [ ] **Step 5: Re-measure and re-pin.** Run `tests/test_service.py::test_my_deadline_blocks_query_count_is_pinned`; it currently pins `<= 45` against a measured 42. Print the new count, tighten the bound to `measured + 3`, and update the test's docstring with the new composition. Report the before/after numbers.

- [ ] **Step 6: Run** the suppression suites UNTOUCHED as equivalence evidence — `tests/test_upgrade_suppression.py tests/test_leg_opt_out_suppression.py tests/test_lottery_outcomes.py tests/test_home.py tests/test_service.py` — then the FULL suite + ruff.

- [ ] **Step 7: Commit** — `perf: batch covered-round derivation across concerts (task 2)`

---

### Task 3: Fold settled rounds per leg on the concert page

**Files:**
- Modify: `src/app/db/service.py` (`LegRounds`, `concert_round_rows`)
- Modify: `src/app/web/templates/_round_rows.html`
- Modify: both `messages.po`
- Test: `tests/test_concert_rows.py`, `tests/test_concert_page.py`

**Interfaces:**
- Consumes: `_wants_you`, `RoundRow` (`round_`, `outcome`, `can_capture`, `can_report_result`, `covered`, `leg_result`, `upgrade_locked`, `capture_days`, `any_day_won`, `has_day_results`, `primary_anchor`, `primary_at_utc`), `LegResult`, `RoundKind`.
- Produces: `LegRounds` gains, WITHOUT changing `rounds` (existing consumers such as `concert_next_moment` keep seeing every row):

```python
visible: tuple[RoundRow, ...] = ()
folded: tuple[RoundRow, ...] = ()
fold_counts: tuple[tuple[str, int], ...] = ()   # ordered ("lost"|"skipped"|"covered"|"upcoming", n)
```

**Visibility rule** (spec §A) — a row is visible on its leg when ANY holds:
1. `row.leg_result is LegResult.WON` (the receipt — visible even when settled/PAID).
2. `_wants_you(row.outcome, row.can_capture, row.round_.closes_at_utc, now)`.
3. `row.round_.kind is RoundKind.UPGRADE and not row.upgrade_locked`.
4. It is the single soonest round on this leg with `opens_at_utc > now`, AND the leg is not secured (no row on it has `leg_result is LegResult.WON`).

A CANCELLED leg folds everything (`visible` empty). Folded rows keep chronological order.

**`fold_counts` tallying**, first match wins per row, emitted in this fixed order: `lost` (`leg_result is LegResult.LOST` or `outcome is LOST`), `skipped` (`outcome is NOT_APPLIED`), `covered` (`row.covered`), `upcoming` (round not yet opened). Rows matching none are counted in none — the summary count is `len(folded)`, the chips explain part of it.

- [ ] **Step 1: Failing service tests** in `tests/test_concert_rows.py` (adapt seeds to its helpers):

```python
async def test_a_secured_leg_keeps_its_receipt_and_folds_the_rest(session):
    # leg won via round A; rounds B (lost earlier) and C (later general sale,
    # now covered) fold. A stays visible even after PAID.
    ...
    assert [r.round_.id for r in leg.visible] == [round_a.id]
    assert dict(leg.fold_counts)["lost"] == 1


async def test_an_unsecured_leg_shows_awaited_open_and_exactly_one_upcoming(session):
    # APPLIED round + open round + THREE later unopened rounds
    ...
    assert len([r for r in leg.visible if r.round_.opens_at_utc > NOW]) == 1
    assert dict(leg.fold_counts)["upcoming"] == 2


async def test_an_eligible_upgrade_is_always_visible_and_a_locked_one_folds(session): ...
async def test_a_cancelled_leg_folds_everything(session): ...
async def test_a_viewer_with_no_standing_still_sees_the_open_round(session): ...
async def test_rounds_stays_the_full_set_for_next_for_you(session):
    # LegRounds.rounds unchanged in length; visible+folded partition it
    assert len(leg.visible) + len(leg.folded) == len(leg.rounds)
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** in `concert_round_rows`, after the per-leg lists are built. Keep the rule in ONE helper (e.g. `_split_leg_rounds(rows, day, now) -> (visible, folded, counts)`) with a docstring naming the four clauses and pointing at the spec.

- [ ] **Step 4: Template.** In `_round_rows.html`, the per-leg loop renders `leg.visible` as it renders rounds today, then:

```jinja
{% if leg.folded %}
<details class="moreround">
  <summary>
    {% trans count=leg.folded|length %}+{{ count }} more round{% pluralize %}+{{ count }} more rounds{% endtrans %}
    {% for kind, n in leg.fold_counts %}<span class="fchip">{{ fold_count_label(kind, n) }}</span>{% endfor %}
  </summary>
  {% for row in leg.folded %}{{ ...the same row markup... }}{% endfor %}
</details>
{% endif %}
```

Extract the existing per-round row markup into a macro in this file first if it is inline, so `visible` and `folded` render through ONE definition — never two copies. `fold_count_label(kind, n)` is a small Jinja global registered in `web/app.py` returning the ngettext'd chip text per kind (`{n} lost`, `{n} skipped`, `{n} covered`, `{n} upcoming`) — each its OWN msgid with its own plural, no sentence composition (spec §B).

- [ ] **Step 5: CSS.** `.moreround` summary + `.fchip` in the main body, matching `.morerounds` from #98 (same quiet affordance, 3px radius, `--dim`); phone/tablet counterparts only inside the existing sections.

- [ ] **Step 6: Catalogues** — the fold summary pair plus the four chip pairs; delete `messages.pot`.

- [ ] **Step 7: Page tests** in `tests/test_concert_page.py`: the fold renders with the right count and chips; a folded round's capture form is present in the DOM (reachable, collapsed); no `<details class="moreround">` when nothing folds; a secured leg shows its receipt row outside the fold.

- [ ] **Step 8: Run** those files + `tests/test_i18n_catalogues.py`, then the FULL suite + ruff. **Commit** — `feat: fold settled rounds per leg on the concert page (task 3)`

---

### Task 4: Closing sweep

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) + `uv run ruff check .`; record tallies.
- [ ] **Step 2:** Smoke against a seeded temp DB (never the repo's `app.db`), web-only mode, at 375/730/1200: a 6-round concert's board card shows 2 rungs + the count line; its concert page shows one fold per leg with correct chips; expanding a fold reveals working capture buttons; a secured leg shows its receipt outside the fold.
- [ ] **Step 3:** Spec Status → implemented (2026-07-27) + an "Implementation deviations (recorded)" section if any arose.
- [ ] **Step 4:** WISHLIST: move #1 (board ladder) to Shipped dated, in house style, naming the cap-without-expansion decision AND the concert-page fold that shipped with it (log the concert-page declutter as its own Shipped entry, since it was raised after the list was written); renumber the remaining Proposed; add the revision-pass paragraph; note the query-debt payment against whichever entry tracked it; fix `#N` cross-references.
- [ ] **Step 5:** CLAUDE.md UI conventions: 2-3 sentences — the board caps its ladder and never expands (capture stays off cards); the concert page folds per leg on the `_wants_you` rule, which now drives three surfaces.
- [ ] **Step 6: Commit** — `chore: ladder declutter closing sweep (task 4)`
