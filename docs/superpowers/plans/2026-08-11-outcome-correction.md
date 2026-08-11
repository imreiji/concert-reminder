# Outcome correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user take back a recorded lottery outcome — on the concert page per leg or per round, and in Discord per round — by returning it to unrecorded, from which the existing capture buttons take over.

**Architecture:** One new service writer, `clear_round_outcome`, is the single deletion path for `RoundOutcome`/`RoundOutcomeDay` and owns its own rule resync (invariant 2). A web route and a Discord button both call it and add no logic of their own. The web surface reuses the existing `_outcome_response` renderer, the existing `_capture_actions.html` macro, and the existing `<dialog class="prune">` shape; the bot reuses `_progressive_click`'s "re-derive, never trust the message" rule.

**Tech Stack:** Python 3.12, FastAPI, Jinja2 + htmx, SQLAlchemy async, discord.py, pytest-asyncio, Babel.

**Spec:** `docs/superpowers/specs/2026-08-11-outcome-correction-design.md` — read it before Task 1. **Sketch:** `docs/superpowers/demo/dekimasen-outcome-correction-sketch.html` (open it; it is the visual reference for Tasks 3–4).

## Global Constraints

- **The code in this plan is UNVERIFIED.** It was written by reading the tree, not by running it. Treat every snippet as a strong hint about intent, not as authority. If a signature, import, or attribute does not match what you find, the tree is right and the plan is wrong — fix it and say so in your report.
- **Branch:** `outcome-correction`, already created off `origin/main`. Do not create another.
- **Both gates must pass before every commit:** `uv run --isolated pytest -q` and `uv run --isolated ruff check .`. Use `--isolated` — an external process holds a lock on `.venv` and a plain `uv run` will try to resync and fail.
- **Run test commands in the FOREGROUND.** Do not background the suite.
- **Every new user-visible string must be translated in BOTH `.po` catalogues in the SAME commit.** `tests/test_i18n_catalogues.py` fails on any extracted-but-untranslated msgid, so a commit that adds English only is a red commit. Exact ja/zh strings are given inline in each task — use them verbatim. Never edit an existing msgid's bytes.
- **Invariant 2:** `clear_round_outcome` is the only place these rows may be deleted, and it calls `reinstate_user_rules` itself. No caller adds a second resync; no second writer is introduced.
- **Invariant 7:** user-controlled text (round labels, concert titles) reaches the page as text content or a `data-` attribute read via `dataset`, never interpolated into an inline `on*` handler.
- **UI tokens:** 3px radius (there is a sweep test forbidding 6px/8px), type weights 400/600/700 only. New phone rules go INSIDE the existing `@media (max-width: 700px)` block at the end of `style.css` — `tests/test_theme_and_tokens.py` pins the top-level media-query count.
- **Tests state the mutation they would survive.** A test that only re-asserts the shape of the code under it is a proxy assertion and does not count. For each test you write, be able to name a wrong implementation it would catch.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/app/db/core.py` | `clear_round_outcome` + `_rederive_round_from_days` | 1 |
| `src/app/db/service.py` | facade re-export of both names | 1 |
| `tests/test_outcome_clear.py` | service-level behaviour (new file) | 1 |
| `src/app/web/routes/outcomes.py` | `POST /rounds/{id}/outcome/clear` | 2 |
| `tests/test_outcome_routes.py` | route behaviour (existing file) | 2 |
| `src/app/web/templates/_capture_actions.html` | the `correctable` parameter and the affordance | 3 |
| `src/app/web/templates/_round_rows.html` | passes `correctable=True` | 3 |
| `src/app/web/static/style.css` | `.reopen` + its phone rule | 3 |
| `src/app/web/templates/base.html` | `cleared` toast key | 3 |
| `src/app/web/templates/concert_detail.html` | the confirm dialog + its script | 4 |
| `src/app/bot/views.py` | `ClearOutcomeButton`, the confirm view, the flat-pair guard | 5 |
| `docs/architecture.md` | module entries for the new seams | 6 |

---

### Task 1: The service writer

**Files:**
- Modify: `src/app/db/core.py` (import line 23; new functions after `record_remaining_days_lost`, which ends near line 985)
- Modify: `src/app/db/service.py` (import block near line 178, `__all__` near line 572)
- Test: `tests/test_outcome_clear.py` (create)

**Interfaces:**
- Consumes: `_covered_day_ids`, `_won_day_ids`, `unresolved_day_ids`, `reinstate_user_rules`, `_now` — all already in `core.py`.
- Produces: `clear_round_outcome(session, user_id: int, round_id: int, day_id: int | None = None, now: datetime | None = None) -> None`, importable from `app.db.service`. Tasks 2 and 5 call exactly this.

**Read first:** spec §A. The rule that makes this simple: per-leg clearing is only ever offered for a leg that has its own `RoundOutcomeDay` row, so day rows always exist on that path and the no-rows-means-all convention is already off. That is why no materialization step appears below — do not add one.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outcome_clear.py`. Reuse `seed_two_legs` from `tests/test_lottery_outcomes.py` (import it) rather than writing a new fixture.

```python
"""Taking back a recorded outcome: clear_round_outcome is the only path that
deletes RoundOutcome/RoundOutcomeDay rows, and it re-plans the user's rules
itself (invariant 2)."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import ReminderQueue, ReminderRule, RoundOutcome, RoundOutcomeDay
from app.db.service import (
    clear_round_outcome,
    ensure_user,
    record_round_day_result,
    record_round_outcome,
    set_leg_opt_out,
    sync_rule,
)
from app.domain.types import Anchor, LegResult, LotteryOutcome

from tests.test_lottery_outcomes import NOW, seed_two_legs


async def _outcome(s, round_id: int) -> LotteryOutcome | None:
    return (await s.execute(
        select(RoundOutcome.outcome).where(
            RoundOutcome.user_id == 42, RoundOutcome.round_id == round_id
        )
    )).scalar_one_or_none()


async def _day_rows(s, round_id: int) -> dict[int, LegResult]:
    rows = (await s.execute(
        select(RoundOutcomeDay.day_id, RoundOutcomeDay.result).where(
            RoundOutcomeDay.user_id == 42, RoundOutcomeDay.round_id == round_id
        )
    )).all()
    return {day_id: result for day_id, result in rows}


async def test_whole_round_clear_removes_outcome_and_every_day_row(session):
    # Mutation caught: a clear that deletes only the RoundOutcome and leaves
    # RoundOutcomeDay rows behind. Those rows are what secured_day_ids_by_round
    # reads, so the user would still "hold" a ticket on a round showing nothing.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    assert await _outcome(s, both.id) is LotteryOutcome.WON

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert await _outcome(s, both.id) is None
    assert await _day_rows(s, both.id) == {}


async def test_whole_round_clear_leaves_other_rounds_alone(session):
    # Mutation caught: a delete() missing its round_id predicate.
    s = session
    _c, leg_a, _leg_b, a_only, both, _general = await seed_two_legs(s)
    await record_round_outcome(s, 42, a_only.id, LotteryOutcome.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert await _outcome(s, a_only.id) is LotteryOutcome.LOST


async def test_whole_round_clear_leaves_other_users_alone(session):
    # Mutation caught: a delete() missing its user_id predicate -- one user
    # correcting their own record wiping everybody else's.
    s = session
    _c, leg_a, _leg_b, _a_only, both, _general = await seed_two_legs(s)
    await ensure_user(s, 99, "someone-else")
    await record_round_outcome(s, 42, both.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(s, 99, both.id, LotteryOutcome.WON, NOW)

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert (await s.execute(
        select(RoundOutcome.outcome).where(
            RoundOutcome.user_id == 99, RoundOutcome.round_id == both.id
        )
    )).scalar_one_or_none() is LotteryOutcome.WON


async def test_per_leg_clear_keeps_the_other_legs_win(session):
    # The headline case. Mutation caught: a per-leg clear that falls through to
    # the whole-round branch, throwing away Saturday to fix Sunday.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_b.id, now=NOW)

    assert await _day_rows(s, both.id) == {leg_a.id: LegResult.WON}
    assert await _outcome(s, both.id) is LotteryOutcome.WON


async def test_per_leg_clear_preserves_paid(session):
    # Mutation caught: re-deriving to WON unconditionally, which demotes PAID
    # and re-arms a payment reminder for a ticket already paid for.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await record_round_outcome(s, 42, both.id, LotteryOutcome.PAID, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_b.id, now=NOW)

    assert await _outcome(s, both.id) is LotteryOutcome.PAID


async def test_per_leg_clear_of_the_only_win_reopens_the_round(session):
    # Mutation caught: leaving the round WON after its last WON row is gone --
    # a round claiming a ticket no leg holds.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _day_rows(s, both.id) == {leg_b.id: LegResult.LOST}
    assert await _outcome(s, both.id) is LotteryOutcome.APPLIED


async def test_per_leg_clear_settles_lost_when_nothing_is_left_open(session):
    # The rare third branch: the cleared leg is opted out, so it is not
    # "unresolved" either. Mutation caught: an unconditional APPLIED, which
    # would re-open a round the reader has nothing pending on.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await set_leg_opt_out(s, 42, leg_a.id, True)

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _outcome(s, both.id) is LotteryOutcome.LOST


async def test_forged_day_id_writes_nothing(session):
    # Mutation caught: dropping the _covered_day_ids check, which lets a form
    # post name another concert's leg.
    s = session
    _c, leg_a, _leg_b, a_only, _both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, a_only.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, a_only.id, day_id=999_999, now=NOW)

    assert await _day_rows(s, a_only.id) == {leg_a.id: LegResult.WON}


async def test_missing_round_returns_silently(session):
    # Mutation caught: raising instead of returning, which would 500 the route
    # rather than letting it answer 404 itself.
    await clear_round_outcome(session, 42, 999_999, now=NOW)


async def test_clear_replans_the_queue(session):
    # The invariant-2 test, and the one that fails if the resync is dropped:
    # a NOT_APPLIED round plans no RESULTS row, so clearing it must bring that
    # row back. Asserting on reminder_queue, not on a return value.
    s = session
    _c, _leg_a, _leg_b, a_only, _both, _general = await seed_two_legs(s)
    rule = ReminderRule(user_id=42, round_id=a_only.id, anchor=Anchor.RESULTS,
                        offset_days=0, offset_hours=0)
    s.add(rule)
    await s.flush()
    await sync_rule(s, rule, NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []

    await record_round_outcome(s, 42, a_only.id, LotteryOutcome.NOT_APPLIED, NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() == []

    await clear_round_outcome(s, 42, a_only.id, now=NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []
```

If `seed_two_legs`'s `a_only` round has no `results_at_utc`, the last test will plan nothing. Check the seed helper; if the anchor is missing, set `a_only.results_at_utc = dt(7, 1)` (import `dt` too) before syncing and say so in your report.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --isolated pytest tests/test_outcome_clear.py -q`
Expected: FAIL — `ImportError: cannot import name 'clear_round_outcome' from 'app.db.service'`.

- [ ] **Step 3: Implement**

In `src/app/db/core.py`, extend the sqlalchemy import on line 23:

```python
from sqlalchemy import delete, exists, func, or_, select
```

Add after `record_remaining_days_lost`:

```python
async def _rederive_round_from_days(
    session: AsyncSession, user_id: int, round_: Round, now: datetime,
) -> None:
    """The round-level answer implied by the day rows that SURVIVE a per-leg
    clear -- the only place that derivation lives.

    Three cases, and the order matters. A surviving WON row means the reader
    still holds a ticket, so the round keeps whatever it has: WON stays WON and
    PAID stays PAID, because demoting PAID would re-arm a payment reminder for a
    ticket already paid for (the same trap `record_round_day_result` guards).
    Otherwise a leg with nothing recorded means the campaign is open again, and
    APPLIED is the honest word for it -- the reader had a per-leg result, so
    they were in the draw, and it is exactly the state the won/lost buttons
    re-open from. Only when nothing is left to wait on (every remaining leg
    lost, cancelled or opted out) does the round settle LOST.

    Deliberately does NOT auto-arm the next round on that last branch, unlike
    `record_round_outcome`: a correction is not a new loss, and the arm that a
    genuine loss made is still there.
    """
    existing = (await session.execute(
        select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_.id
        )
    )).scalar_one_or_none()
    if existing is None:
        return  # nothing to re-derive; the day row was all there was
    if await _won_day_ids(session, user_id, round_.id):
        return  # a ticket survives -- WON stays WON, PAID stays PAID
    existing.outcome = (
        LotteryOutcome.APPLIED
        if await unresolved_day_ids(session, user_id, round_)
        else LotteryOutcome.LOST
    )
    await session.flush()


async def clear_round_outcome(
    session: AsyncSession, user_id: int, round_id: int, day_id: int | None = None,
    now: datetime | None = None,
) -> None:
    """Take a recorded outcome back -- the ONLY path that deletes a
    `RoundOutcome` or a `RoundOutcomeDay`, and the sibling of
    `record_round_outcome` in every other respect (invariant 2): a missing
    round returns silently, a day the round does not cover writes nothing, and
    it re-plans this user's rules for the whole concert itself, so no call site
    can forget to.

    `day_id` None clears the WHOLE round: the outcome and every day row. There
    is nothing to re-derive, because the round returns to "no row" -- the common
    case the entire model is built around, which is why no reader downstream
    needed a change for this feature.

    With a `day_id` it clears ONE leg. The surfaces only offer that for a leg
    that has its OWN day row, which is what keeps this simple: day rows already
    exist, so the no-rows-means-all convention is already off and nothing needs
    materializing first. Do not add a materialization step here -- if one looks
    necessary, the caller is offering a per-leg clear where it should be
    offering a whole-round one.
    """
    now = now or _now()
    round_ = await session.get(Round, round_id)
    if round_ is None:
        return

    if day_id is None:
        await session.execute(delete(RoundOutcomeDay).where(
            RoundOutcomeDay.user_id == user_id, RoundOutcomeDay.round_id == round_id,
        ))
        await session.execute(delete(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id,
        ))
        await session.flush()
        await reinstate_user_rules(session, user_id, round_.concert_id, now)
        return

    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == round_.concert_id)
    )).scalars())
    if day_id not in _covered_day_ids(round_, all_day_ids):
        return  # forged, stale, or another concert's leg: a committed no-op
    await session.execute(delete(RoundOutcomeDay).where(
        RoundOutcomeDay.user_id == user_id,
        RoundOutcomeDay.round_id == round_id,
        RoundOutcomeDay.day_id == day_id,
    ))
    await session.flush()
    await _rederive_round_from_days(session, user_id, round_, now)
    await reinstate_user_rules(session, user_id, round_.concert_id, now)
```

In `src/app/db/service.py`, add `clear_round_outcome` to the `from app.db.core import (...)` block and to `__all__`, both in alphabetical position. `tests/test_service_facade.py` fails if they disagree.

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_outcome_clear.py tests/test_service_facade.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full gates**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/app/db/core.py src/app/db/service.py tests/test_outcome_clear.py
git commit -m "feat(db): clear_round_outcome -- take a recorded outcome back"
```

---

### Task 2: The web route

**Files:**
- Modify: `src/app/web/routes/outcomes.py`
- Test: `tests/test_outcome_routes.py`

**Interfaces:**
- Consumes: `clear_round_outcome` from Task 1; `_outcome_response`, `_leg_of_concert`, `ensure_user` already in the module.
- Produces: `POST /rounds/{round_id}/outcome/clear`, form field `day_id` optional. Task 3's template posts to it.

**Read first:** the module docstring in `routes/outcomes.py`. This route holds no business logic — resolve the caller, hand off, commit, re-render — and reuses `_outcome_response` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_outcome_routes.py`, matching the client/login helpers already in that file (read them first — do not invent a new client fixture).

```python
async def test_clear_route_removes_the_outcome(client, session):
    # Mutation caught: a route that renders the fragment without writing.
    ...  # seed a round, record WON, POST /rounds/{id}/outcome/clear
    resp = await client.post(f"/rounds/{round_id}/outcome/clear")
    assert resp.status_code == 200
    assert await _outcome(session, round_id) is None


async def test_clear_route_404s_on_a_missing_round(client):
    # Mutation caught: dropping the existence check, which makes the route
    # report success for a write the service silently skipped.
    resp = await client.post("/rounds/999999/outcome/clear")
    assert resp.status_code == 404


async def test_clear_route_ignores_a_day_of_another_concert(client, session):
    # Mutation caught: passing day_id straight through, which would clear a leg
    # the round does not cover (or raise an FK error at commit).
    ...
    resp = await client.post(
        f"/rounds/{round_id}/outcome/clear", data={"day_id": other_concert_leg_id}
    )
    assert resp.status_code == 200
    assert await _day_rows(session, round_id) != {}


async def test_clear_route_redirects_without_htmx(client):
    # Mutation caught: returning a bare fragment to a JS-less browser, which
    # renders as the whole document.
    resp = await client.post(f"/rounds/{round_id}/outcome/clear",
                             follow_redirects=False)
    assert resp.status_code == 303


async def test_clear_route_sends_the_cleared_toast(client):
    # Mutation caught: reusing an outcome key, which would tell the reader they
    # had been marked lost.
    resp = await client.post(f"/rounds/{round_id}/outcome/clear",
                             headers={"HX-Request": "true"})
    assert "cleared" in resp.headers["HX-Trigger"]
```

Fill the `...` seeding from the patterns already in that file. Every test must assert on the DB or the response, never on the fact that a function was called.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_outcome_routes.py -q -k clear`
Expected: FAIL — 404/405 on an unregistered route.

- [ ] **Step 3: Implement**

Add `clear_round_outcome` to the `from app.db.service import (...)` block, then append:

```python
@router.post("/rounds/{round_id}/outcome/clear", response_class=HTMLResponse)
async def clear_outcome(
    request: Request,
    round_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    day_id: int | None = Form(None),
):
    """Take back what this reader recorded -- the un-answer, and the only route
    that deletes a RoundOutcome.

    The same thin shell as its two siblings above, for the same reasons: the
    service owns every rule (a missing round writes nothing, an uncovered day
    writes nothing, the round's rules are re-planned by the writer), so a second
    write path here would desync the queue (invariant 2).

    The one rule the route owns is its siblings' rule, applied to a third kind
    of input: a `day_id` that names no leg of THIS round's concert clears
    nothing at all, rather than reaching the service with an id it would have to
    re-validate anyway. One class of input, one answer.
    """
    round_ = await session.get(Round, round_id)
    if round_ is None:
        raise HTTPException(status_code=404)
    await ensure_user(session, user.id, user.username)
    if day_id is None or await _leg_of_concert(session, day_id, round_.concert_id):
        await clear_round_outcome(session, user.id, round_id, day_id)
    await session.commit()
    return await _outcome_response(request, session, user, "cleared", round_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_outcome_routes.py -q`
Expected: PASS. (The `cleared` toast key is added in Task 3; the header is emitted regardless, so this passes now.)

- [ ] **Step 5: Full gates, then commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src/app/web/routes/outcomes.py tests/test_outcome_routes.py
git commit -m "feat(web): POST /rounds/{id}/outcome/clear"
```

---

### Task 3: The affordance on the round row

**Files:**
- Modify: `src/app/web/templates/_capture_actions.html`
- Modify: `src/app/web/templates/_round_rows.html:114`
- Modify: `src/app/web/static/style.css` (new `.reopen` near `.act`, around line 940; phone rule inside the EXISTING `@media (max-width: 700px)` block near line 1995)
- Modify: `src/app/web/templates/base.html` (`TOAST_MSGS`, near line 349)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_concert_page.py` (or the existing concert-page render test file — find it with `rg "concert-rounds" tests/`)

**Interfaces:**
- Consumes: the route from Task 2.
- Produces: `capture_actions(..., correctable=False)`; only `_round_rows.html` passes `True`.

**Read first:** open the sketch in a browser. Section 2 is what this task builds.

- [ ] **Step 1: Write the failing tests**

```python
async def test_settled_round_offers_the_clear_and_drops_nothing_to_do(client):
    # Mutation caught: adding the affordance BESIDE "Nothing to do" instead of
    # replacing it -- the sentence is false once a correction exists.
    html = (await client.get(f"/concerts/{event_id}")).text
    assert "/outcome/clear" in html
    assert "Nothing to do" not in html


async def test_won_round_keeps_paid_and_adds_the_clear(client):
    # Mutation caught: an elif that swallows the Paid button.
    html = (await client.get(f"/concerts/{event_id}")).text
    assert 'value="paid"' in html
    assert "/outcome/clear" in html


async def test_resolved_leg_offers_a_per_leg_clear_carrying_its_day_id(client):
    # Mutation caught: a per-leg card posting no day_id, which would clear the
    # whole round -- Saturday thrown away to fix Sunday.
    html = (await client.get(f"/concerts/{event_id}")).text
    assert f'name="day_id" value="{leg_b_id}"' in html


async def test_home_rows_offer_no_clear(client):
    # Mutation caught: defaulting `correctable` to True, which puts a
    # destructive action into Home's one-tap flow.
    html = (await client.get("/")).text
    assert "/outcome/clear" not in html


async def test_unrecorded_round_offers_no_clear(client):
    # Mutation caught: rendering the affordance in the `outcome is none` branch,
    # where there is no answer to un-answer.
    ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_concert_page.py -q -k clear`
Expected: FAIL — `/outcome/clear` absent from the rendered page.

- [ ] **Step 3: Implement the macro**

In `_capture_actions.html`, add `correctable=False` as the last macro parameter and document it in the header comment. Add this helper macro above `capture_actions`:

```jinja
{# The un-answer. It is a real form with a method/action so it works with JS
   off, and it posts nothing but the leg it is scoped to -- `only_day` when this
   card is under a leg the reader answered individually, and nothing at all
   otherwise, which the route reads as "the whole round".

   `confirm_*` are set only when the press would drop a SECURED record; the
   dialog in concert_detail.html reads them off dataset and gates the submit.
   Absent, the form submits straight through, which is right: clearing a loss
   or a skip forfeits nothing and a confirmation would be theatre. The round
   label is user-controlled text and rides in a data- attribute, never an
   inline on* handler (invariant 7). #}
{% macro clear_form(round_id, target, day_id=None, confirm_label=None) -%}
<form hx-post="/rounds/{{ round_id }}/outcome/clear" hx-target="{{ target }}"
      hx-swap="outerHTML" method="post" action="/rounds/{{ round_id }}/outcome/clear"
      {% if confirm_label %}data-clear-confirm="1" data-clear-label="{{ confirm_label }}"{% endif %}>
  {% if day_id is not none %}<input type="hidden" name="day_id" value="{{ day_id }}">{% endif %}
  <button class="reopen" type="submit">{{ _("Change") }}</button>
</form>
{%- endmacro %}
```

Then wire it into the four branches. The settled branch becomes:

```jinja
{% else %}
{% if correctable %}{{ clear_form(round_id, target,
     confirm_label=(row.round_.label if row.outcome.value == "paid" else None)) }}
{% else %}<span class="done">{{ _("Nothing to do") }}</span>{% endif %}
{% endif %}
```

In the APPLIED branch add `{% if correctable %}{{ clear_form(round_id, target) }}{% endif %}` after the two buttons; in the WON branch add it with `confirm_label=row.round_.label`; inside the per-day branch, after the `{% for %}` over `ns.days`, add — only when this card is scoped to a leg that is already answered:

```jinja
{% if correctable and only_day is not none and not ns.days and row.leg_result %}
{{ clear_form(round_id, target, day_id=only_day,
     confirm_label=(row.round_.label if row.leg_result.value == "won" else None)) }}
{% endif %}
```

`ns.days` empty with an `only_day` set is exactly "this leg is answered while its siblings are not" — the case the existing comment says renders no forms at all. Use `loc(row.round_, "label")` instead of `row.round_.label` if the surrounding template already localizes labels that way; check `_round_rows.html:86`.

In `_round_rows.html:114`, pass `correctable=True` to the `capture_actions(...)` call.

- [ ] **Step 4: Implement the CSS**

Near `.act` in `style.css`:

```css
/* The un-answer (_capture_actions.html). Deliberately NOT an .act: a
   correction is not a capture action, and on a settled row it is the only
   thing in the cell -- bordered, it would read as the row's call to action
   when almost nobody needs it. Quiet text with a hairline underline, accent on
   hover, same 3px radius as everything else for its focus ring. */
.reopen {
  font: inherit; font-size: .72rem; font-weight: 700; letter-spacing: .03em;
  padding: .22rem .3rem; border: 1px solid transparent; border-radius: 3px;
  background: none; color: var(--dim); cursor: pointer; white-space: nowrap;
  text-decoration: underline; text-underline-offset: 2px;
  text-decoration-color: var(--line);
  transition: color 150ms ease, text-decoration-color 150ms ease;
}
.reopen:hover { color: var(--accent); text-decoration-color: var(--accent); }
```

Inside the EXISTING `@media (max-width: 700px)` block, in the `.rnd2 .acts` group near line 1995, extend the existing selector list rather than adding a rule: `.rnd2 .acts .act, .rnd2 .acts .done, .rnd2 .acts .reopen { ... }`. Do not open a new media query — `tests/test_theme_and_tokens.py` counts them.

- [ ] **Step 5: Add the toast key**

In `base.html`'s `TOAST_MSGS`, add after `"paid"`:

```jinja
      "cleared": _("Cleared — you can answer this round again"),
```

Extend the comment above it: the keys are `LotteryOutcome` values *plus* `cleared`, which is not one — the map is a lookup and an unmapped key already yields no toast.

- [ ] **Step 6: Translate**

Append to `src/app/translations/ja/LC_MESSAGES/messages.po`:

```
#: src/app/web/templates/_capture_actions.html
msgid "Change"
msgstr "変更"

#: src/app/web/templates/base.html
msgid "Cleared — you can answer this round again"
msgstr "取り消しました — このラウンドに再度回答できます"
```

and to `zh`:

```
#: src/app/web/templates/_capture_actions.html
msgid "Change"
msgstr "更改"

#: src/app/web/templates/base.html
msgid "Cleared — you can answer this round again"
msgstr "已清除 — 你可以重新回答这一轮"
```

The em dash must be the same character in msgid and msgstr as in the template. Copy the msgid from the template rather than retyping it.

- [ ] **Step 7: Run the tests and the gates**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: both clean, `test_i18n_catalogues.py` included.

- [ ] **Step 8: Commit**

```bash
git add src/app/web/templates src/app/web/static/style.css src/app/translations tests
git commit -m "feat(web): a quiet un-answer on settled round rows"
```

---

### Task 4: The confirmation for a secured record

**Files:**
- Modify: `src/app/web/templates/concert_detail.html` (dialog + script, after the calendar dialog near line 231)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: the concert-page test file from Task 3

**Read first:** `_following_toggle.html:57-97` for the `.prune` dialog shape, and `home.html:286-330` for the dataset-driven fill pattern. Note the difference: that dialog opens AFTER a write; this one gates one.

- [ ] **Step 1: Write the failing test**

```python
async def test_secured_round_carries_the_confirm_flag(client):
    # Mutation caught: gating the dialog on every clear (theatre on a loss) or
    # on none (a PAID record wiped by one press).
    html = (await client.get(f"/concerts/{event_id}")).text
    assert "data-clear-confirm" in html


async def test_lost_round_carries_no_confirm_flag(client):
    html = (await client.get(f"/concerts/{lost_event_id}")).text
    assert "data-clear-confirm" not in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_concert_page.py -q -k confirm`
Expected: FAIL.

- [ ] **Step 3: Implement the dialog**

Append to `concert_detail.html`, before the catch-up dialog include:

```jinja
{#- Clearing a ticket you hold. Only forms carrying data-clear-confirm are
    gated: a lost or skipped round forfeits nothing, and a confirmation there
    would be theatre.

    It must NOT borrow the unfollow dialog's wording. That one promises the won
    mark survives, because an opt-out forfeits the reminder and never the
    record. This is the first thing in the app that removes the record, and the
    copy has to say so.

    One dialog for the page, filled from the pressed form's dataset -- the round
    label is user-controlled text and never touches an inline on* handler
    (invariant 7). Backdrop-close comes only from base.html's global drag-safe
    handler; a local e.target === dlg handler is forbidden by a sweep test. -#}
<dialog class="prune" id="clearDlg">
  <div class="dh">{{ _("Clear your answer for this round?") }}
    <button type="button" data-close aria-label="{{ _('Close') }}">×</button></div>
  <p>
    {% trans %}You recorded a ticket for <b id="clearName">this round</b>. Clearing it removes that record, the payment reminder it is holding open, and the reason other rounds on this day are marked Covered. The round goes back to unrecorded and you can answer it again.{% endtrans %}
  </p>
  <div class="da">
    <button class="btn quiet" type="button" data-close>{{ _("Keep it") }}</button>
    <button class="btn danger" type="button" id="clearGo">{{ _("Clear my answer") }}</button>
  </div>
</dialog>

<script>
  // Gates the submit rather than following it. The form is a real form with a
  // method/action, so with JS off it posts straight through -- the correction
  // still works, only the confirmation is lost, which is the same trade every
  // onsubmit="return confirm(...)" in this codebase already makes.
  (function () {
    var dlg = document.getElementById("clearDlg");
    var pending = null;
    document.body.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form.dataset || !form.dataset.clearConfirm) return;
      if (form.dataset.clearConfirmed) return;   // second pass: let it through
      e.preventDefault();
      document.getElementById("clearName").textContent =
        form.dataset.clearLabel || "";
      pending = form;
      dlg.showModal();
    }, true);
    document.getElementById("clearGo").addEventListener("click", function () {
      dlg.close();
      if (!pending) return;
      // Mark, then re-submit: requestSubmit re-fires the submit event, which
      // the guard above now waves past, and htmx picks it up as normal.
      pending.dataset.clearConfirmed = "1";
      pending.requestSubmit();
      pending = null;
    });
    dlg.addEventListener("close", function () { pending = null; });
  })();
</script>
```

Check whether `data-close` is already wired globally in `base.html` (the prune dialog uses it); if it is not, add `onclick="this.closest('dialog').close()"` to those two buttons as the calendar dialog does at line 214.

A form swapped in by htmx is a new element, so the listener must be delegated on `document.body` — it is. The `true` capture flag matters: htmx also listens for submit, and the guard must run first.

- [ ] **Step 4: Translate**

ja:

```
msgid "Clear your answer for this round?"
msgstr "このラウンドの回答を取り消しますか？"

msgid "Keep it"
msgstr "そのままにする"

msgid "Clear my answer"
msgstr "回答を取り消す"
```

zh:

```
msgid "Clear your answer for this round?"
msgstr "要清除这一轮的回答吗？"

msgid "Keep it"
msgstr "保留"

msgid "Clear my answer"
msgstr "清除我的回答"
```

The `{% trans %}` body is one msgid containing `<b id="clearName">this round</b>` verbatim — extract it exactly as Babel sees it (run `uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .` and copy the msgid, then delete `messages.pot`, which is gitignored). Translations:

- ja: `このラウンドで<b id="clearName">チケット</b>を確保済みとして記録しています。取り消すと、その記録、そのために保持されている支払いリマインダー、そしてこの日の他のラウンドが「確保済み」と表示される理由がなくなります。ラウンドは未回答に戻り、再度回答できます。`
- zh: `你已记录在<b id="clearName">这一轮</b>拿到了票。清除后，该记录、为它保留的付款提醒，以及这一天其他轮次被标记为「已确保」的依据都会消失。该轮次会回到未回答状态，你可以重新回答。`

Keep the `<b id="clearName">…</b>` tag intact in both, with the id unchanged — the script writes into it.

- [ ] **Step 5: Verify it in a browser**

Do not skip this and do not reason about it. Start the app (`uv run --isolated python -m app.main` with `DISCORD_TOKEN` empty for web-only), open a concert with a PAID round, press Change, and confirm: the dialog opens, "Keep it" leaves the record intact, "Clear my answer" clears it and the row comes back with the entry buttons. Check both themes.

- [ ] **Step 6: Gates and commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src/app/web/templates/concert_detail.html src/app/translations tests
git commit -m "feat(web): confirm before clearing a ticket you hold"
```

---

### Task 5: Discord — an explicit backtrack, and a guard on the stale pair

**Files:**
- Modify: `src/app/bot/views.py` (`_handle_outcome_click` ~274, `WonButton`/`LostButton` ~327-367, `_progress_reply` ~561, the `PERSISTENT_ITEMS`/registration list ~810)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_bot_reminders.py` (or the bot view test file — find it with `rg "DynamicItem|from_custom_id" tests/`)

**Read first:** spec §E, and the comment block at `views.py:455-465` explaining why a stale press must re-derive.

- [ ] **Step 1: Write the failing tests**

```python
async def test_flat_lost_button_will_not_overwrite_a_won_round(db):
    # Mutation caught: the unguarded press that exists today -- a months-old DM
    # wiping a PAID ticket with no confirmation.
    ...
    assert await _outcome(s, round_id) is LotteryOutcome.PAID


async def test_backtrack_clears_an_unsecured_round_immediately(db):
    # Mutation caught: demanding confirmation for a loss, which makes the
    # common correction two presses.
    ...
    assert await _outcome(s, round_id) is None


async def test_backtrack_asks_before_clearing_a_secured_round(db):
    # Mutation caught: clearing a held ticket on one press.
    ...
    assert await _outcome(s, round_id) is LotteryOutcome.WON  # not yet cleared
```

Follow the file's existing pattern: call `Cog.command.callback(...)` / `Button.callback(...)` with a fake `discord.Interaction` exposing `.user.id`, `.user.name`, and async `.response.send_message` / `.response.edit_message` that record their args, and monkeypatch the module's `SessionMaker` to an in-memory engine. Assert on the DB and on the recorded reply, never on internals.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_bot_reminders.py -q -k "backtrack or overwrite"`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the button and its confirm view:

```python
class ClearOutcomeButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:clear:(?P<rid>\d+)"
):
    """The un-answer, in the DM where the mis-press happened.

    It rides on the REPLY to a press, never on the reminder itself -- which is
    why `domain/rehearsal.py:expected_buttons` needs no entry for it.

    Persistent, so it can be pressed months later against a round since settled
    on the site. It therefore re-derives state and never trusts the message it
    was pressed on, exactly as `_progressive_click` does, and asks first when
    the press would drop a ticket the reader holds. Clearing the WHOLE round is
    deliberate: a DM reply is one moment about one press, and per-leg surgery
    needs to see every leg at once, which is the page -- one tap away on the
    "Open on dekimasen.app" button every reminder carries.
    """

    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Change my answer"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:clear:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            await _apply_locale(session, interaction.user.id)
            outcome = await _round_outcome_value(
                session, interaction.user.id, self.round_id
            )
            if outcome in (LotteryOutcome.WON, LotteryOutcome.PAID):
                await session.commit()
                await interaction.response.edit_message(
                    content=_("You hold a ticket from this round. Clearing your answer "
                              "removes that record and its payment reminder. Clear it?"),
                    view=build_clear_confirm_view(self.round_id),
                )
                return
            await clear_round_outcome(session, interaction.user.id, self.round_id)
            await session.commit()
        await interaction.response.edit_message(
            content=_("Cleared — you can record this round again."), view=None
        )
```

Add a `ConfirmClearButton` on `dk:clearok:(?P<rid>\d+)` that calls `clear_round_outcome` unconditionally and edits to the same cleared message, and a `KeepAnswerButton` on `dk:keep:(?P<rid>\d+)` that writes nothing and edits to `_("Kept — nothing changed.")`. `build_clear_confirm_view(round_id)` returns a `discord.ui.View(timeout=None)` holding both.

`_round_outcome_value` and `clear_round_outcome` must be imported at the top of `views.py` from `app.db.service` — check whether `_round_outcome_value` is exported by the facade; if it is not, use `round_result_state` (already imported) and read its third element instead of reaching into `core`.

Guard the flat pair — replace `WonButton.callback` / `LostButton.callback` bodies with a shared helper:

```python
async def _handle_result_click(
    interaction: discord.Interaction, round_id: int, outcome: LotteryOutcome,
    success_msg: str,
) -> None:
    """The flat Won/Lost pair, with the guard `_apply_press` already applies to
    the all-legs shortcuts.

    These buttons are persistent, so this DM can be pressed long after the round
    was secured -- and won, on a settled round, demotes PAID while lost wipes the
    ticket outright. A settled win is not something a stale press may undo. It
    is no longer a dead end either: the reply carries the backtrack button, so
    the guard is a signpost rather than silence.
    """
    async with SessionMaker() as session:
        await _apply_locale(session, interaction.user.id)
        current = ...  # this user's outcome for round_id
        if current in (LotteryOutcome.WON, LotteryOutcome.PAID):
            await session.commit()
            await interaction.response.send_message(
                _("This round is already recorded as won. Use the button below to "
                  "change it."),
                view=build_backtrack_view(round_id),
            )
            return
        await record_round_outcome(session, interaction.user.id, round_id, outcome)
        await session.commit()
    await interaction.response.send_message(
        _(success_msg), view=build_backtrack_view(round_id)
    )
```

`build_backtrack_view(round_id)` returns a `View(timeout=None)` holding one `ClearOutcomeButton`. Attach it to `_progress_reply`'s three terminal returns too (the ones currently returning `view=None`), so every settled reply carries it.

Register `ClearOutcomeButton`, `ConfirmClearButton` and `KeepAnswerButton` in the persistent-items list near line 810 alongside the existing buttons, or `from_custom_id` will never fire after a restart.

- [ ] **Step 4: Translate**

ja: `Change my answer` → `回答を変更`; `Cleared — you can record this round again.` → `取り消しました — このラウンドを再度記録できます。`; `Kept — nothing changed.` → `そのままにしました — 変更はありません。`; the "already recorded as won" line → `このラウンドはすでに当選として記録されています。変更する場合は下のボタンを使ってください。`; the "You hold a ticket" line → `このラウンドのチケットを確保しています。回答を取り消すと、その記録と支払いリマインダーがなくなります。取り消しますか？`

zh: `更改回答`; `已清除 — 你可以重新记录这一轮。`; `保持不变 — 没有任何更改。`; `这一轮已记录为中签。如需更改，请使用下面的按钮。`; `你持有这一轮的票。清除回答会移除该记录及其付款提醒。要清除吗？`

Bot strings composed before the locale is set use `N_()` at the call site and `_()` at render — follow whichever the neighbouring button already does.

- [ ] **Step 5: Gates and commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src/app/bot/views.py src/app/translations tests
git commit -m "feat(bot): an explicit backtrack button, and guard the stale flat pair"
```

---

### Task 6: Docs, and driving it through Discord for real

**Files:**
- Modify: `docs/architecture.md` (the `db/core.py`, `web/routes/outcomes.py` and `bot/views.py` entries)
- Modify: `WISHLIST.md` (move the in-flight note to Shipped, then the full revision pass CLAUDE.md requires)

- [ ] **Step 1: Document the seams**

In `docs/architecture.md`, add to the relevant entries: `clear_round_outcome` is the only deletion path for `RoundOutcome`/`RoundOutcomeDay` and owns its own resync; the per-leg clear is offered only for a leg holding its own row, which is why no materialization appears on that path; the DM backtrack is whole-round by design.

- [ ] **Step 2: Drive it through the dev bot (owner-requested)**

Automated tests never touch the Discord gateway, so this is the only thing that proves the wiring. The local `DISCORD_TOKEN` is a throwaway bot with only the owner on it — running it is safe.

1. Run `uv run --isolated python -m app.main` with the real dev `DISCORD_TOKEN` and `DEV_GUILD_ID` set.
2. Open `/admin/rehearsal`, start a rehearsal, and step it to a RESULTS reminder.
3. In Discord: press **Won** → the reply must carry **Change my answer**.
4. Press **Change my answer** → it must ask first (the round is WON), and **Keep it** must leave the record intact.
5. Press it again, confirm → the round clears; check the concert page agrees.
6. Press **Lost** on the ORIGINAL reminder → it must now record LOST (the round is unrecorded again).
7. Press **Won**, then press **Lost** on that same old message → the guard must refuse and reply with the backtrack button.

Report exactly what each press produced. If any step differs, stop and report rather than patching around it.

- [ ] **Step 3: WISHLIST**

Move the in-flight note into Shipped with today's date and what shipped, note that it closes the 2026-08-04 irreversible-APPLIED entry, then do the full re-rank CLAUDE.md requires — including renumbering, which was deliberately deferred while this was in flight.

- [ ] **Step 4: Gates and commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add docs WISHLIST.md
git commit -m "docs: outcome correction -- architecture entries and wishlist"
```

---

## Self-review

**Spec coverage.** §A → Task 1 (both modes, all three re-derivation branches). §B → Task 2. §C → Task 3 (all four render branches, the "Nothing to do" replacement, the `correctable` default keeping Home identical). §D → Task 4. §E1 → Task 5 (backtrack on both reply paths, whole-round, re-derives). §E2 → Task 5 (flat guard). §F → tests in every task; §F1 → Task 6 Step 2. The spec's "what this does not do" list needs no task by construction.

**Known soft spots, called out rather than hidden.** Task 2's and Task 5's tests carry `...` for seeding and fake-interaction setup, because both files have established local helpers an implementer must read and reuse; inventing them here would produce a second fixture the codebase does not want. Every assertion in them is concrete. Task 3's test file name is not pinned — find it with `rg "concert-rounds" tests/`.

**Type consistency.** `clear_round_outcome(session, user_id, round_id, day_id=None, now=None)` is spelled identically in Tasks 1, 2 and 5. `correctable` is the macro parameter in Task 3 and nowhere else. `data-clear-confirm` / `data-clear-label` are written in Task 3 and read in Task 4. `build_backtrack_view` / `build_clear_confirm_view` are both defined and used within Task 5.
