# Coming-up De-crowding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse Home's "Coming up" from one row per anchor into one block per concert — led by the round that wants the viewer first, with the rest behind client-side folds — per `docs/superpowers/specs/2026-07-27-coming-up-decrowd-design.md`.

**Architecture:** A new grouping layer in `db/service.py` builds `ConcertBlock`s on top of the existing `my_deadline_rows` (no second derivation); the concert page's "which round wants me first" predicate is generalized so both surfaces share one rule. `_deadline_rows.html` becomes block-structured while keeping its `#deadline-rows` htmx target and its five-column member row. `upcoming_deadlines` and Discover are untouched.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, FastAPI + Jinja2 + htmx, babel gettext (ja/zh).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Run suites in the FOREGROUND. Accepted baseline on this machine: exactly 2 pre-existing env failures (`test_test_dm_when_bot_disabled`, `test_healthz`) — anything else is yours.
- Branch is `coming-up-decrowd` (stacked on `per-leg-outcomes`). Commit there; never switch branches.
- `upcoming_deadlines`, `my_deadline_rows`, Discover, the board, the concert page and the DM flow are OUT of scope — `my_deadline_rows` gains one field and nothing else.
- The outer `<div id="deadline-rows">` is the htmx swap target for `POST /rounds/{id}/outcome`; both render paths must emit the identical structure, and that route's `#board`/`#board-summary` out-of-band fragments stay untouched.
- Capture buttons render ONLY through the existing `capture_actions` macro with target `#deadline-rows` — do not re-decide which button shows when.
- New user-visible strings are `_()`/`{% trans %}`-wrapped with hand-filled ja and zh msgstrs, no fuzzy, plurals via `{% pluralize %}`; `tests/test_i18n_catalogues.py` enforces.
- CSS: every phone rule inside the single existing `@media (max-width: 700px)` section, every tablet rule inside the `701-1040px` band. No new top-level media queries — `test_theme_and_tokens.py` pins the count at 6. Radius 3px.
- Invariant 7: no user text in inline `on*` handlers; `| tojson` never `| safe`; never `data-name`.
- Commit messages as given, plus the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Service — shared lead rule, `ConcertBlock`, `my_deadline_blocks`

**Files:**
- Modify: `src/app/db/service.py` (`DeadlineRow`; `my_deadline_rows`; `_needs_you` near the concert-page section; new block layer beside `my_deadline_rows`)
- Test: `tests/test_home.py` (append), `tests/test_concert_rows.py` (the shared-rule agreement test)

**Interfaces:**
- Consumes: existing `my_deadline_rows`, `DeadlineRow`, `UpcomingDeadline`, `LotteryOutcome`, `DEADLINE_ROWS_LIMIT`.
- Produces (Tasks 2-3 consume verbatim):

```python
ANCHOR_FAN_OUT = 6      # anchors-per-concert headroom for the internal fetch
VISIBLE_BLOCKS = 6      # blocks rendered before the page-level fold

def _wants_you(outcome: LotteryOutcome | None, can_capture: bool,
               closes_at_utc: datetime | None, now: datetime) -> bool

@dataclass(frozen=True)
class ConcertBlock:
    event_id: str
    concert_title: str
    venue: str | None
    starts_at_utc: datetime | None
    lead: DeadlineRow
    others: tuple[DeadlineRow, ...]

async def my_deadline_blocks(
    session: AsyncSession, user_id: int, now: datetime | None = None,
    limit: int = DEADLINE_ROWS_LIMIT, concert_ids: set[int] | None = None,
) -> list[ConcertBlock]
```

`DeadlineRow` gains `closes_at_utc: datetime | None = None` (filled in `my_deadline_rows` from the round it already loaded — no new query).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_home.py`, reusing its fixtures/builders; adapt seeds to that file's helpers):

```python
async def test_blocks_collapse_a_round_to_its_soonest_anchor(session):
    # one round with closes + results + payment ahead -> ONE member row,
    # carrying the soonest anchor.
    ...
    blocks = await my_deadline_blocks(session, UID, now=NOW)
    assert len(blocks) == 1
    assert blocks[0].others == ()
    assert blocks[0].lead.deadline.anchor is Anchor.CLOSES


async def test_standing_beats_time_for_the_lead(session):
    # round A: WON, payment due 20 Aug. round B: no standing, opens tomorrow.
    # A leads even though B is sooner; B is in others.
    ...
    assert blocks[0].lead.deadline.round_id == round_a.id
    assert [r.deadline.round_id for r in blocks[0].others] == [round_b.id]


async def test_a_settled_round_never_leads(session):
    # round A: LOST (settled). round B: no standing, open now, later moment.
    # B leads despite being later.
    ...


async def test_others_stay_chronological(session): ...
async def test_blocks_sort_by_their_lead_moment(session): ...
async def test_block_cap_counts_concerts_not_rows(session):
    # 12 concerts x 3 anchor rows each -> exactly DEADLINE_ROWS_LIMIT blocks,
    # and the wider internal window actually fills them (regression against
    # truncate-then-group under-filling).
    ...
    assert len(blocks) == 10


async def test_event_start_only_concert_leads_with_the_show(session):
    # a concert whose only future row has no round_id
    assert blocks[0].lead.deadline.round_id is None
    assert blocks[0].others == ()
```

And in `tests/test_concert_rows.py`, the anti-drift test:

```python
async def test_the_lead_rule_matches_the_concert_pages_needs_you(session):
    """Both surfaces answer 'does this want me?' identically for the same
    inputs -- one rule, two row shapes."""
    now = NOW
    for outcome, can_capture, closes in [
        (None, True, now + timedelta(days=1)),
        (None, True, now - timedelta(days=1)),
        (None, False, None),
        (LotteryOutcome.APPLIED, True, None),
        (LotteryOutcome.WON, False, None),
        (LotteryOutcome.LOST, True, now + timedelta(days=1)),
        (LotteryOutcome.PAID, True, None),
        (LotteryOutcome.NOT_APPLIED, True, None),
    ]:
        round_ = Round(id=1, concert_id=1, kind=RoundKind.LOTTERY_ROUND,
                       label="x", closes_at_utc=closes)
        row = RoundRow(round_=round_, outcome=outcome, can_capture=can_capture,
                       can_report_result=False)
        assert _needs_you(row, now) == _wants_you(outcome, can_capture, closes, now)
```

- [ ] **Step 2: Run them** — `uv run pytest tests/test_home.py tests/test_concert_rows.py -q` — expect FAIL (ImportError on `my_deadline_blocks`/`_wants_you`).

- [ ] **Step 3: Implement.** Generalize the predicate where `_needs_you` lives:

```python
def _wants_you(outcome: LotteryOutcome | None, can_capture: bool,
               closes_at_utc: datetime | None, now: datetime) -> bool:
    """Does this round still want something from this reader?

    Primitives, not a row type, because TWO row shapes ask it: the concert
    page's RoundRow (via _needs_you) and Home's DeadlineRow (via the block
    lead). One rule, so the two surfaces cannot drift on what "wants me
    first" means.
    """
    if outcome in (LotteryOutcome.APPLIED, LotteryOutcome.WON):
        return True          # live standing: awaiting a result, or owing money
    if outcome is not None:
        return False         # LOST / PAID / NOT_APPLIED are settled
    return can_capture and (closes_at_utc is None or closes_at_utc > now)


def _needs_you(row: RoundRow, now: datetime) -> bool:
    return _wants_you(row.outcome, row.can_capture, row.round_.closes_at_utc, now)
```

(keep `_needs_you`'s existing docstring content on `_wants_you`.)

Then the block layer, beside `my_deadline_rows`:

```python
ANCHOR_FAN_OUT = 6
VISIBLE_BLOCKS = 6


@dataclass(frozen=True)
class ConcertBlock:
    """One concert's slice of "Coming up": the round that wants this reader
    first, plus the rest folded behind it. Built ON my_deadline_rows, never
    beside it -- the per-row decoration (gates, outcome, venue, covered and
    upgrade filtering) is exactly what a member line needs."""

    event_id: str
    concert_title: str
    venue: str | None
    starts_at_utc: datetime | None
    lead: DeadlineRow
    others: tuple[DeadlineRow, ...] = ()


async def my_deadline_blocks(
    session: AsyncSession, user_id: int, now: datetime | None = None,
    limit: int = DEADLINE_ROWS_LIMIT, concert_ids: set[int] | None = None,
) -> list[ConcertBlock]:
    """Home's "Coming up", grouped: one block per concert, capped at `limit`
    CONCERTS (not rows).

    Two collapses. Per ROUND: upcoming_deadlines emits one row per future
    anchor in chronological order, so keeping the FIRST row per round id is
    exactly the moment the concert page's _primary_anchor picks -- the two
    surfaces agree by construction rather than by a second rule. Per
    CONCERT: the remaining rows become one block.

    The internal fetch is `limit * ANCHOR_FAN_OUT` rows because
    my_upcoming_deadlines truncates BEFORE decoration: ten anchor rows can
    be two concerts, so grouping a limit-sized fetch would under-fill. The
    window bounds work; it is not a promise, exactly as today's truncation
    is not.
    """
    now = now or _now()
    rows = await my_deadline_rows(
        session, user_id, now=now, limit=limit * ANCHOR_FAN_OUT,
        concert_ids=concert_ids,
    )

    seen_rounds: set[int] = set()
    by_event: dict[str, list[DeadlineRow]] = {}
    for row in rows:
        round_id = row.deadline.round_id
        if round_id is not None:
            if round_id in seen_rounds:
                continue  # a later anchor of a round already represented
            seen_rounds.add(round_id)
        # A row with no round id is the show itself (one per leg): nothing to
        # collapse onto, so every one survives as its own member line.
        by_event.setdefault(row.deadline.event_id, []).append(row)

    blocks: list[ConcertBlock] = []
    for event_id, members in by_event.items():
        ordered = sorted(members, key=lambda r: (
            not _wants_you(r.outcome, r.can_capture, r.closes_at_utc, now),
            r.deadline.at_utc,
        ))
        lead = ordered[0]
        others = sorted(ordered[1:], key=lambda r: r.deadline.at_utc)
        blocks.append(ConcertBlock(
            event_id=event_id,
            concert_title=lead.deadline.concert_title,
            venue=lead.venue,
            starts_at_utc=lead.starts_at_utc,
            lead=lead,
            others=tuple(others),
        ))
    blocks.sort(key=lambda b: (b.lead.deadline.at_utc, b.event_id))
    return blocks[:limit]
```

Add `closes_at_utc: datetime | None = None` to `DeadlineRow` (documented as "the round's close, carried so the block lead rule can ask `_wants_you` without re-loading the round") and populate it in `my_deadline_rows` from the `rounds` dict already in scope.

- [ ] **Step 4: Run** the two files, then the FULL suite (foreground) + `uv run ruff check .` — expect PASS.

- [ ] **Step 5: Commit** — `feat: group Coming up into per-concert blocks (task 1)`

---

### Task 2: Template restructure, route wiring, catalogues

**Files:**
- Modify: `src/app/web/templates/_deadline_rows.html`
- Modify: `src/app/web/app.py` (home handler), `src/app/web/routes/outcomes.py` (`_outcome_response`)
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `.../zh/.../messages.po`
- Test: `tests/test_home.py`, `tests/test_outcome_routes.py`

**Interfaces:**
- Consumes: Task 1's `ConcertBlock`, `my_deadline_blocks`, `VISIBLE_BLOCKS`.
- Produces: template context keys `blocks: list[ConcertBlock]` and `visible_blocks: int`, set identically by BOTH render paths.

- [ ] **Step 1: Write the failing render tests** (adapt to each file's fixtures):

```python
# tests/test_home.py
async def test_home_renders_one_block_header_per_concert(client, db): ...
async def test_a_folded_round_is_present_but_collapsed(client, db):
    # the folded round's capture form is IN the DOM (reachable), inside
    # <details class="morerounds"> which carries no `open` attribute.
    ...
async def test_no_fold_link_for_a_single_round_concert(client, db):
    assert "morerounds" not in body
async def test_the_page_level_fold_appears_only_past_six_blocks(client, db): ...

# tests/test_outcome_routes.py
async def test_outcome_swap_returns_the_same_block_structure(client, db):
    # POST /rounds/{id}/outcome from Home returns a fragment whose root is
    # <div ... id="deadline-rows">, contains a block header, and still
    # carries the two out-of-band fragments.
    ...
```

- [ ] **Step 2: Run** — expect FAIL.

- [ ] **Step 3: Restructure `_deadline_rows.html`.** Keep the file's existing header comment (why buttons live here) and extend it with the block rationale. Keep `status_pill` and the `capture_actions` import as they are. New shape:

```jinja
{% macro member_row(row, tz) -%}
{% set d = row.deadline %}
<div class="row">
  <span>{% if d.round_id %}{{ status_pill(row.outcome) }}{% endif %}</span>
  <span class="when-c num">{% set wl = dual_lines(d.at_utc, tz) %}<b>{{ wl[0] }}</b>{{ wl[1] }}</span>
  {# The concert title moved to the block header, so this cell now names the
     ROUND. Five cells either way -- the desktop grid is unchanged. #}
  <span class="title-c">
    {{ loc(d, "label") if d.round_id else d.label }}
    <small data-happens="{{ d.label }} {{ deadline_label(d.anchor) }}"></small>
  </span>
  <span class="act-c"><b>{{ d.label }}</b> {{ deadline_label(d.anchor) }}</span>
  <span class="acts">
    {% if d.round_id %}
      {{ capture_actions(row, d.round_id, "#deadline-rows",
                         prune_title=row.deadline.concert_title,
                         prune_url="/concerts/" ~ d.event_id,
                         is_upgrade=row.is_upgrade) }}
    {% endif %}
  </span>
</div>
{%- endmacro %}

{% macro concert_block(block, tz) -%}
<div class="cblock">
  <div class="blockhead">
    <a href="/concerts/{{ block.event_id }}">{{ block.concert_title }}</a>
    <small>{% if block.venue %}📍 {{ block.venue }}{% endif %}{% if block.venue and block.starts_at_utc %} · {% endif %}{% if block.starts_at_utc %}{{ day_month(block.starts_at_utc) }}{% endif %}</small>
  </div>
  {{ member_row(block.lead, tz) }}
  {% if block.others %}
  <details class="morerounds">
    <summary>{% trans count=block.others|length %}+{{ count }} more round{% pluralize %}+{{ count }} more rounds{% endtrans %}</summary>
    {% for row in block.others %}{{ member_row(row, tz) }}{% endfor %}
  </details>
  {% endif %}
</div>
{%- endmacro %}

<div class="deadline-rows" id="deadline-rows">
  <div class="rowhead">
    <span>{{ _("Your status") }}</span><span>{{ _("Closes") }}</span><span>{{ _("Round") }}</span>
    <span>{{ _("What happens") }}</span><span>{{ _("You") }}</span>
  </div>
  {% for block in blocks[:visible_blocks] %}{{ concert_block(block, tz) }}
  {% else %}
  <p class="dim">{{ _("Nothing coming up on the events you follow.") }}</p>
  {% endfor %}
  {% set overflow = blocks[visible_blocks:] %}
  {% if overflow %}
  <details class="moreconcerts">
    <summary>{% trans count=overflow|length %}+{{ count }} more concert{% pluralize %}+{{ count }} more concerts{% endtrans %}</summary>
    {% for block in overflow %}{{ concert_block(block, tz) }}{% endfor %}
  </details>
  {% endif %}
</div>
```

If `loc(d, "label")` does not apply (the label on `UpcomingDeadline` is already locale-resolved at build time — check `upcoming_deadlines`), use `d.label` plainly and note it in your report. Do NOT add a second localization site.

- [ ] **Step 4: Wire both routes.** In `web/app.py`'s home handler and `web/routes/outcomes.py`'s `_outcome_response`, swap `my_deadline_rows(...)` for `my_deadline_blocks(...)` and put `"blocks": blocks, "visible_blocks": VISIBLE_BLOCKS` in the context in place of `"rows"`. Import `VISIBLE_BLOCKS` from `app.db.service` in both — never a literal 6 — and extend the existing comment in each about the shared `DEADLINE_ROWS_LIMIT` default to cover it.

- [ ] **Step 5: Catalogues.** `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .`, `pybabel update` for ja and zh, hand-fill the new msgids (`Round`, and both plural pairs — plural forms must survive intact), delete `messages.pot`.

- [ ] **Step 6: Run** the two test files plus `tests/test_i18n_catalogues.py`, then the FULL suite + ruff.

- [ ] **Step 7: Commit** — `feat: Coming up renders concert blocks with folds (task 2)`

---

### Task 3: Desktop, tablet and phone styling

**Files:**
- Modify: `src/app/web/static/style.css`
- Test: `tests/test_theme_and_tokens.py` (guards must still pass), `tests/test_mobile.py` (append)

**Interfaces:** consumes Task 2's classes `.cblock`, `.blockhead`, `details.morerounds`, `details.moreconcerts`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_mobile.py`, matching its style):

```python
def test_phone_section_styles_the_concert_blocks():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    phone = css.split("/* ---- phone")[1]      # use this file's existing splitter
    assert ".cblock" in phone
```

- [ ] **Step 2: Run** — expect FAIL.

- [ ] **Step 3: Style.** Desktop (in the main body of the file, near the existing `.deadline-rows`/`.row` rules): `.cblock` spacing and a hairline separator between blocks; `.blockhead` as a title line (link at the existing row title weight, `small` in `--dim`); `summary` styled as a quiet inline affordance (cursor pointer, `--dim`, no default marker fuss beyond what the repo's other `details` do — match `.kebab`/`.fsheet` conventions), radius 3px, no new tokens.
  Tablet band (`701-1040px`): confirm `data-happens` still folds the what-happens cell into the member line — the attribute now sits on an empty `small`, so verify the `::after` rule still has something to attach to; adjust INSIDE the band only.
  Phone (`max-width: 700px`): block header as the card title row, member lines keeping the existing bordered-card treatment and full-width 44px actions, folds full-width and thumb-sized.
  **Measure, don't reason** (repo rule): seed a dev DB, open Home at 375/730/1200px, and check the block header, both folds, and a 3-round block before finalizing values. Report what you measured.

- [ ] **Step 4: Run** `tests/test_mobile.py tests/test_theme_and_tokens.py tests/test_home.py`, then the FULL suite + ruff. The breakpoint-count guard must still pass (no new top-level queries).

- [ ] **Step 5: Commit** — `feat: style the Coming up blocks across the three widths (task 3)`

---

### Task 4: Closing sweep

**Files:** `docs/superpowers/specs/2026-07-27-coming-up-decrowd-design.md`, `WISHLIST.md`, `CLAUDE.md`

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) + `uv run ruff check .`; record exact tallies.
- [ ] **Step 2:** Smoke in web-only dev mode against a seeded temp DB (never the repo's `app.db`): a concert with 3 upcoming rounds renders one block with a "+2 more rounds" fold; a capture press inside the fold swaps correctly and the fold's state after swap is sane; 8 tracked concerts produce the "+2 more concerts" fold.
- [ ] **Step 3:** Spec Status line → implemented (2026-07-27), plus an "Implementation deviations (recorded)" section if any arose.
- [ ] **Step 4:** WISHLIST: move Proposed #1 to Shipped (dated, house style, naming the block shape, the shared `_wants_you` rule, and the 6-of-10 fold), renumber the remaining Proposed, add the revision-pass paragraph, fix any `#N` cross-references.
- [ ] **Step 5:** CLAUDE.md UI conventions: amend the "Home vs Discover" paragraph with 2-3 sentences — Coming up is per-concert blocks led by `_wants_you` (the same rule the concert page's "Next for you" uses), folds are `details`, and Discover's flat deadline list is deliberately NOT grouped.
- [ ] **Step 6: Commit** — `chore: Coming up de-crowding closing sweep (task 4)`
