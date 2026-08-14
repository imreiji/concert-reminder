# Round poll, phase 1 — the pass finds rounds and tells you

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily, flag-gated pass that re-reads each quiet concert's own
official page with DeepSeek and records the rounds it appears to have grown, as
reviewable proposals — writing nothing a user can see.

**Architecture:** `src/app/round_poll.py` is run order only and sits ABOVE
`db/`, exactly like `discovery.py`/`triage.py`/`draft_completion.py`. The
judgement is pure and DB-free in `domain/round_proposals.py`. The candidate
list, the host-approval queue, the prompt and the evidence rule are all
EXISTING machinery this reuses rather than reimplements.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async, SQLite + Alembic,
Jinja2, gettext (en/ja/zh), pytest-asyncio auto mode.

Spec: `docs/superpowers/specs/2026-08-13-round-poll-design.md`. Phase 2 (the
draft page and the per-round apply onto a live concert) is a SEPARATE plan and
is out of scope here.

## Global Constraints

- **The code in this plan is UNVERIFIED.** It was written from reading the
  repo, not from running it. Treat every snippet as a sketch of intent, not as
  correct code: check names, signatures and imports against the real files
  before using them, and if a snippet contradicts the repo, the repo wins —
  say so in your report rather than making the repo match the plan.
- **Always `uv run --isolated`**; never `uv sync` (an external process holds a
  lock on `.venv`).
- **Run tests in the FOREGROUND with `timeout: 900000`.** Backgrounding has
  stalled implementers on this project.
- Baseline at branch point: **2903 passing**. `uv run --isolated ruff check .`
  must be clean.
- `src/app/domain/` is pure: **no discord, fastapi or sqlalchemy imports, ever.**
- `src/app/db/` feature modules import `core`, **never** the facade
  (`db/service.py`) — that is a cycle. Every new public name must be added to
  `service.py` or `tests/test_service_facade.py` fails.
- `round_poll.py` imports `domain/`, `app.llm`, `app.fetching` and
  `db.service`; nothing in `db/` may import it.
- **Invariant 1:** the DB stores aware UTC only; `UTCDateTime` rejects naive
  datetimes. Never store or compare a naive datetime.
- **Invariant 4:** notifications go through the `notifications` outbox, never
  sent directly from a pass or a route.
- **Migrations:** after `alembic revision --autogenerate`, ALWAYS edit the
  revision — replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and
  delete the `import app.db.models` line. Keep `Base.metadata`'s
  NAMING_CONVENTION.
- **i18n:** new user-facing English needs both `ja` and `zh` catalogues filled
  by hand, no `fuzzy` markers (a fuzzy entry compiles to English while looking
  translated), and `messages.pot` deleted rather than committed.
- **Never write `Concert.ladder_rechecked_at_utc` from the poll.** That column
  is the HUMAN's "I have re-checked this one" stamp behind
  `/admin/quiet-ladders`'s ordering. A machine writing it silently marks the
  owner's worklist as attended. The poll gets its own column.
- Commit trailers, exactly:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```
- **Test discipline:** for every assertion, name the single edit that would
  make the feature wrong while leaving it green. If you can name one, the test
  is not finished. Seven tests that could not fail were caught on this repo's
  last multi-phase build.

## File structure

| File | Responsibility |
| --- | --- |
| `src/app/domain/round_proposals.py` (new) | PURE. Dedupe-key derivation and the diff: given rounds a concert holds and rounds a page proposes, which are new. |
| `src/app/db/models.py` (modify) | `RoundProposal` model; `Concert.ladder_polled_at_utc`. |
| `alembic/versions/<rev>_round_proposals.py` (new) | The table and the column. |
| `src/app/db/round_proposals.py` (new) | Reads/writes for proposals; the daily clock (`round_poll_due`/`stamp_round_poll_run`); the poll stamp. |
| `src/app/db/service.py` (modify) | Facade re-exports. |
| `src/app/round_poll.py` (new) | Run order: which concerts, in what sequence, what a failure costs. |
| `src/app/config.py` (modify) | `round_poll_enabled`. |
| `src/app/scheduler/loop.py` (modify) | The tick block. |
| `src/app/web/routes/admin.py` (modify) | `GET /admin/quiet-ladders/proposals`. |
| `src/app/web/templates/admin_round_proposals.html` (new) | The read-only list. |

---

### Task 1: the pure diff and the dedupe key

**Files:**
- Create: `src/app/domain/round_proposals.py`
- Test: `tests/test_round_proposals_domain.py`

**Interfaces:**
- Consumes: `app.domain.round_evidence.ProposedRound` (existing).
- Produces:
  - `dedupe_key(label: str, opens_at_utc: datetime | None) -> str`
  - `new_proposals(existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]) -> list[ProposedRound]`
  - `@dataclass(frozen=True) class HeldRound: label: str; opens_at_utc: datetime | None`

`HeldRound` is deliberately a NEW two-field dataclass rather than reusing
`db.quiet_ladders.QuietRound`: `domain/` may not import `db/`. The caller
adapts.

- [ ] **Step 1: Read the existing `ProposedRound`**

Open `src/app/domain/round_evidence.py` and read `ProposedRound` (around line
352). Use its real field names in this task; the snippets below assume `label`
and `opens_at_utc` and **may be wrong**.

- [ ] **Step 2: Write the failing tests**

```python
from datetime import UTC, datetime

from app.domain.round_proposals import HeldRound, dedupe_key, new_proposals


def test_the_key_folds_widths_and_spacing_so_one_round_is_one_row():
    """Mutation: dropping the normalisation. Then a page that renders
    '１次先行' one day and '1次先行 ' the next accumulates a second proposal
    for the same round, every day, forever."""
    a = dedupe_key("１次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
    b = dedupe_key(" 1次先行  ", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
    assert a == b


def test_a_round_with_no_open_time_still_dedupes_on_its_label():
    """Mutation: making the key None/empty when opens_at is None -- every poll
    then adds another copy of the same undated round."""
    a = dedupe_key("一般発売", None)
    b = dedupe_key("一般発売", None)
    assert a == b and a != ""


def test_a_moved_open_time_is_a_DIFFERENT_key():
    """Deliberate, and the spec's reasoning: dismissing 'opens Sept 3' is not
    a judgement on 'opens Sept 10'. Mutation: keying on the label alone, which
    would let one dismissal swallow a corrected deadline."""
    a = dedupe_key("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))
    b = dedupe_key("1次先行", datetime(2026, 9, 10, 1, 0, tzinfo=UTC))
    assert a != b


def test_a_round_the_concert_already_holds_is_not_proposed(make_proposed):
    """Mutation: returning `proposed` unchanged. The pass would then re-propose
    every round the concert already has, every day."""
    held = [HeldRound("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    proposed = [make_proposed("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    assert new_proposals(held, proposed) == []


def test_a_genuinely_new_round_survives(make_proposed):
    """Mutation: returning [] unconditionally -- which the previous test alone
    would not catch."""
    held = [HeldRound("1次先行", datetime(2026, 9, 3, 1, 0, tzinfo=UTC))]
    fresh = make_proposed("2次先行", datetime(2026, 9, 20, 1, 0, tzinfo=UTC))
    assert new_proposals(held, [fresh]) == [fresh]
```

Write a local `make_proposed` fixture in this test file that builds a real
`ProposedRound` from `round_evidence.py` — read its constructor first.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_round_proposals_domain.py -q`
Expected: FAIL, `ModuleNotFoundError: app.domain.round_proposals`.

- [ ] **Step 4: Implement**

```python
"""PURE. Which proposed rounds are NEW, and the key that makes a dismissal stick.

No session, no network, no key -- so the rule that decides whether the owner
is shown a proposal at all is testable without any of them, the same
separation `round_evidence.py` already makes for whether a round may exist.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.round_evidence import ProposedRound


@dataclass(frozen=True)
class HeldRound:
    """A round the concert ALREADY carries. Not `db.quiet_ladders.QuietRound`:
    `domain/` may not import `db/`, so the caller adapts."""

    label: str
    opens_at_utc: datetime | None


def _normalize_label(label: str) -> str:
    # NFKC folds full-width digits and letters onto their ASCII forms, so
    # '１次先行' and '1次先行' are one round rather than two rows a day.
    return unicodedata.normalize("NFKC", label).strip().casefold()


def dedupe_key(label: str, opens_at_utc: datetime | None) -> str:
    """Stable identity for one proposed round.

    Derived and readable rather than an opaque hash: a key you can read in the
    table is a key you can debug.
    """
    stamp = opens_at_utc.isoformat() if opens_at_utc is not None else ""
    return f"{_normalize_label(label)}|{stamp}"


def new_proposals(
    existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]
) -> list[ProposedRound]:
    """`proposed` minus anything the concert already holds, order preserved."""
    held = {dedupe_key(r.label, r.opens_at_utc) for r in existing}
    return [p for p in proposed if dedupe_key(p.label, p.opens_at_utc) not in held]
```

- [ ] **Step 5: Run to verify they pass, then lint**

Run: `uv run --isolated pytest tests/test_round_proposals_domain.py -q`
Run: `uv run --isolated ruff check .`

- [ ] **Step 6: Apply each named mutation and confirm the matching test fails**

Revert cleanly between mutations — a report on an earlier branch misattributed
a result because two were live in one working tree.

- [ ] **Step 7: Commit**

```bash
git add src/app/domain/round_proposals.py tests/test_round_proposals_domain.py
git commit
```

---

### Task 2: the table, the model, and the DB layer

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_round_proposals.py`
- Create: `src/app/db/round_proposals.py`
- Modify: `src/app/db/service.py`
- Test: `tests/test_round_proposals_db.py`

**Interfaces:**
- Consumes: Task 1's `dedupe_key`.
- Produces:
  - `upsert_proposal(session, concert_id: int, *, label: str, kind: RoundKind, opens_at_utc, closes_at_utc, evidence_yaml: str, source_url: str, now: datetime) -> RoundProposal`
  - `pending_proposals(session) -> list[RoundProposal]`
  - `dismissed_keys_for(session, concert_id: int) -> set[str]`
  - `round_poll_due(session, now: datetime) -> bool`
  - `stamp_round_poll_run(session, now: datetime) -> None`
  - `record_ladder_polled(session, concert_id: int, now: datetime) -> None`

- [ ] **Step 1: Read the two models you are copying from**

`FetchDomain` (`models.py:1024`) for the pending-is-NULL idiom, and
`discovery_due`/`stamp_discovery_run` (`src/app/db/drafts.py:354` and `:362`)
for the daily clock — including that the stamp is written even when the run
FAILED, or a crashing pass re-runs every 60 seconds forever.

- [ ] **Step 2: Add the model and the column**

```python
class RoundProposal(Base):
    """One round a poll of the concert's official page says it has grown.

    Pending is BOTH `dismissed_at` and `applied_at` NULL -- the
    nullable-timestamp idiom `FetchDomain` and `PendingDraft` already use,
    rather than a status string with its own vocabulary.
    """

    __tablename__ = "round_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE, unlike PendingDraft.concert_id's SET NULL: a proposal is ABOUT a
    # concert, not a record of where a concert came from, so it has no meaning
    # once the concert is gone.
    concert_id: Mapped[int] = mapped_column(
        ForeignKey("concerts.id", ondelete="CASCADE")
    )
    # domain/round_proposals.py:dedupe_key. Unique per concert so a re-poll
    # UPDATES rather than adding a second row -- which is what makes a
    # dismissal stick across daily runs.
    dedupe_key: Mapped[str] = mapped_column(String(400))
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[RoundKind] = mapped_column(SAEnum(RoundKind))
    opens_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closes_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # field -> the quoted source line, one small YAML document. BESIDE the
    # proposal, the way PendingDraft.completion_yaml sits beside its draft.
    evidence_yaml: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Written by phase 2 only; phase 1 never sets it.
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("concert_id", "dedupe_key", name="uq_round_proposal_key"),
    )
```

On `Concert`, beside `ladder_rechecked_at_utc`:

```python
    # The POLL's stamp, deliberately NOT ladder_rechecked_at_utc. That column
    # answers "has a HUMAN looked at this" and orders /admin/quiet-ladders; a
    # machine writing it would silently mark the owner's worklist attended.
    ladder_polled_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

- [ ] **Step 3: Generate and hand-edit the migration**

```bash
uv run --isolated alembic revision --autogenerate -m "round proposals"
```

Then edit the generated file: replace every `app.db.models.UTCDateTime()` with
`sa.DateTime()` and delete `import app.db.models`. Confirm the unique
constraint carries its name. Then:

```bash
uv run --isolated alembic upgrade head
```

- [ ] **Step 4: Write the failing tests**

Use `tests/conftest.py`'s shared `db`/`session` fixtures — do not write a new
one; they register `PRAGMA foreign_keys=ON`, without which a missing cascade
makes a test PASS.

```python
async def test_a_second_poll_of_the_same_round_updates_rather_than_duplicates(session):
    """Mutation: making upsert an unconditional INSERT. Nothing else in the
    suite would notice; the table just grows one row per day per round."""


async def test_a_dismissed_key_is_reported_so_the_next_poll_can_skip_it(session):
    """Mutation: dismissed_keys_for returning an empty set -- a dismissed
    proposal then comes back tomorrow and every day after."""


async def test_pending_excludes_dismissed_and_applied(session):
    """Mutation: dropping either NULL check. Seed one of each so dropping
    ONE is visible; with only a dismissed row, dropping the applied check
    passes."""


async def test_deleting_the_concert_takes_its_proposals(session):
    """The CASCADE. Mutation: SET NULL instead -- which needs foreign_keys=ON
    to be observable at all, hence the shared fixture."""


async def test_the_daily_clock_is_stamped_even_when_the_run_failed(session):
    """Mutation: stamping only on success. The pass then re-runs every 60s
    forever after one bad page -- the exact trap loop.py documents for the
    discovery sweep."""


async def test_polling_does_not_touch_the_human_recheck_stamp(session):
    """The worklist-integrity rule. Mutation: record_ladder_polled writing
    ladder_rechecked_at_utc -- which looks harmless and silently clears
    /admin/quiet-ladders' ordering."""
```

Fill each body in. The docstrings above are the requirement; a test whose body
does not actually catch its named mutation is not done.

- [ ] **Step 5: Run red, implement `src/app/db/round_proposals.py`, run green**

Model `round_poll_due`/`stamp_round_poll_run` on `discovery_due`/
`stamp_discovery_run` in `drafts.py`. Read them first.

- [ ] **Step 6: Add every new name to `db/service.py`**

Run: `uv run --isolated pytest tests/test_service_facade.py -q` — it fails if
the module and the facade disagree.

- [ ] **Step 7: Full suite, lint, commit**

---

### Task 3: the pass

**Files:**
- Create: `src/app/round_poll.py`
- Test: `tests/test_round_poll.py`

**Interfaces:**
- Consumes: Tasks 1 and 2; `db.service.quiet_ladder_rows`,
  `note_fetch_domain`, `approved_fetch_hosts`; `app.fetching`;
  `app.llm.chat`; `domain.page_text.normalize_page_text`;
  `domain.round_completion.completion_prompt` / `parse_completion_response`;
  `domain.round_evidence.verify_rounds`; `domain.yaml_export.concert_to_yaml`.
- Produces: `@dataclass class PollReport` with at least `polled: int`,
  `skipped_no_url: int`, `skipped_host: int`, `failed: int`,
  `new_proposals: int`, `budget_exhausted: bool`; and
  `run_round_poll(session, now, *, fetcher=..., chat=...) -> PollReport`.

**Read first:** `src/app/draft_completion.py` — this task is the same shape
against a different candidate list. And `src/app/discovery.py:61-91`, the
budget comment, which explains why the bound is a wall clock and why a fixed
start point starves.

- [ ] **Step 1: Note what the candidate query already gives you**

`quiet_ladder_rows(session)` returns `QuietLadder` rows carrying
`concert_id`, `official_url` and `rounds` (existing rounds, whose docstring
says they exist so a re-check does not re-propose them), sorted
never-checked-first then oldest-check-first. **Do not re-query any of that**,
and do not write a second definition of "which concerts are worth re-reading"
— the spec forbids it and drift is the reason.

- [ ] **Step 2: Write the failing tests**

Inject a fake fetcher and a fake `chat` — **no test may call DeepSeek**, the
same discipline `tests/test_triage*.py` already follows.

```python
async def test_a_concert_with_no_official_url_is_skipped_and_counted(session):
    """Mutation: crashing, or silently skipping without counting. A quiet
    concert nobody gave a page is a fact worth reporting."""


async def test_an_unknown_host_is_recorded_pending_and_the_concert_skipped(session):
    """Mutation: fetching anyway. Assert BOTH that no fetch happened and that
    a FetchDomain row now exists -- asserting only the row would pass with the
    fetch still firing."""


async def test_a_declined_host_is_skipped_and_NOT_re_proposed(session):
    """Mutation: treating declined like unknown, which re-proposes a host a
    human already refused."""


async def test_an_ungrounded_round_is_rejected_with_its_reason(session):
    """verify_rounds' job. Mutation: trusting the model's reply. Assert the
    reason is recorded, not merely that the round is absent -- 'dropped
    silently' and 'rejected with a reason' must not look the same."""


async def test_one_concert_failing_does_not_stop_the_run(session):
    """Mutation: letting the exception escape. Seed three concerts, make the
    SECOND raise, and assert the third was still polled."""


async def test_a_round_the_concert_already_holds_is_not_proposed(session):
    """End-to-end over Task 1's rule."""


async def test_the_wall_clock_budget_stops_the_run_and_says_so(session):
    """Mutation: dropping budget_exhausted from the report -- a truncation
    only the journal knows about is the silent degradation this repo keeps
    finding."""
```

- [ ] **Step 3: Run red**

- [ ] **Step 4: Implement**

Per concert, in this order, each step's failure costing only that concert:

1. no `official_url` → `skipped_no_url += 1`, continue.
2. host not in `approved_fetch_hosts` → `note_fetch_domain(...)`,
   `skipped_host += 1`, continue. A DECLINED host is skipped without
   re-recording.
3. fetch via `ApprovedPublicHosts` → `normalize_page_text`.
4. `concert_to_yaml(...)` → `completion_prompt(...)` → `chat(...)` →
   `parse_completion_response(...)`.
5. `verify_rounds(...)`; rejections recorded with their reason.
6. `new_proposals(held, verified)` where `held` adapts `row.rounds` into
   `HeldRound`; drop keys in `dismissed_keys_for(...)`; `upsert_proposal` the
   rest.
7. `record_ladder_polled(...)`.

Wrap each concert in `try/except Exception`, log, `failed += 1`, continue.
Check the wall clock BETWEEN concerts, and set `budget_exhausted`.

**`concert_to_yaml`'s signature is required-argument-heavy on purpose** (see
CLAUDE.md invariant 3 on its `characters` parameter). Read it before calling
it; do not add a default to make your call site shorter.

- [ ] **Step 5: Run green, apply every named mutation, full suite, lint, commit**

---

### Task 4: the flag, the tick, and the digest

**Files:**
- Modify: `src/app/config.py`, `src/app/scheduler/loop.py`
- Test: `tests/test_round_poll_scheduler.py`

**Interfaces:**
- Consumes: Task 3's `run_round_poll`, Task 2's clock.

- [ ] **Step 1: Add the setting**

```python
    # Same shape as discovery_enabled: one config value switching the whole
    # subsystem, absent from production until deliberately set.
    round_poll_enabled: bool = False
```

- [ ] **Step 2: Write the failing tests**

```python
async def test_the_pass_does_not_run_when_the_flag_is_off(session):
    """Mutation: dropping the flag check -- production starts fetching
    third-party pages on deploy, unasked."""


async def test_a_failed_run_is_still_stamped_as_todays_run(session):
    """The re-stamp-after-rollback path loop.py documents for discovery.
    Mutation: dropping the except-branch stamp; the pass then re-runs every
    60 seconds forever."""


async def test_a_failing_poll_does_not_roll_back_delivery(session):
    """Every block in the tick owns its try/except and its own commit: the
    least important operation must never roll back the most important one.
    Mutation: removing the try/except."""


async def test_the_digest_is_queued_not_sent(session):
    """Invariant 4. Mutation: DMing from the pass -- assert a Notification row
    exists and that no send was attempted."""


async def test_the_digest_kind_is_not_in_UNREPORTED_NOTE_KINDS():
    """It reports on a THIRD-PARTY PAGE, not on deliveries -- the `discovery`
    notice's precedent. Mutation: adding it to that set, which would make it
    stop being logged in delivery_log."""
```

- [ ] **Step 3: Implement the tick block**

Copy the discovery block's structure in `loop.py` (its own `try/except`, its
own commit, and the re-stamp on the now-clean transaction after a rollback).
Queue the digest with `concert_id = NULL` so the drain renders plain text, to
`ADMIN_WHITELIST`, and `ensure_user` an admin **only** when
`session.get(User, admin_id)` returns None — otherwise every run overwrites a
real admin's username with a placeholder.

- [ ] **Step 4: Run green, mutations, full suite, lint, commit**

---

### Task 5: the read-only proposals page

**Files:**
- Modify: `src/app/web/routes/admin.py`
- Create: `src/app/web/templates/admin_round_proposals.html`
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `.../zh/...`
- Test: `tests/test_admin_round_proposals.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_page_renders_for_an_admin(client):
    """Every page needs at least one logged-in GET render test -- a missing one
    shipped a 500 once (template context drift)."""


async def test_a_non_admin_gets_403(client):
    """Invariant 5: signed in and unauthorized is 403."""


async def test_each_proposal_shows_its_quoted_evidence(client):
    """The whole point of the page. Mutation: rendering the label and dates
    without the quote -- the operator then cannot check the claim, which is
    what separates this from a guess. Scope the assertion to the row: this
    repo shipped a test that passed with its feature deleted because base.html
    already contained the asserted string."""


async def test_a_dismissed_proposal_is_not_listed(client):
    """Mutation: listing everything."""
```

- [ ] **Step 2: Run red, implement, run green**

Route: `GET /admin/quiet-ladders/proposals`, `require_admin`, reading
`pending_proposals` from the facade. Template renders concerts grouped, each
proposal with its label, dual times (`dual_lines` — invariant 1: never a bare
JST), and its quoted source line. Read-only in phase 1; **no buttons.**

Use the `.tagtable` class the other admin pages use. Radiuses: 3px / 999px /
4px / 50% only — there is a sweep test.

- [ ] **Step 3: i18n**

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill both by hand, remove every `fuzzy` marker `pybabel` adds, delete
`messages.pot`. Run `tests/test_i18n_catalogues.py`.

- [ ] **Step 4: Full suite, lint, commit**

---

### Task 6: documentation

**Files:** `docs/architecture.md`, `README.md`, `WISHLIST.md`

- [ ] **Step 1: `docs/architecture.md`**

Entries under the modules they belong to, written for that file's stated
purpose — which reasonable-looking edits would undo a measurement or re-open
an incident, not what the code does. At minimum:

- Why `ladder_polled_at_utc` is separate from `ladder_rechecked_at_utc`, and
  what merging them would silently do to the owner's worklist.
- Why the dedupe key includes `opens_at_utc`, and what keying on the label
  alone would swallow.
- Why the run is uncapped by COUNT but bounded by a WALL CLOCK, and why the
  classify pass's 511-lead failure does not transfer (one prompt per concert,
  not N items in one prompt).
- That the pass reuses `completion_prompt` and `verify_rounds` rather than
  owning a second prompt or a second safety rule.

- [ ] **Step 2: `README.md`**

One line appended to the "Shipped since Phase 12" list, per CLAUDE.md's
"Feature wishlist, and the two files a shipped feature updates". Say what an
operator can now do. Note in the line that this is phase 1 of two.

- [ ] **Step 3: `WISHLIST.md`**

**Do NOT move entry #2 to Shipped** — phase 2 is unbuilt and the entry covers
both shapes, of which the small one is still unstarted. Update the entry in
place: record that the large shape's phase 1 shipped, dated, with what it does
and does not yet do. Then do the revision pass CLAUDE.md requires.

- [ ] **Step 4: Full suite, lint, commit**

---

## Self-review notes

**Spec coverage.** Architecture → Tasks 1-3. Data model → Task 2. Daily pass,
host gating, budget, failure isolation → Task 3. Gating flag, clock, digest →
Task 4. Phase 1's read-only review surface → Task 5. Every phase-2 item (the
draft page, per-round apply, `applied_at` being written) is out of scope and
named as such.

**The riskiest task is 3**, not 2. Task 2's migration fails loudly. Task 3
decides what the owner is shown and what is silently dropped, and its failure
mode — a real round discarded without a reason — is invisible by construction.
Its `verify_rounds` rejection test is the most important check in this plan.

**Ordering.** 1 is pure and independent. 2 needs nothing from 1 but 3 needs
both. 4 wires 3 into the tick. 5 reads 2. 6 last.
