# Round poll phase 2 — the draft page and the apply path

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a stored proposal into a real round on a live concert —
reviewed field by field, with the model's quoted source line beside each value
— and surface (never apply) a round whose dates the page says have moved.

**Architecture:** The judgement stays pure in `domain/round_proposals.py`,
which learns a third verdict. Storage widens so a proposal can reconstruct a
whole round. The apply route becomes the **fourth caller** of the editor's own
`build_round`/`apply_round_fields` seam and is followed by `sync_concert`.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async, SQLite + Alembic,
Jinja2, pytest-asyncio auto mode.

Spec: `docs/superpowers/specs/2026-08-14-round-poll-phase-2-design.md`.
Phase 1 shipped as PR #156.

## Global Constraints

- **The code in this plan is UNVERIFIED.** It was written from reading the
  repo, not running it. Phase 1's plan was wrong **eleven times**, including
  one claim ("the resume cursor is free") that was flatly false and would have
  starved the queue. Check every name, signature and import against the real
  file. **If a snippet contradicts the repo, the repo wins** — implement what
  is correct and report the discrepancy rather than bending the repo to match.
- **Always `uv run --isolated`**; never `uv sync` (an external process holds a
  lock on `.venv`).
- **Do NOT run the full suite.** Measured 2026-08-14: 2964 tests, ~330-650s,
  against a Bash tool `timeout` maximum of **600000** — a larger value is
  silently capped, and the run is moved to the background, which has stalled
  implementers on this project. Run **targeted test files in the foreground**
  with `timeout: 600000` plus `uv run --isolated ruff check .`, then hand back.
  **Never report a full-suite number you did not personally see.** The
  controller confirms the suite between tasks.
- Baseline at branch point: **2964 passing**, ruff clean.
- `src/app/domain/` is pure: **no discord, fastapi or sqlalchemy imports, and
  no `app.db`.**
- `src/app/db/` feature modules import `core`, **never** the facade
  (`db/service.py`) — that is a cycle. Every new public name must be added to
  `service.py` or `tests/test_service_facade.py` fails.
- **Invariant 1:** aware UTC only; `UTCDateTime` rejects naive datetimes. Web
  pages render times **dual** via `dual_lines`/`fmt_dual_lines`, never a bare
  JST. `fmt_dual` is Discord-only.
- **Invariant 2:** `reminder_queue` is a materialized outbox. Any edit to
  rounds must call the relevant `sync_*`.
- **Invariant 5:** `require_admin`; signed in and unauthorized is **403**.
- **Invariant 6:** URLs use the editor-chosen `event_id`, not `Concert.id`.
- **Invariant 7:** no user- or model-controlled text in an inline `on*`
  handler — use `data-` attributes read via `dataset`. `label`,
  `evidence_yaml` and every proposed value are LLM-authored.
- **Migrations:** after `alembic revision --autogenerate`, replace every
  `app.db.models.UTCDateTime()` with `sa.DateTime()`. There is **no**
  `import app.db.models` line to delete — Alembic never generates one (CLAUDE.md
  was corrected on this point 2026-08-13). Keep the NAMING_CONVENTION; name any
  constraint explicitly.
- Admin pages in this repo are deliberately **English-only** and not wrapped in
  `_()` — `{{ _(` appears zero times in any `admin_*.html`. Match that; add no
  msgids.
- UI: sentence case; radiuses only 3px / 999px / 4px / 50% (sweep test);
  `.edgecard` and `.banner` are the only callout shapes; `.tagtable` like the
  sibling admin pages.
- Commit trailers, exactly:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```
- **Test discipline:** for every assertion, name the single edit that would
  make the feature wrong while leaving it green. If you can name one, the test
  is not finished. Phase 1 caught five tests that could not fail; the recurring
  cause was a fixture too small to distinguish what was asserted.

## File structure

| File | Responsibility |
| --- | --- |
| `src/app/db/models.py` (modify) | Three columns on `RoundProposal`. |
| `alembic/versions/<rev>_proposal_full_round.py` (new) | Those columns. |
| `src/app/db/round_proposals.py` (modify) | `upsert_proposal` widens; a loader for one concert's pending proposals. |
| `src/app/domain/round_proposals.py` (modify) | PURE. `HeldRound` widens; `classify_proposals` replaces `new_proposals`. |
| `src/app/round_poll.py` (modify) | Persist the three fields; store CHANGED; digest reports both. |
| `src/app/web/routes/quiet_ladders.py` (modify) | The draft page, apply, dismiss. |
| `src/app/web/templates/admin_round_proposal_draft.html` (new) | The forms. |

---

### Task 1: storage widens, and the writer stops dropping what it parsed

**Files:**
- Modify: `src/app/db/models.py`, `src/app/db/round_proposals.py`, `src/app/round_poll.py`
- Create: `alembic/versions/<generated>_proposal_full_round.py`
- Test: `tests/test_round_proposals_db.py`, `tests/test_round_poll.py`

**Interfaces:**
- Produces: `RoundProposal.results_at_utc`, `.payment_deadline_at_utc`,
  `.applies_to_labels` (JSON list of leg-label strings, `[]` when the model
  named none); `upsert_proposal(..., results_at_utc, payment_deadline_at_utc,
  applies_to_labels, ...)`.

**Why:** `domain/round_completion.py`'s prompt asks the model for
`applies_to`, `apply_opens_jst`, `apply_closes_jst`, `results_jst` and
`payment_deadline_jst`. `upsert_proposal` persists four fields and drops the
other three **after** `verify_rounds` has already grounded them. Results and
payment are two of the anchors this app exists to remind people about.

- [ ] **Step 1: Read what already exists**

`src/app/domain/round_proposals.py` defines `OPENS_AT_FIELD =
"apply_opens_jst"`, `CLOSES_AT_FIELD = "apply_closes_jst"` and
`proposed_stamp_utc(proposed, field)`. Read them. You will add two more field
constants beside them; **do not write a second parser.**

- [ ] **Step 2: Add the field constants**

In `src/app/domain/round_proposals.py`, beside the existing two:

```python
RESULTS_AT_FIELD = "results_jst"
PAYMENT_AT_FIELD = "payment_deadline_jst"
APPLIES_TO_FIELD = "applies_to"
```

- [ ] **Step 3: Add the columns**

On `RoundProposal` in `models.py`:

```python
    results_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    payment_deadline_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The leg LABELS the model named, verbatim -- not ConcertDay ids. The
    # model reads a page, not our database, so it can only ever name labels;
    # the draft page maps them to legs at render time and shows one that
    # matches nothing as unmatched rather than dropping it.
    applies_to_labels: Mapped[list] = mapped_column(JSON, default=list)
```

- [ ] **Step 4: Generate and hand-edit the migration**

```bash
uv run --isolated alembic revision --autogenerate -m "proposal carries a whole round"
```

Replace every `app.db.models.UTCDateTime()` with `sa.DateTime()`. There is no
`import app.db.models` line to delete. Then `uv run --isolated alembic upgrade
head`.

- [ ] **Step 5: Write the failing tests**

```python
async def test_the_three_new_fields_round_trip(session):
    """Mutation: dropping any one from upsert_proposal's write. Assert all
    three separately -- asserting a single one lets the other two be dropped."""


async def test_a_refresh_overwrites_the_new_fields_too(session):
    """Today's reading wins on every field except first_seen_at. Mutation:
    setting the new fields only on INSERT, so a corrected results date never
    reaches an already-recorded proposal."""


async def test_applies_to_labels_defaults_to_empty_not_null(session):
    """Empty means ALL legs, and the page ticks every box for it. Mutation:
    nullable with no default -- the template then branches on None somewhere
    and the convention has two spellings."""
```

Then in `tests/test_round_poll.py`:

```python
async def test_the_poll_persists_results_payment_and_legs(session):
    """The whole point of the task. Mutation: reverting round_poll.py to pass
    only label/kind/opens/closes -- which is exactly what shipped in phase 1,
    so this test is what stops a revert."""
```

- [ ] **Step 6: Run red, implement, run green**

Widen `upsert_proposal`'s keyword arguments and its update branch, then in
`round_poll.py` pass `proposed_stamp_utc(candidate, RESULTS_AT_FIELD)`,
`proposed_stamp_utc(candidate, PAYMENT_AT_FIELD)`, and the raw
`candidate.data.get(APPLIES_TO_FIELD) or []` — read the real `ProposedRound`
to confirm `data` is the attribute holding it.

- [ ] **Step 7: Facade, mutations, targeted tests, lint, commit**

Add nothing new to `db/service.py` unless you added a public name; run
`tests/test_service_facade.py` either way.

---

### Task 2: the third verdict, pure

**Files:**
- Modify: `src/app/domain/round_proposals.py`
- Test: `tests/test_round_proposals_domain.py`

**Interfaces:**
- Consumes: Task 1's field constants.
- Produces:
  ```python
  @dataclass(frozen=True)
  class HeldRound:
      label: str
      opens_at_utc: datetime | None
      closes_at_utc: datetime | None = None
      results_at_utc: datetime | None = None
      payment_deadline_at_utc: datetime | None = None

  @dataclass(frozen=True)
  class Classified:
      fresh: list[ProposedRound]     # the concert has no round with this key
      changed: list[ProposedRound]   # same key, some other field disagrees

  def classify_proposals(
      existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]
  ) -> Classified
  ```

**Delete `new_proposals` and fold its tests into the new ones.** Keeping both
leaves two functions answering one question, which is how a diff and its caller
drift apart.

`applies_to` is deliberately **not** compared: the model names leg *labels* and
a `HeldRound` would have to carry the concert's own labels to compare them,
which drags leg identity into a pure module for a field the operator re-picks
on the draft page anyway. Say so in the docstring so the omission reads as a
decision.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_identical_round_is_neither_fresh_nor_changed(make_proposed):
    """Mutation: returning everything as fresh. The pass would re-propose every
    round the concert already holds, every day."""


def test_a_moved_closing_date_is_CHANGED_not_dropped(make_proposed):
    """The case the phase exists for: a concert is quiet precisely because its
    stored closes is past. Mutation: comparing only opens -- which is exactly
    what dedupe_key does, and what shipped in phase 1."""


def test_a_moved_results_date_alone_is_CHANGED(make_proposed):
    """Mutation: comparing closes but not results. Seed closes IDENTICAL so
    this can only pass by comparing results."""


def test_a_moved_payment_deadline_alone_is_CHANGED(make_proposed):
    """Mutation: comparing results but not payment. Same shape: every other
    field identical."""


def test_a_genuinely_new_round_is_FRESH(make_proposed):
    """Mutation: returning [] for fresh unconditionally."""


def test_order_is_preserved_within_each_bucket(make_proposed):
    """Mutation: building the buckets from a set. The digest and the draft page
    both read these in order, and a set makes that order arbitrary per run."""
```

- [ ] **Step 2: Run red**

Expected: `ImportError: cannot import name 'classify_proposals'`.

- [ ] **Step 3: Implement**

```python
def classify_proposals(
    existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]
) -> Classified:
    """Split `proposed` into genuinely new rounds and changed ones.

    A round is CHANGED when the concert holds one with the same dedupe key --
    same label, same opening minute -- but some other timestamp disagrees. That
    is the case this whole feature is for: a concert is quiet precisely because
    its stored deadlines are in the past, so a postponed closing date is the
    likeliest true find, and `dedupe_key` alone would discard it as "held".

    `applies_to` is deliberately NOT compared. The model names leg LABELS off a
    page; comparing them would drag leg identity into a pure module, for a
    field the operator re-picks on the draft page anyway.
    """
    held = {dedupe_key(r.label, r.opens_at_utc): r for r in existing}
    fresh: list[ProposedRound] = []
    changed: list[ProposedRound] = []
    for p in proposed:
        match = held.get(dedupe_key(p.label, proposed_stamp_utc(p, OPENS_AT_FIELD)))
        if match is None:
            fresh.append(p)
        elif _differs(match, p):
            changed.append(p)
    return Classified(fresh=fresh, changed=changed)


def _differs(held: HeldRound, proposed: ProposedRound) -> bool:
    for field, stored in (
        (CLOSES_AT_FIELD, held.closes_at_utc),
        (RESULTS_AT_FIELD, held.results_at_utc),
        (PAYMENT_AT_FIELD, held.payment_deadline_at_utc),
    ):
        if proposed_stamp_utc(proposed, field) != stored:
            return True
    return False
```

- [ ] **Step 4: Run green; apply every named mutation, reverting cleanly between each**

- [ ] **Step 5: Lint, commit**

---

### Task 3: the pass stores changes, and the digest says so

**Files:**
- Modify: `src/app/round_poll.py`
- Test: `tests/test_round_poll.py`, `tests/test_round_poll_scheduler.py`

**Interfaces:**
- Consumes: Task 2's `classify_proposals`, `Classified`, widened `HeldRound`.
- Produces: `PollReport.changed_proposals: int`.

**Read first:** `round_poll.py`'s `_poll_one` builds `held` from `row.rounds`
(a `QuietLadder` carries the concert's existing rounds so a re-check does not
re-propose them). It currently adapts only label and opens; it must now adapt
closes, results and payment too. Read `db/quiet_ladders.py`'s `QuietRound` for
the real attribute names before assuming them.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_moved_closing_date_is_stored_as_a_proposal(session):
    """Mutation: dropping `changed` from what gets upserted -- the pass would
    find the postponement and throw it away, which is phase 1's behaviour."""


async def test_a_changed_proposal_counts_separately_from_a_new_one(session):
    """Mutation: summing them into new_proposals. Seed ONE of each so the two
    numbers differ; equal counts make a swap invisible."""


async def test_an_identical_round_still_produces_nothing(session):
    """The regression guard on Task 2's HELD bucket."""
```

And in the scheduler tests:

```python
async def test_the_digest_names_changes_apart_from_new_rounds(session):
    """Only new rounds are approvable on the draft page. Mutation: one combined
    line -- the operator then opens the page expecting an Approve button that
    is deliberately absent for half the rows."""
```

- [ ] **Step 2-4: Run red, implement, run green**

Both buckets are upserted; `report.new_proposals` counts inserts among
`fresh` (`first_seen_at == now`, as phase 1 established), and
`report.changed_proposals` counts the `changed` bucket. Keep
`_fold_duplicate_keys` in front of both.

- [ ] **Step 5: Mutations, targeted tests, lint, commit**

---

### Task 4: the draft page

**Files:**
- Modify: `src/app/web/routes/quiet_ladders.py`, `src/app/db/round_proposals.py`
- Create: `src/app/web/templates/admin_round_proposal_draft.html`
- Test: `tests/test_admin_round_proposal_draft.py`

**Interfaces:**
- Produces: `GET /admin/quiet-ladders/proposals/{event_id}`;
  `pending_proposals_for(session, concert_id) -> list[RoundProposal]` in the
  DB module, re-exported from the facade.

**Read first:** `admin_round_proposals.html` (the phase-1 list) and
`_editor_round_card.html` (the editor's round form). Match their shapes rather
than inventing a third.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_draft_page_renders_for_an_admin(client):
    """Every page needs one logged-in GET render test -- a missing one shipped
    a 500 on this repo once, from template context drift."""


async def test_a_non_admin_gets_403(client):
    """Invariant 5. The fixture must sign in as a real non-admin, or a
    signed-out 403 would pass this while the check was missing."""


async def test_each_field_is_pre_filled_with_the_models_value(client):
    """Mutation: rendering empty inputs. Scope the assertion to the form for
    THIS proposal -- base.html's nav and tab bar have made a page-wide
    assertion pass with the whole feature deleted on this repo."""


async def test_every_field_shows_its_quoted_source_line(client):
    """The reason the page exists: without the quote an operator cannot check
    the claim. Mutation: rendering values without evidence."""


async def test_a_CHANGED_proposal_shows_stored_beside_proposed_and_no_apply(client):
    """Mutation: rendering it like a new one. Assert BOTH that the stored value
    appears AND that no apply control does -- either alone passes while the
    other half is wrong."""


async def test_every_leg_box_is_ticked_when_the_model_named_none(client):
    """Empty means ALL. Mutation: rendering none ticked, which reads as a round
    applying to nothing. Seed TWO legs so 'all' and 'the first' differ."""


async def test_a_leg_label_matching_nothing_is_shown_as_unmatched(client):
    """Mutation: dropping it silently -- the operator then cannot tell the
    model read a leg this concert does not have."""


async def test_a_concert_with_no_pending_proposals_renders_an_empty_state(client):
    """A link in a days-old digest DM must land somewhere sensible.
    Mutation: 404ing."""
```

- [ ] **Step 2-4: Run red, implement, run green**

Times render **dual** (`dual_lines`). Values ride in inputs; nothing
model-authored goes into an inline `on*` handler. `source_url` keeps phase 1's
`clean_url` treatment.

- [ ] **Step 5: Mutations, targeted tests, lint, commit**

---

### Task 5: apply and dismiss

**Files:**
- Modify: `src/app/web/routes/quiet_ladders.py`, `src/app/db/round_proposals.py`
- Test: `tests/test_admin_round_proposal_apply.py`

**Interfaces:**
- Produces: `POST /admin/quiet-ladders/proposals/{event_id}/{proposal_id}/apply`
  and `.../dismiss`; `mark_proposal_applied(session, proposal_id, now)`.

**This is the riskiest task in the plan.** It writes a round onto a concert
people already hold reminders for.

**Read first, and use them:** `web/routes/concerts.py`'s `build_round(...)`
— its docstring names its callers ("the rich creation form, the edit page's
new rows, and the URL-import commit route"); yours is the fourth — and
`apply_round_fields(round_, label, kind, opens_at, closes_at, results_at,
payment_at, url, applies_to, label_en, notes, label_zh)`, which takes its
timestamps as **strings** and does its own JST parsing. Hand it form values,
exactly as the editor does. Also `parse_round_legs(value, valid_day_ids,
key_to_day_id)` for the leg chips — do not write a second parser.

- [ ] **Step 1: Write the failing tests**

```python
async def test_applying_a_proposal_creates_the_round(session, client):
    """The happy path."""


async def test_applying_a_proposal_populates_the_reminder_queue(session, client):
    """THE most important check in this plan. Invariant 2: reminder_queue is a
    materialized outbox. Mutation: deleting the `sync_concert` call -- a test
    asserting only that a Round row exists passes while the reminder is
    silently never scheduled, which is this feature's own failure mode
    reintroduced by its fix. Seed a deadline far enough in the future that a
    row is genuinely due."""


async def test_applying_stamps_applied_at_and_the_proposal_leaves_pending(session, client):
    """Mutation: creating the round without stamping -- the proposal then
    reappears on the page forever and a second press creates a duplicate."""


async def test_every_leg_ticked_is_stored_as_EMPTY_applies_to(session, client):
    """The empty-means-all convention. Mutation: storing the explicit id list.
    A leg added later would silently fall outside a frozen array. Seed two
    legs; assert `round.applies_to` is falsy, not that it equals both ids."""


async def test_a_subset_of_legs_is_stored_verbatim(session, client):
    """The other half. Without it, 'always store empty' passes the test above."""


async def test_applying_a_CHANGED_proposal_is_REFUSED_by_the_route(session, client):
    """A hidden button is not an authorisation check. Mutation: relying on the
    template alone. POST it directly and assert both the refusal AND that no
    Round was created."""


async def test_a_non_admin_cannot_apply(session, client):
    """Invariant 5, on the one route that writes."""


async def test_dismissing_sets_dismissed_at_and_writes_no_round(session, client):
    """Mutation: dismiss falling through to apply. Assert both halves."""


async def test_an_edited_value_is_what_gets_written(session, client):
    """The reason fields are editable. Submit a corrected closing time and
    assert the ROUND carries it, not the model's original. Mutation: reading
    the proposal row instead of the form."""
```

- [ ] **Step 2-4: Run red, implement, run green**

Refuse a CHANGED proposal in the **route**, re-deriving change-ness from the
concert's live rounds — never from a stored flag.

- [ ] **Step 5: Mutations, targeted tests, lint, commit**

---

### Task 6: documentation

**Files:** `docs/architecture.md`, `WISHLIST.md`, `README.md`,
`docs/superpowers/specs/2026-08-13-round-poll-design.md`

- [ ] **Step 1: `docs/architecture.md`**

Under the round poll's entry, written for that file's stated purpose — which
reasonable-looking edits would undo a measurement or re-open an incident:

- why change-ness is **derived at render time, never stored** (a proposal fixed
  by hand resolves itself; there is no flag to drift);
- why a CHANGED proposal is refused **in the route** and not merely hidden;
- why every-leg-ticked normalises back to **empty** `applies_to`;
- that the apply path is the fourth caller of `build_round` and that
  `sync_concert` is what makes the deadline real.

- [ ] **Step 2: `WISHLIST.md`**

Entry #2 covers two shapes. The **large one is now complete**; the small one (a
round-gap dimension on the discovery matcher) is still unstarted and catches a
case this cannot — a concert holding a future anchor is never quiet. So: move
the large shape's story to Shipped, dated, and leave a Proposed entry for the
small shape alone, re-ranked on its own merits rather than inheriting #2's
rank. Then the full revision pass CLAUDE.md requires.

Close the parts of #24 this delivered (the moved-closing-time gap is now
surfaced; the hand-added-round staleness resolves itself) and leave what
remains.

- [ ] **Step 3: `README.md`**

One line on the "Shipped since Phase 12" list per CLAUDE.md's "Feature
wishlist, and the two files a shipped feature updates" — what an operator can
now do. Phase 1's line said "phase 1 of 2"; this completes it, so either amend
that line or add one that closes it. Do not leave the README claiming a
half-built feature.

- [ ] **Step 4: mark the phase-1 spec's phase-2 section as delivered**

`docs/superpowers/specs/2026-08-13-round-poll-design.md` describes phase 2 in
the future tense. Add a dated pointer to the phase-2 spec rather than rewriting
it — the record should show what was believed then.

- [ ] **Step 5: ruff, commit**

---

## Self-review notes

**Spec coverage.** Widened storage → Task 1. The third verdict → Task 2.
Storing changes and the digest → Task 3. The draft page, legs, evidence,
empty state → Task 4. Apply, dismiss, the CHANGED refusal, the empty-means-all
normalisation → Task 5. Docs → Task 6. The spec's "out of scope" list is
untouched by every task.

**The riskiest task is 5.** Tasks 1-4 fail loudly; Task 5 writes a round onto a
concert with live reminders, and its worst failure — a round created without
`sync_concert` — is silent, looks exactly like success, and is precisely the
failure this feature exists to prevent.

**Ordering.** 1 → 2 (the verdict compares fields Task 1 stores) → 3 (the pass
consumes the verdict) → 4 (the page reads what 1 and 3 wrote) → 5 (apply needs
the page's form) → 6.
