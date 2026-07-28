# Cleanup Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear five WISHLIST debts in one pass — per `docs/superpowers/specs/2026-07-28-cleanup-batch-design.md`.

**Architecture:** Four independent small fixes plus one real mechanism (client-side fold preservation across htmx swaps). Nothing redesigns behaviour shipped this week; `all_legs_cancelled` gains one more consumer.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, FastAPI + Jinja2 + htmx, babel gettext (ja/zh).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Suites run in the FOREGROUND. Accepted baseline: exactly 2 pre-existing env failures (`test_test_dm_when_bot_disabled`, `test_healthz`).
- Branch is `cleanup-batch` (off `main`). Commit there; never switch branches.
- Invariant 8 is the reference, not the target: an opt-out forfeits the reminder, never the record. This batch makes the COPY match the code.
- Invariant 4: notifications go through the `notifications` outbox. Task 2 SUPPRESSES a notice; it must not add or reroute a send path.
- `open_round_id` and its tests stay — it is the JS-off half; the new client mechanism is the generalising half.
- New/changed user-visible strings hand-filled ja+zh, no fuzzy; run the pybabel cycle and delete `messages.pot`; delete by hand any msgid this orphans.
- Invariant 7: no user text in inline `on*`; `| tojson` never `| safe`; never `data-name`.
- CSS (if any) in the main body or the existing phone/tablet sections; no new top-level media query (guard pins 6).
- Commit messages as given, plus `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Slugs prefer `title_en`, and the importer debt

**Files:**
- Modify: `src/app/web/routes/imports.py` (`generate_event_id` — find it; it may live elsewhere), `src/app/domain/yaml_import.py`, `src/app/db/service.py` (`match_tag_ids_by_name` docstring), `src/app/web/templates/preferences.html`
- Test: `tests/test_imports.py` (slugs), `tests/test_draft_import.py` or `tests/test_yaml_import.py` (the container-guard warning — find which exists)

**Interfaces:** none produced; these are self-contained.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_event_id_prefers_the_english_title(client, db):
    # a Japanese-only `title` WITH `title_en` set -> slug from the English,
    # not the "concert" fallback
    ...

async def test_event_id_falls_back_to_the_japanese_title(client, db):
    # no title_en -> today's behaviour, unchanged
    ...

def test_a_container_value_warns_instead_of_blanking_silently():
    # a draft whose `organizer:` is a list -> ParsedConcert.warnings mentions
    # the field; the value itself must NOT be stringified into the warning
    # (that stringify is what the alias-fan-out DoS fix removed)
    ...
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement all four items.**
  - `generate_event_id`: prefer `title_en` when non-blank, else `title`. One line plus a comment naming invariant 6 (URLs are the human-readable identity) and why there is no backfill (`event_id` is editor-owned after creation; rewriting a live URL breaks held links).
  - `yaml_import.py`: drop the dead `or 'nesting too deep'` fallback — an exception is always truthy. Keep the message informative for the RecursionError path, which is what that fallback was reaching for; check how that path currently reports and make it say something true.
  - `_text`'s container guard: warn, naming the field, WITHOUT stringifying the value.
  - `match_tag_ids_by_name` docstring: state first-tag-wins collision order and that blank names drop from both output lists.
  - `preferences.html`: backslashes → forward slashes in the preset-item edit form action.

- [ ] **Step 4: Run** the touched files, then the FULL suite + ruff.

- [ ] **Step 5: Commit** — `fix: slug event ids from the English title, and clear the importer debt (task 1)`

---

### Task 2: The dialog stops overstating, and a dead concert announces nothing

**Files:**
- Modify: `src/app/web/templates/_following_toggle.html`, `src/app/db/service.py` (`handle_newly_tagged`)
- Modify: both `messages.po`
- Test: `tests/test_concert_page.py` (dialog), `tests/test_service.py` or `tests/test_tags.py` (tag attach — find where `handle_newly_tagged`'s tests live and join them)

**Interfaces:** consumes the existing `all_legs_cancelled`.

**Copy requirement (spec §B):** both LIVE branches must keep naming the reminder loss — that is why the confirmation is heavy — and must state that the recorded mark survives. The dead-concert branch shipped on `cancelled-concerts` and is NOT to be touched.

**Suppression requirement (spec §C, owner ruling 1):** when `all_legs_cancelled`, `handle_newly_tagged` queues NO notification and applies NO preset. Ask the predicate ONCE at the top, not per subscriber. Do not add or reroute any send path — this only skips (invariant 4).

- [ ] **Step 1: Write the failing tests.**

```python
async def test_the_unfollow_dialog_says_the_record_survives(client, db):
    # both live branches: the reminder loss is still named AND the mark is
    # stated as surviving
    ...

async def test_attaching_a_tag_to_a_dead_concert_notifies_nobody(session):
    n = await handle_newly_tagged(session, dead_concert, [artist_tag])
    assert (await notifications(session)) == []
    assert (await rules_for(session, follower)) == []

async def test_attaching_a_tag_to_a_live_concert_is_unchanged(session):
    # the regression half -- this matters more than the fix half
    ...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** both. `handle_newly_tagged` needs the concert's days; check whether its caller already holds them before adding a query, and say what you found.
- [ ] **Step 4: Catalogues** for the two reworded msgids; delete `messages.pot`.
- [ ] **Step 5: Run** the touched files + `tests/test_i18n_catalogues.py`, then the FULL suite + ruff.
- [ ] **Step 6: Commit** — `fix: the unfollow dialog and a dead concert both stop overstating (task 2)`

---

### Task 3: Expanded folds survive an htmx swap

**Files:**
- Modify: `src/app/web/templates/base.html` (beside the existing htmx listeners), `_round_rows.html`, `_deadline_rows.html`
- Test: `tests/test_concert_page.py`, `tests/test_home.py`

**Interfaces:** produces the `data-fold` key convention — `leg-{day_id}` (a leg's round fold), `block-{event_id}` (a concert block on Home), `more-concerts` (Home's page-level fold).

**Mechanism (spec §D):** in `base.html`, on `htmx:beforeRequest` collect the `data-fold` keys of open `<details>` inside the request's target; on `htmx:afterSettle` reopen the matching ones. Generic and caller-free — it covers the outcome routes and Home's blocks with no per-caller plumbing.

`open_round_id` STAYS: it is server-rendered, so it is the half that works with JS off; this is the half that generalises. Do not remove it or its tests.

Read `base.html`'s existing listener block first and match its idiom (it already handles `htmx:beforeRequest`/`htmx:afterRequest` for the progress bar and `htmx:afterSwap` for countdowns). Invariant 7: no user text interpolated into any handler; the keys are ids and event_ids only.

- [ ] **Step 1: Write the failing tests.** These assert the MARKUP contract the script depends on (the presence and shape of `data-fold` on each fold), since a headless test cannot exercise the browser event:

```python
async def test_every_leg_fold_carries_a_stable_key(client, db): ...
async def test_home_block_folds_carry_stable_keys(client, db): ...
```

Plus, in whichever test already asserts `base.html`'s script content (find it — the theme/toast tests do something similar), one assertion that the restore listener exists and keys off `data-fold`.

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the keys and the listener.
- [ ] **Step 4: MEASURE** (repo rule — this one is genuinely browser behaviour, so a headless test cannot prove it): seed a temp dev DB (never the repo's `app.db`), run web-only (empty `DISCORD_TOKEN`), then in the browser: expand a leg's fold, toggle a DIFFERENT leg off, and confirm the first fold is still open; press a capture button inside an expanded fold and confirm it stays open; confirm a fold that was closed stays closed. Record what you observed at 375 and 1200.
- [ ] **Step 5: Run** the touched files, then the FULL suite + ruff.
- [ ] **Step 6: Commit** — `feat: expanded folds survive an htmx swap (task 3)`

---

### Task 4: Closing sweep

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) + `uv run ruff check .`; record tallies.
- [ ] **Step 2:** Spec Status → implemented (2026-07-28) plus an "Implementation deviations (recorded)" section if any arose.
- [ ] **Step 3:** WISHLIST: move #2, #3, #4, #5 and #7 to Shipped (dated, house style, one entry each or one batched entry — judge which reads better and say why); move **#6 to Rejected** with the owner's reason (a board card is a scanning surface; one badge per card is what a badge is for; the concert page one click away labels every round). Renumber the remaining Proposed; add the revision-pass paragraph; fix `#N` cross-references.
- [ ] **Step 4:** CLAUDE.md: two short additions — `event_id` slugs prefer `title_en`; and the `data-fold` convention (folds inside a swappable region carry a stable key so expanded state survives, with `open_round_id` named as the JS-off complement).
- [ ] **Step 5:** Read the run ledger and cross-check your deviation list against its rulings.
- [ ] **Step 6: Commit** — `chore: cleanup batch closing sweep (task 4)`
