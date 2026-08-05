# AI draft completion (AI triage, phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill a pending skeleton draft's empty round ladder from the official
ticket page the draft already names, keeping every proposed deadline grounded in
text the app can find on that page.

**Architecture:** An admin button on `/concerts/import/pending` writes a
`TriageRun` row with `kind="complete"`; the scheduler tick picks it up and, per
open skeleton draft, fetches the draft's `official_url` (only from a host an
admin has approved), extracts page text, asks DeepSeek for a `rounds:` list
where every timestamp carries a quoted source line, drops any round whose quote
the app cannot find in that same text, and rewrites only the `rounds:` key of
the stored draft. Evidence and rejections are stored beside the draft and
rendered on its preview.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + Alembic (SQLite),
Jinja2, httpx, BeautifulSoup4, PyYAML, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-05-draft-completion-design.md`.
Read it before starting; every task below implements part of it.

## Global Constraints

- **Tests and lint must both pass before any commit:** `uv run --isolated pytest -q`
  and `uv run --isolated ruff check .`. Always `--isolated` — an external process
  holds a lock on `.venv` on this machine and a plain `uv run` will try to resync
  and fail.
- **The full suite exceeds the 600s Bash timeout.** Run it in halves, e.g.
  `uv run --isolated pytest -q tests/test_a*.py tests/test_[b-m]*.py` then
  `uv run --isolated pytest -q tests/test_[n-z]*.py`. Per-task, run only that
  task's test files plus the ones it touches; run the full suite in halves once
  at the end of the task, in the FOREGROUND (a backgrounded suite run stalls).
- **Never store naive datetimes** (invariant 1). The DB stores aware UTC only;
  `UTCDateTime` rejects naive values. JST strings in drafts stay strings here —
  this feature never converts them; `import_commit` already does that.
- **`import_commit` stays the only write path into `concerts`** (invariant 6's
  neighbourhood). Nothing in this plan creates a concert.
- **Never send a DM from a web route** (invariant 4). The run queues one
  `Notification` through the outbox; the web half queues nothing.
- **Migrations:** after `uv run --isolated alembic revision --autogenerate`,
  ALWAYS edit the revision to replace `app.db.models.UTCDateTime()` with
  `sa.DateTime()` and delete the `import app.db.models` line. Keep
  `Base.metadata`'s NAMING_CONVENTION. Config files stay ASCII-only.
- **New user-facing copy goes through `_()`** and needs both catalogues updated
  (`src/app/translations/ja` and `/zh`) — Task 12. Admin pages
  (`/admin/fetch-domains`) are English-only and NOT wrapped in `_()`, exactly
  like `/admin/deliveries` and `/admin/discoveries`.
- **Never interpolate user-controlled text into an inline `on*` handler**
  (invariant 7). Use `data-` attributes read via `dataset`.
- **Commit after every task**, with the trailer:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Vxjeyen7iM1ccYfpjD1mPF
  ```
- **Branch:** `draft-completion` (already created off `origin/main`).

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/page_text.py` (new) | HTML → the one canonical page text. Pure. |
| `src/app/domain/round_evidence.py` (new) | The safety rule: is this round grounded in that text? Pure. |
| `src/app/domain/round_completion.py` (new) | The completion prompt, its reply parse, and the rounds-only merge. Pure. |
| `src/app/fetching.py` (modify) | Host **policy** objects: pinned vs approved-public. |
| `src/app/db/models.py` (modify) | `FetchDomain`; `TriageRun.kind` + four counts; `PendingDraft.completion_yaml`. |
| `src/app/db/service.py` (modify) | Fetch-domain reads/writes; completion-candidate loader; `request_triage(kind=)`. |
| `src/app/draft_completion.py` (new) | Run order: which drafts, what it costs, what a failure costs. Also `complete_one`, shared with the paste route. |
| `src/app/scheduler/loop.py` (modify) | Dispatch the picked-up run on its `kind`. |
| `src/app/web/routes/imports.py` (modify) | The button's POST, the pending-list callout, the paste fallback, evidence into the preview context. |
| `src/app/web/routes/fetch_domains.py` (new) | `/admin/fetch-domains`, admin-only, English-only. |
| `src/app/web/templates/_editor_round_card.html` (modify) | One optional evidence block. |

---

### Task 1: Schema — `fetch_domains`, `TriageRun.kind` + counts, `PendingDraft.completion_yaml`

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_draft_completion.py`
- Test: `tests/test_draft_completion_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FetchDomain` (columns `id`, `host`, `first_seen_at`,
  `first_seen_url`, `approved_at`, `declined_at`, `decided_by`);
  `TriageRun.kind: str`, `TriageRun.drafts_completed/rounds_added/
  rounds_rejected/blocked_domains: int | None`;
  `PendingDraft.completion_yaml: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_completion_schema.py`:

```python
"""The phase-2 columns, and what their defaults mean.

A written 0 on a run means "looked, found none"; NULL means "never got there,
or not this kind's business". That distinction is the whole reason these
columns are nullable, so it is pinned here rather than left to a comment.
"""

from datetime import UTC, datetime

import pytest

from app.db.models import FetchDomain, PendingDraft, TriageRun


@pytest.mark.asyncio
async def test_triage_run_defaults_to_the_classify_kind(session):
    run = TriageRun(requested_at=datetime.now(UTC), requested_by=1)
    session.add(run)
    await session.flush()
    assert run.kind == "classify"
    # A classify run never gets there, so the completion counts stay absent.
    assert run.drafts_completed is None
    assert run.rounds_added is None
    assert run.rounds_rejected is None
    assert run.blocked_domains is None


@pytest.mark.asyncio
async def test_pending_draft_starts_with_no_completion_record(session):
    row = PendingDraft(draft_text="title: x\nrounds: []\n", title="x", created_by=1)
    session.add(row)
    await session.flush()
    assert row.completion_yaml == ""


@pytest.mark.asyncio
async def test_fetch_domain_pending_is_both_timestamps_null(session):
    row = FetchDomain(
        host="eplus.jp",
        first_seen_at=datetime.now(UTC),
        first_seen_url="https://eplus.jp/sf/detail/1234",
    )
    session.add(row)
    await session.flush()
    assert row.approved_at is None and row.declined_at is None
```

Find the shared `session` fixture: `grep -rn "def session" tests/conftest.py`.
Use it exactly as `tests/test_triage_run.py` does — do not invent a new fixture,
and make sure the fixture registers the `PRAGMA foreign_keys=ON` connect
listener (it does; do not bypass it).

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_draft_completion_schema.py -q
```
Expected: `ImportError: cannot import name 'FetchDomain'`.

- [ ] **Step 3: Add the model changes**

In `src/app/db/models.py`, add to `TriageRun` (after `requested_at`):

```python
    # Which run this is. "classify" is phase 1 (the discovery-queue pass) and
    # "complete" is phase 2 (filling pending skeletons' rounds). ONE table for
    # both because they share everything that is hard -- the request/pickup
    # handshake, the status machine, and the re-stamp-after-rollback rule --
    # and differ only in what they count. server_default so every row written
    # before this column existed reads back as the classify run it was.
    kind: Mapped[str] = mapped_column(String(20), default="classify", server_default="classify")
```

and, beside the existing nullable count columns:

```python
    # Completion-run counts. NULL on a classify run and on a run that never
    # started; 0 means "looked, found none". Kind-specific by design -- see
    # `kind` above -- so reading a count without checking the kind is a bug in
    # the reader, not a reason to fold these into the classify counts, whose
    # meanings genuinely differ.
    drafts_completed: Mapped[int | None] = mapped_column(Integer)
    rounds_added: Mapped[int | None] = mapped_column(Integer)
    rounds_rejected: Mapped[int | None] = mapped_column(Integer)
    blocked_domains: Mapped[int | None] = mapped_column(Integer)
```

Add to `PendingDraft`:

```python
    # What an AI completion pass read and decided, as one small YAML document:
    # `source_url`, `evidence` (round index -> field -> the quoted source line)
    # and `rejected` (human-readable reasons a proposed round was dropped).
    # BESIDE the draft, never inside it: evidence is proofreading scaffolding,
    # and draft_text is a document that gets exported, re-pasted and committed
    # into concerts. Non-empty also means ATTEMPTED, which is what stops a
    # second press re-paying for a decision already handed to the operator --
    # so it is written only when an LLM call actually happened.
    completion_yaml: Mapped[str] = mapped_column(Text, default="", server_default="")
```

Add a new model beside `TriageRun`:

```python
class FetchDomain(Base):
    """One host the completion pass wanted to read, and whether it may.

    This app's HTTP fetches are host-pinned by design (`app/fetching.py`), but
    a draft's `official_url` is by nature an arbitrary host -- that is what an
    official page IS. Rather than drop the pin, the completion pass asks a
    human once per host: an unknown host is recorded here and the draft is
    skipped, and only an approved host is ever fetched.

    Pending is BOTH timestamps NULL, the nullable-timestamp idiom
    `dismissed_at`/`announced_at` already use rather than a status string with
    its own vocabulary. A declined host stays declined and is never proposed
    again -- re-proposing something a human already refused is how an approval
    queue becomes noise nobody reads.
    """

    __tablename__ = "fetch_domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Lowercased at the single write path (`note_fetch_domain`); hosts are
    # case-insensitive and two casings of one host must not be two rows with
    # two different verdicts.
    host: Mapped[str] = mapped_column(String(255), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    # The URL that wanted it, so the approver can judge the host by what it
    # was actually asked to read rather than by its name alone.
    first_seen_url: Mapped[str] = mapped_column(String(1000), default="")
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    declined_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # SET NULL, not CASCADE: erasing the admin who decided must not un-decide
    # the host -- same reasoning as TriageRun.requested_by.
    decided_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="SET NULL")
    )
```

- [ ] **Step 4: Run the test — it should pass**

```
uv run --isolated pytest tests/test_draft_completion_schema.py -q
```
Expected: 3 passed (tests build their DB from `Base.metadata`, so they pass
before the migration exists).

- [ ] **Step 5: Generate and fix the migration**

```
uv run --isolated alembic revision --autogenerate -m "draft completion"
```
Then open the generated file in `alembic/versions/` and:
1. Replace every `app.db.models.UTCDateTime()` with `sa.DateTime()`.
2. Delete the `import app.db.models` line.
3. Confirm the `add_column` calls carry the server defaults:
   `sa.Column("kind", sa.String(length=20), nullable=False, server_default="classify")`,
   `sa.Column("completion_yaml", sa.Text(), nullable=False, server_default="")`.
   Autogenerate sometimes omits them; without them the ALTER fails on a
   non-empty table.
4. Confirm `op.create_table("fetch_domains", ...)` names its constraints (the
   NAMING_CONVENTION handles this; check `fk_fetch_domains_decided_by_users`
   and `uq_fetch_domains_host` appear).

This migration adds columns and a table only — it calls no `drop_constraint`,
so the legacy-anonymous-constraint rule (CLAUDE.md) does not apply and no
legacy-DDL fixture is needed.

- [ ] **Step 6: Apply it and verify**

```
uv run --isolated alembic upgrade head
uv run --isolated alembic downgrade -1
uv run --isolated alembic upgrade head
```
Expected: all three succeed. The down-then-up proves the downgrade is real.

- [ ] **Step 7: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/db/models.py alembic/versions tests/test_draft_completion_schema.py
git commit -m "feat: schema for AI draft completion (fetch_domains, run kind, draft evidence)"
```

---

### Task 2: `domain/page_text.py` — one canonical page text

**Files:**
- Create: `src/app/domain/page_text.py`
- Test: `tests/test_page_text.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PAGE_TEXT_CAP: int = 60_000`; `html_to_text(html: str) -> str`;
  `normalize_page_text(text: str) -> str`; `collapse(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_page_text.py`:

```python
"""HTML in, the one text the model reads and evidence is checked against.

The property that matters is not prettiness: it is that ONE function produces
the text both the prompt and the verifier see. A page that reaches the model
one way and the verifier another turns the evidence rule into theatre.
"""

from app.domain.page_text import PAGE_TEXT_CAP, collapse, html_to_text, normalize_page_text


def test_script_and_style_contents_never_reach_the_text():
    html = """
    <html><head><style>.a{color:red}</style><script>var x = "先行抽選";</script></head>
    <body><p>1次先行抽選 申込締切 2026年1月10日(土)23:59</p></body></html>
    """
    text = html_to_text(html)
    assert "申込締切 2026年1月10日(土)23:59" in text
    assert "color:red" not in text
    assert "var x" not in text


def test_block_elements_are_separated_so_two_lines_do_not_fuse():
    # Without a separator, <td>23:59</td><td>受付終了</td> becomes "23:59受付終了",
    # and a quote of either half then fails to match the page it came from.
    text = html_to_text("<table><tr><td>23:59</td><td>受付終了</td></tr></table>")
    assert "23:59 受付終了" in text


def test_runs_of_whitespace_collapse_to_single_spaces():
    assert collapse("a  \n\t b　c") == "a b c"


def test_text_is_capped():
    text = normalize_page_text("あ" * (PAGE_TEXT_CAP + 500))
    assert len(text) == PAGE_TEXT_CAP


def test_normalize_is_idempotent():
    once = normalize_page_text("  a \n b  ")
    assert normalize_page_text(once) == once
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_page_text.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.domain.page_text'`.

- [ ] **Step 3: Write the module**

Create `src/app/domain/page_text.py`:

```python
"""HTML to the ONE text an AI completion pass reads.

Pure, like every other module in `domain/`: a string in, a string out, no
httpx call of its own -- the same split `ingest.py` and `eventernote.py` make
against their fetchers.

WHY ONE FUNCTION. The completion pass asks a model for rounds and then checks
that the quote it gave for each timestamp actually occurs on the page. That
check is only worth anything if the text the model read and the text the
verifier searches are byte-identical, so both go through here. Two
normalizations -- one for the prompt, one for the check -- would let a quote
be "not found" because of a whitespace rule the model never saw, or, worse,
let an invented quote match text the model was never given.

The whitespace rule is deliberately aggressive: every run of whitespace,
INCLUDING U+3000 (the ideographic space, which this app's Japanese pages are
full of and which `str.split()` already treats as whitespace), collapses to one
ASCII space. Models reproduce spacing loosely when they quote, so an exact
substring test against loosely-spaced source text fails constantly; collapsing
both sides is what makes the test about the WORDS rather than the layout.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

# How much of a page reaches the model. Extracted text is far denser than the
# HTML it came from -- 60k characters of Japanese is an enormous page -- and the
# same cap covers a pasted page, so the fetched and pasted paths cannot behave
# differently.
PAGE_TEXT_CAP = 60_000

_WHITESPACE = re.compile(r"\s+")


def collapse(text: str) -> str:
    """Every run of whitespace to a single space, ends trimmed."""
    return _WHITESPACE.sub(" ", text).strip()


def html_to_text(html: str) -> str:
    """Readable text from a fetched page: no script, no style, blocks separated.

    The separator is not cosmetic. `get_text()` with no separator fuses
    adjacent cells -- `<td>23:59</td><td>受付終了</td>` becomes `23:59受付終了`
    -- and a model quoting either half would then be quoting something that,
    as a string, is not on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_page_text(soup.get_text(separator=" "))


def normalize_page_text(text: str) -> str:
    """Collapse, then cap. The entry point for text that is already text
    (a pasted page), and the tail of `html_to_text`."""
    return collapse(text)[:PAGE_TEXT_CAP]
```

- [ ] **Step 4: Run the test**

```
uv run --isolated pytest tests/test_page_text.py -q
```
Expected: 5 passed.

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/page_text.py tests/test_page_text.py
git commit -m "feat: page_text, the one text a completion pass reads"
```

---

### Task 3: `domain/round_evidence.py` — the rule that replaces `strip_rounds`

**Files:**
- Create: `src/app/domain/round_evidence.py`
- Test: `tests/test_round_evidence.py`

**Interfaces:**
- Consumes: `app.domain.page_text.collapse`.
- Produces:
  - `TIMESTAMP_FIELDS: tuple[str, ...]` =
    `("apply_opens_jst", "apply_closes_jst", "results_jst", "payment_deadline_jst")`
  - `@dataclass(frozen=True) ProposedRound` with fields
    `data: dict`, `evidence: dict[str, str]`, `label: str`
  - `@dataclass(frozen=True) Verdict` with fields
    `accepted: tuple[ProposedRound, ...]`, `rejected: tuple[str, ...]`
  - `verify_rounds(rounds: Sequence[ProposedRound], page_text: str,
    leg_labels: Sequence[str], today: date) -> Verdict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_round_evidence.py`:

```python
"""Phase 2's strip_rounds: a round survives only if the app can find the text
the model says it read.

Phase 1 could guarantee honesty by emitting no rounds at all. Phase 2 emits
them, so every one of these cases is a way a fabricated deadline could reach a
real user as a real reminder. A rejection is never silent -- each one carries a
reason that reaches the preview.
"""

from datetime import date

from app.domain.round_evidence import ProposedRound, verify_rounds

PAGE = (
    "チケット情報 1次先行抽選 受付開始 2026年1月5日(月)12:00 "
    "申込締切 2026年1月10日(土)23:59 当落発表 2026年1月15日(木)18:00 "
    "入金期限 2026年1月20日(火)23:59 2次先行は後日発表"
)
TODAY = date(2025, 12, 1)


def _round(**over):
    data = {"label": "1次先行抽選", "kind": "lottery", "apply_closes_jst": "2026-01-10 23:59"}
    evidence = {"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}
    data.update(over.pop("data", {}))
    evidence.update(over.pop("evidence", {}))
    return ProposedRound(data=data, evidence=evidence, label=data["label"])


def test_a_grounded_round_is_accepted():
    v = verify_rounds([_round()], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1 and not v.rejected


def test_a_round_with_no_quote_for_its_timestamp_is_rejected():
    v = verify_rounds([_round(evidence={"apply_closes_jst": ""})], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "no evidence" in v.rejected[0]


def test_a_quote_that_is_not_on_the_page_is_rejected():
    r = _round(evidence={"apply_closes_jst": "申込締切 2026年2月28日(土)23:59"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "not on the page" in v.rejected[0]


def test_a_real_quote_that_does_not_contain_its_own_timestamp_is_rejected():
    # The nastiest case: the model quotes a line that genuinely exists but says
    # something else. Finding the quote is not enough -- the quote has to be
    # about this timestamp.
    r = _round(evidence={"apply_closes_jst": "当落発表 2026年1月15日(木)18:00"})
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "does not carry" in v.rejected[0]


def test_loose_spacing_in_a_quote_still_matches():
    r = _round(evidence={"apply_closes_jst": "申込締切　2026年1月10日(土)23:59"})
    assert len(verify_rounds([r], PAGE, ["Day 1"], TODAY).accepted) == 1


def test_a_year_missing_from_the_quote_may_come_from_the_page():
    # Japanese pages routinely put the year in a heading and omit it from the
    # deadline line. Requiring it in the quote would reject the common case.
    page = "2026年 チケット情報 申込締切 1月10日(土)23:59"
    r = _round(evidence={"apply_closes_jst": "申込締切 1月10日(土)23:59"})
    assert len(verify_rounds([r], page, ["Day 1"], TODAY).accepted) == 1


def test_a_year_on_neither_the_quote_nor_the_page_is_rejected():
    page = "申込締切 1月10日(土)23:59"
    r = _round(evidence={"apply_closes_jst": "申込締切 1月10日(土)23:59"})
    assert not verify_rounds([r], page, ["Day 1"], TODAY).accepted


def test_a_zero_minute_written_as_20時_is_accepted():
    page = "申込締切 2026年1月10日(土)20時"
    r = _round(
        data={"apply_closes_jst": "2026-01-10 20:00"},
        evidence={"apply_closes_jst": "申込締切 2026年1月10日(土)20時"},
    )
    assert len(verify_rounds([r], page, ["Day 1"], TODAY).accepted) == 1


def test_out_of_order_anchors_are_rejected():
    r = _round(
        data={"apply_opens_jst": "2026-01-15 18:00", "apply_closes_jst": "2026-01-10 23:59"},
        evidence={
            "apply_opens_jst": "当落発表 2026年1月15日(木)18:00",
            "apply_closes_jst": "申込締切 2026年1月10日(土)23:59",
        },
    )
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "out of order" in v.rejected[0]


def test_an_implausible_year_is_rejected():
    page = "申込締切 2126年1月10日(土)23:59"
    r = _round(
        data={"apply_closes_jst": "2126-01-10 23:59"},
        evidence={"apply_closes_jst": "申込締切 2126年1月10日(土)23:59"},
    )
    v = verify_rounds([r], page, ["Day 1"], TODAY)
    assert not v.accepted
    assert "implausible" in v.rejected[0]


def test_applies_to_naming_a_leg_the_draft_does_not_have_is_rejected():
    r = _round(data={"applies_to": ["Day 9"]})
    v = verify_rounds([r], PAGE, ["Day 1", "Day 2"], TODAY)
    assert not v.accepted
    assert "Day 9" in v.rejected[0]


def test_applies_to_matching_a_leg_is_kept():
    r = _round(data={"applies_to": ["Day 2"]})
    v = verify_rounds([r], PAGE, ["Day 1", "Day 2"], TODAY)
    assert len(v.accepted) == 1


def test_a_round_with_no_timestamps_at_all_is_rejected():
    # A round with no deadline is not a round -- it is a label. Keeping it
    # would put an empty rung on the ladder for a human to wonder about.
    r = ProposedRound(data={"label": "2次先行", "kind": "lottery"}, evidence={}, label="2次先行")
    v = verify_rounds([r], PAGE, ["Day 1"], TODAY)
    assert not v.accepted
    assert "no timestamps" in v.rejected[0]


def test_one_bad_round_does_not_cost_a_good_one():
    good, bad = _round(), _round(evidence={"apply_closes_jst": "存在しない行"})
    v = verify_rounds([good, bad], PAGE, ["Day 1"], TODAY)
    assert len(v.accepted) == 1 and len(v.rejected) == 1


def test_evidence_never_rides_into_the_accepted_data():
    v = verify_rounds([_round()], PAGE, ["Day 1"], TODAY)
    assert "evidence" not in v.accepted[0].data
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_round_evidence.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.domain.round_evidence'`.

- [ ] **Step 3: Write the module**

Create `src/app/domain/round_evidence.py`:

```python
"""Is this proposed round grounded in the page it claims to come from?

THIS IS PHASE 2's `strip_rounds`. Phase 1 could promise honesty cheaply: it
emitted no rounds at all, and stripped any the model invented anyway. Phase 2
emits rounds, so the promise has to be earned per round, in code, on the same
principle -- the prompt asks, the code decides.

A round survives only if the model showed WHERE it read each timestamp and this
module can find that text on the page. Five ways to fail, each of them a way a
fabricated deadline could otherwise reach a real user as a real reminder:

  1. a timestamp with no quote at all;
  2. a quote that is not on the page (the plain hallucination);
  3. a quote that IS on the page but does not carry this timestamp -- the
     nastiest case, because the naive "did the quote match?" check passes;
  4. anchors out of order (results before the deadline they announce);
  5. an implausible date, or an `applies_to` naming a leg the draft lacks.

NOTHING IS DROPPED SILENTLY. Every rejection carries a human-readable reason
that reaches the preview, because a real deadline quietly discarded is exactly
as harmful as a fake one quietly kept -- the operator has no way to know to look
in either case.

The comparison is deliberately about WORDS, not layout: both sides go through
`page_text.collapse`, and digits are compared as NUMBERS after normalizing
full-width forms and 年月日時分. A model reproducing 2026年1月10日 as 2026年1月10日
with a different space, or a page writing 23:59 as ２３：５９, must not be a
rejection -- those are formatting, and rejecting on formatting would train the
operator to ignore rejections.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.domain.page_text import collapse

# The four anchors a round can carry, in the order they must occur in time.
# Same order as the ladder itself; `_check_order` reads it as an ordering.
TIMESTAMP_FIELDS: tuple[str, ...] = (
    "apply_opens_jst",
    "apply_closes_jst",
    "results_jst",
    "payment_deadline_jst",
)

# How far from today a proposed date may sit. A ticket page can legitimately
# carry a deadline that has already passed (an old lead being drafted late), so
# the past window is generous; the future window is what catches a fat-fingered
# or hallucinated century.
_PAST_YEARS = 2
_FUTURE_YEARS = 3

_FULLWIDTH = str.maketrans("０１２３４５６７８９：", "0123456789:")
_NUMBER = re.compile(r"\d+")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})")


@dataclass(frozen=True)
class ProposedRound:
    """One round as the model proposed it, with its evidence held apart.

    `data` is the round mapping destined for the draft's YAML and NEVER
    contains `evidence` -- that is proofreading scaffolding and must not ride
    into a document that gets committed into `concerts`.
    """

    data: dict
    evidence: dict[str, str]
    label: str


@dataclass(frozen=True)
class Verdict:
    accepted: tuple[ProposedRound, ...] = ()
    rejected: tuple[str, ...] = ()


def normalize_numbers(text: str) -> list[int]:
    """Every number in `text`, after folding the Japanese ways of writing one.

    ２０２６年１月１０日(土)２３：５９ and 2026-01-10 23:59 must yield the same
    list, or the check would reject a page for its typography.
    """
    folded = text.translate(_FULLWIDTH)
    folded = re.sub(r"[年月時]", " ", folded)
    folded = re.sub(r"[日分秒]", " ", folded)
    return [int(n) for n in _NUMBER.findall(folded)]


def _stamp_parts(stamp: str) -> tuple[int, int, int, int, int] | None:
    """(year, month, day, hour, minute) from a 'YYYY-MM-DD HH:MM' string."""
    match = _STAMP.search(stamp.strip())
    if match is None:
        return None
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def _quote_carries_stamp(quote: str, page_numbers: set[int], parts) -> bool:
    """Does this quote actually say this timestamp?

    Month, day and hour must be IN THE QUOTE. The YEAR may instead come from
    anywhere on the page: Japanese ticket pages routinely put it in a heading
    and omit it from the deadline line itself, and demanding it in the quote
    would reject the common case. The MINUTE is waived when it is 0, because
    '20時' is how a page writes 20:00 and carries no zero to find.
    """
    year, month, day, hour, minute = parts
    numbers = set(normalize_numbers(quote))
    if not {month, day, hour} <= numbers:
        return False
    if minute and minute not in numbers:
        return False
    return year in numbers or year in page_numbers


def _check_order(data: dict) -> str | None:
    """The anchors present must not go backwards in time."""
    seen: list[tuple[str, str]] = []
    for field_name in TIMESTAMP_FIELDS:
        value = str(data.get(field_name) or "").strip()
        if value:
            seen.append((field_name, value))
    for (a_name, a), (b_name, b) in zip(seen, seen[1:]):
        # ISO-ish strings sort chronologically as text, which is the whole
        # reason this app writes them this way -- no parsing needed here.
        if b < a:
            return f"{a_name} ({a}) and {b_name} ({b}) are out of order"
    return None


def verify_rounds(
    rounds: Sequence[ProposedRound],
    page_text: str,
    leg_labels: Sequence[str],
    today: date,
) -> Verdict:
    """Split proposed rounds into the grounded and the rejected-with-a-reason.

    One bad round never costs a good one -- the same skip-and-count philosophy
    every parser in this package follows -- because the alternative is a page
    with one sloppy line handing back nothing at all.
    """
    page = collapse(page_text)
    page_numbers = set(normalize_numbers(page))
    known_legs = {label.strip() for label in leg_labels}
    accepted: list[ProposedRound] = []
    rejected: list[str] = []

    for proposed in rounds:
        label = proposed.label or "(unlabelled round)"
        reason = _reject_reason(proposed, page, page_numbers, known_legs, today)
        if reason is None:
            accepted.append(proposed)
        else:
            rejected.append(f"round {label!r}: {reason}")

    return Verdict(accepted=tuple(accepted), rejected=tuple(rejected))


def _reject_reason(
    proposed: ProposedRound,
    page: str,
    page_numbers: set[int],
    known_legs: set[str],
    today: date,
) -> str | None:
    """The first reason this round cannot be trusted, or None."""
    stamps = {
        name: str(proposed.data.get(name) or "").strip()
        for name in TIMESTAMP_FIELDS
        if str(proposed.data.get(name) or "").strip()
    }
    if not stamps:
        return "no timestamps at all -- a round with no deadline is a label, not a rung"

    for name, stamp in stamps.items():
        quote = collapse(str(proposed.evidence.get(name) or ""))
        if not quote:
            return f"no evidence for {name} ({stamp})"
        if quote not in page:
            return f"the quote for {name} is not on the page: {quote!r}"
        parts = _stamp_parts(stamp)
        if parts is None:
            return f"{name} ({stamp}) is not a 'YYYY-MM-DD HH:MM' timestamp"
        year = parts[0]
        if not (today.year - _PAST_YEARS <= year <= today.year + _FUTURE_YEARS):
            return f"{name} ({stamp}) has an implausible year"
        if not _quote_carries_stamp(quote, page_numbers, parts):
            return f"the quote for {name} does not carry {stamp}: {quote!r}"

    order_problem = _check_order(proposed.data)
    if order_problem is not None:
        return order_problem

    applies_to = proposed.data.get("applies_to") or []
    if isinstance(applies_to, list):
        for leg in applies_to:
            if str(leg).strip() not in known_legs:
                return f"applies_to names {str(leg).strip()!r}, which is not a leg of this draft"

    return None
```

Note: remove the unused `field` import if ruff flags it.

- [ ] **Step 4: Run the tests**

```
uv run --isolated pytest tests/test_round_evidence.py -q
```
Expected: 15 passed. If `test_a_real_quote_that_does_not_contain_its_own_timestamp_is_rejected`
fails, check `_quote_carries_stamp` — the 当落発表 line shares the year and
minute with the 申込締切 line but not the month/day/hour combination, so the
`{month, day, hour} <= numbers` test is what must reject it.

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/round_evidence.py tests/test_round_evidence.py
git commit -m "feat: round evidence verification, phase 2's strip_rounds"
```

---

### Task 4: `domain/round_completion.py` — prompt, reply parse, rounds-only merge

**Files:**
- Create: `src/app/domain/round_completion.py`
- Test: `tests/test_round_completion.py`

**Interfaces:**
- Consumes: `app.domain.triage_prompts.extract_yaml`,
  `app.domain.round_evidence.ProposedRound`, `app.domain.page_text.PAGE_TEXT_CAP`.
- Produces:
  - `completion_prompt(draft_text: str, page_text: str) -> tuple[str, str]`
  - `parse_completion_response(text: str) -> tuple[list[ProposedRound], list[str]]`
    (rounds, warnings)
  - `merge_rounds(draft_text: str, rounds: Sequence[dict]) -> str`
  - `draft_leg_labels(draft_text: str) -> list[str]`
  - `CompletionResponseError` (unusable reply)

- [ ] **Step 1: Write the failing test**

Create `tests/test_round_completion.py`:

```python
"""The completion prompt's reply, and the surgical merge back into a draft."""

import pytest
import yaml

from app.domain.round_completion import (
    CompletionResponseError,
    completion_prompt,
    draft_leg_labels,
    merge_rounds,
    parse_completion_response,
)

SKELETON = """\
# source: https://www.eventernote.com/events/486243
title: 例）ライブ
title_en: Example live
kind: tour
performances:
- label: Day 1
  label_en: Day 1
  venue: Zepp Haneda
- label: Day 2
  label_en: Day 2
  venue: Zepp Namba
rounds: []
"""

REPLY = """\
```yaml
rounds:
  - label: 1次先行抽選
    kind: lottery
    applies_to: [Day 1]
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
```
"""


def test_the_prompt_carries_the_draft_and_the_page():
    system, user = completion_prompt(SKELETON, "チケット情報 申込締切")
    assert "rounds" in system
    assert "Day 1" in user and "チケット情報 申込締切" in user


def test_the_prompt_forbids_inventing_a_deadline():
    system, _user = completion_prompt(SKELETON, "x")
    assert "evidence" in system
    assert "NEVER" in system


def test_a_fenced_reply_parses_into_rounds_with_evidence_held_apart():
    rounds, warnings = parse_completion_response(REPLY)
    assert not warnings
    assert len(rounds) == 1
    assert rounds[0].label == "1次先行抽選"
    assert rounds[0].evidence["apply_closes_jst"].startswith("申込締切")
    # Evidence is lifted OUT of the data: it must never reach the draft.
    assert "evidence" not in rounds[0].data
    assert rounds[0].data["apply_closes_jst"] == "2026-01-10 23:59"


def test_a_reply_that_is_not_a_mapping_is_unusable():
    with pytest.raises(CompletionResponseError):
        parse_completion_response("- just\n- a list\n")


def test_an_empty_rounds_list_is_an_answer_not_an_error():
    rounds, warnings = parse_completion_response("rounds: []\n")
    assert rounds == [] and not warnings


def test_a_malformed_single_round_is_skipped_and_named():
    rounds, warnings = parse_completion_response("rounds:\n  - 'not a mapping'\n")
    assert rounds == []
    assert warnings and "round 1" in warnings[0]


def test_a_timestamp_yaml_resolved_to_a_datetime_comes_back_as_text():
    # PyYAML resolves `2026-01-10 23:59:00` to a datetime object. The draft
    # vocabulary is text, and yaml_import's own _dt parses it from text, so a
    # datetime here would be dumped back in a shape parse_draft does not read.
    rounds, _w = parse_completion_response(
        "rounds:\n  - label: x\n    apply_closes_jst: 2026-01-10 23:59:00\n"
        "    evidence: {apply_closes_jst: q}\n"
    )
    assert rounds[0].data["apply_closes_jst"] == "2026-01-10 23:59"


def test_merge_replaces_only_the_rounds_key():
    merged = merge_rounds(SKELETON, [{"label": "1次先行抽選", "kind": "lottery"}])
    data = yaml.safe_load(merged)
    assert data["title"] == "例）ライブ"
    assert data["kind"] == "tour"
    assert [d["label"] for d in data["performances"]] == ["Day 1", "Day 2"]
    assert data["rounds"][0]["label"] == "1次先行抽選"


def test_merge_keeps_the_source_comment_that_containment_reads():
    # phase 1's duplicate containment matches the WHOLE '# source: ...' line
    # inside a stored draft. A round-trip that drops it would silently make the
    # next triage press re-draft this production.
    merged = merge_rounds(SKELETON, [{"label": "x"}])
    assert merged.startswith("# source: https://www.eventernote.com/events/486243\n")


def test_merge_survives_a_draft_with_no_comment_prefix():
    merged = merge_rounds("title: x\nrounds: []\n", [{"label": "y"}])
    assert yaml.safe_load(merged)["rounds"][0]["label"] == "y"


def test_leg_labels_come_off_the_draft():
    assert draft_leg_labels(SKELETON) == ["Day 1", "Day 2"]
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_round_completion.py -q
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the module**

Create `src/app/domain/round_completion.py`:

```python
"""The completion prompt, its reply, and the surgical merge back into a draft.

Pure, like `triage_prompts.py`, and split from it on the same principle
`tags_yaml`/`tags_diff` are split: that module is about TRIAGING a queue, this
one is about COMPLETING one document. It reuses `extract_yaml` from there
rather than growing a third fence-stripper -- models preface a fenced block
with a sentence however firmly a prompt forbids it, and one place should know
that.

The model is asked for ONE key, `rounds:`, and nothing else. Not a whole draft:
the rest of the document has already been produced (phase 1) and possibly
proofread, and re-emitting it would churn text a human already read for no
gain. That is also what makes `merge_rounds` a one-key rewrite rather than a
document replacement.

EVIDENCE IS LIFTED OUT AT PARSE TIME. The model writes it inside each round,
because that is the only place it can write it; this module removes it before
the round mapping goes anywhere near a draft. A draft is a document that gets
exported, re-pasted and committed into `concerts`, and scaffolding that rides
along becomes an unknown key somebody's parser warns about two features later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import yaml

from app.domain.page_text import PAGE_TEXT_CAP
from app.domain.round_evidence import TIMESTAMP_FIELDS, ProposedRound
from app.domain.triage_prompts import extract_yaml


class CompletionResponseError(Exception):
    """The reply can't be used at all -- not YAML, or not a mapping. Anything
    short of that degrades to a warning on one round, the same
    warnings-over-failures split every parser in `domain/` makes."""


_COMPLETION_SYSTEM_PROMPT = """\
You are filling in the ticket rounds for ONE dekimasen.app concert draft, in
the vocabulary `.claude/skills/add-concert/SKILL.md` uses. You are given the
draft (which already has its title, its legs and its cast) and the text of that
production's official ticket page.

Output exactly ONE YAML document holding ONE key, `rounds`, and no prose before
or after it:

```yaml
rounds:
  - label: 1次先行抽選
    label_en: 1st advance lottery
    label_zh: 首轮先行抽选
    kind: lottery
    applies_to: [Day 1]
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    results_jst: 2026-01-15 18:00
    payment_deadline_jst: 2026-01-20 23:59
    url: https://example.com/ticket
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
      results_jst: "当落発表 2026年1月15日(木)18:00"
      payment_deadline_jst: "入金期限 2026年1月20日(火)23:59"
```

Rules, and the first one is the only one that matters:

- EVERY timestamp you write MUST have an `evidence` entry quoting the text on
  the page you read it from, copied VERBATIM from that page. A round whose
  evidence cannot be found on the page is thrown away by the application, so an
  invented quote does not get you a round -- it loses you one. If the page does
  not state a time, leave that field out. If it states none, return
  `rounds: []`.
- NEVER infer, estimate or complete a timestamp the page does not state. A
  fabricated deadline reaches a real person as a real reminder for something
  that was never real, which is the worst thing this system can do.
- Times are JST, written `YYYY-MM-DD HH:MM`. A page writing 2026年1月10日(土)
  23:59 means `2026-01-10 23:59`. A deadline written 23:59 on the last day
  stays 23:59 -- do not round it.
- `applies_to` is a list of leg labels copied EXACTLY from the draft's
  `performances`. Omit it (or leave it empty) when the round covers the whole
  event; that is the common case for a tour-wide lottery.
- Japanese is canonical. Fill `label`/`label_en`/`label_zh` as all three, or
  leave the two variants out entirely -- never a partial set.
- One round per campaign rung. 1次先行, 2次先行 and 一般発売 are three rounds,
  not one; a single round's own 受付開始 and 申込締切 are two fields of ONE
  round, not two rounds.
- `kind` is one of: lottery, first_come, general_sale, fanclub, presale,
  stream, goods_sale, eligibility_item_sale, upgrade, overseas_package, other.
  Use `other` when unsure rather than guessing a mechanic.
"""


def completion_prompt(draft_text: str, page_text: str) -> tuple[str, str]:
    """The system+user pair for one draft's completion."""
    user = (
        "The draft as it stands:\n"
        f"{draft_text}\n\n"
        f"The official ticket page, as text (may be truncated at {PAGE_TEXT_CAP} "
        f"characters):\n{page_text[:PAGE_TEXT_CAP]}"
    )
    return _COMPLETION_SYSTEM_PROMPT, user


def _as_stamp_text(value) -> str:
    """A timestamp field as the draft vocabulary's TEXT.

    PyYAML resolves `2026-01-10 23:59:00` to a `datetime` and a bare date to a
    `date`. `yaml_import._dt` parses these fields from text, so leaving a
    datetime object in the mapping would dump back a shape the draft parser
    does not read -- and the failure would land two tasks away, at paste time.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def parse_completion_response(text: str) -> tuple[list[ProposedRound], list[str]]:
    """Rounds (evidence held apart) and per-round warnings.

    Raises `CompletionResponseError` only when the reply as a WHOLE is
    unusable; a single malformed round is skipped and named instead.
    """
    yaml_text = extract_yaml(text)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CompletionResponseError(f"that doesn't parse as YAML: {exc}") from exc
    if data is None:
        return [], []
    if not isinstance(data, dict):
        raise CompletionResponseError(
            "expected a YAML mapping with a 'rounds' key -- got something else"
        )

    raw_rounds = data.get("rounds")
    if raw_rounds is None:
        return [], []
    if not isinstance(raw_rounds, list):
        return [], [f"rounds: expected a list, got {type(raw_rounds).__name__} -- ignored"]

    rounds: list[ProposedRound] = []
    warnings: list[str] = []
    for i, raw in enumerate(raw_rounds, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"round {i}: expected a mapping -- skipped")
            continue
        payload = dict(raw)
        raw_evidence = payload.pop("evidence", None)
        evidence: dict[str, str] = {}
        if isinstance(raw_evidence, dict):
            evidence = {str(k): str(v) for k, v in raw_evidence.items()}
        elif raw_evidence is not None:
            warnings.append(f"round {i}: evidence was not a mapping -- treated as absent")
        for name in TIMESTAMP_FIELDS:
            if name in payload:
                payload[name] = _as_stamp_text(payload[name])
        label = str(payload.get("label") or "").strip()
        if not label:
            warnings.append(f"round {i}: no label -- skipped")
            continue
        payload["label"] = label
        rounds.append(ProposedRound(data=payload, evidence=evidence, label=label))
    return rounds, warnings


def _split_comment_prefix(text: str) -> tuple[str, str]:
    """The leading '#' comment lines, and the rest.

    Load-bearing, not cosmetic: a phase-1 draft's first line is
    `# source: https://www.eventernote.com/events/N`, and the triage runner's
    duplicate containment matches that WHOLE LINE inside stored draft text. A
    naive safe_load/safe_dump round-trip drops every comment, which would make
    the next triage press re-draft a production the operator already has --
    silently, and only visible as duplicates in the pending list.
    """
    lines = text.splitlines(keepends=True)
    cut = 0
    for line in lines:
        if line.lstrip().startswith("#") or not line.strip():
            cut += 1
        else:
            break
    return "".join(lines[:cut]), "".join(lines[cut:])


def merge_rounds(draft_text: str, rounds: Sequence[dict]) -> str:
    """Rewrite ONLY the `rounds:` key of a stored draft.

    Known and accepted: comments INSIDE the body are lost to the round-trip.
    Phase-1 drafts have none (they are safe_dump output) and an agent draft's
    inline comments are not load-bearing; a comment-preserving YAML library
    would be a new dependency protecting data nothing reads. The leading
    prefix, which IS load-bearing, is preserved above.
    """
    prefix, body = _split_comment_prefix(draft_text)
    data = yaml.safe_load(body)
    if not isinstance(data, dict):
        data = {}
    data["rounds"] = [dict(r) for r in rounds]
    return prefix + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def draft_leg_labels(draft_text: str) -> list[str]:
    """The `performances` labels an `applies_to` may name. Never raises: an
    unreadable draft simply has no legs to bind to, and the caller's verify
    step will reject any applies_to rather than crash the run."""
    try:
        data = yaml.safe_load(_split_comment_prefix(draft_text)[1])
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    labels = []
    for day in data.get("performances") or []:
        if isinstance(day, dict):
            label = str(day.get("label") or "").strip()
            if label:
                labels.append(label)
    return labels
```

- [ ] **Step 4: Run the tests**

```
uv run --isolated pytest tests/test_round_completion.py -q
```
Expected: 11 passed.

- [ ] **Step 5: Check the round kinds named in the prompt are real**

```
uv run --isolated python -c "from app.domain.types import RoundKind; print([k.value for k in RoundKind])"
```
Every value listed in `_COMPLETION_SYSTEM_PROMPT`'s `kind` bullet must appear
in that output. Fix the prompt to match the enum if any differs — the parser
defaults an unknown kind to `other` with a warning, so a wrong list is a silent
quality loss rather than an error.

- [ ] **Step 6: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/round_completion.py tests/test_round_completion.py
git commit -m "feat: completion prompt, reply parse and rounds-only merge"
```

---

### Task 5: `fetching.py` grows a host policy

**Files:**
- Modify: `src/app/fetching.py`
- Modify: `src/app/web/routes/imports.py` (the `fetch_ramen_html` call site)
- Modify: `src/app/discovery.py` (its `fetch_html` call site)
- Modify: `src/app/triage.py` (`fetch_event_page`)
- Test: `tests/test_fetch_policies.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PinnedHost(host: str)` and `ApprovedPublicHosts(is_approved:
  Callable[[str], bool])`, both with `.check(url: str) -> None` raising
  `HostNotAllowed`; `fetch_html(url, *, policy, user_agent, timeout=...,
  max_bytes=..., max_redirects=..., transport=None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_policies.py`:

```python
"""One guard, two policies -- and the widened one must not become a loophole."""

import httpx
import pytest

from app.fetching import (
    ApprovedPublicHosts,
    FetchFailed,
    HostNotAllowed,
    PinnedHost,
    fetch_html,
)


def test_pinned_host_is_unchanged_behaviour():
    policy = PinnedHost("www.eventernote.com")
    policy.check("https://www.eventernote.com/events/1")
    with pytest.raises(HostNotAllowed):
        policy.check("https://evil.example/events/1")
    with pytest.raises(HostNotAllowed):
        policy.check("http://www.eventernote.com/events/1")


def test_approved_public_hosts_refuses_an_unapproved_host():
    policy = ApprovedPublicHosts(lambda host: host == "eplus.jp")
    with pytest.raises(HostNotAllowed):
        policy.check("https://not-approved.example/x")


def test_approved_public_hosts_refuses_plain_http():
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("http://eplus.jp/x")


def test_approved_public_hosts_refuses_a_private_address(monkeypatch):
    # The failure this exists to stop: a draft naming the instance metadata
    # endpoint, or any host whose DNS points inside the VPC.
    monkeypatch.setattr(
        "app.fetching._resolve", lambda host: ["169.254.169.254"]
    )
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("https://metadata.example/latest/meta-data/")


def test_approved_public_hosts_refuses_when_any_address_is_private(monkeypatch):
    # A host resolving to one public and one private address is a rebinding
    # setup, not a mixed deployment worth accommodating.
    monkeypatch.setattr(
        "app.fetching._resolve", lambda host: ["93.184.216.34", "127.0.0.1"]
    )
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("https://mixed.example/x")


def test_approved_public_hosts_accepts_an_approved_public_host(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    ApprovedPublicHosts(lambda host: host == "eplus.jp").check("https://eplus.jp/x")


@pytest.mark.asyncio
async def test_a_redirect_off_an_approved_host_to_an_unapproved_one_is_refused(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "good.example":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, text="<html>should never be reached</html>")

    with pytest.raises(HostNotAllowed):
        await fetch_html(
            "https://good.example/x",
            policy=ApprovedPublicHosts(lambda host: host == "good.example"),
            user_agent="test",
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_an_approved_page_comes_back(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<p>hi</p>"))
    body = await fetch_html(
        "https://good.example/x",
        policy=ApprovedPublicHosts(lambda host: True),
        user_agent="test",
        transport=transport,
    )
    assert body == "<p>hi</p>"


@pytest.mark.asyncio
async def test_the_byte_cap_still_applies(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="x" * 5000))
    with pytest.raises(FetchFailed):
        await fetch_html(
            "https://good.example/x",
            policy=ApprovedPublicHosts(lambda host: True),
            user_agent="test",
            max_bytes=100,
            transport=transport,
        )
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_fetch_policies.py -q
```
Expected: `ImportError: cannot import name 'ApprovedPublicHosts'`.

- [ ] **Step 3: Rewrite `fetching.py`'s guard as a policy**

Replace the module docstring's guard paragraph and the `check_host` /
`_redirect_host_hook` / `fetch_html` signature. Keep `FetchError`,
`HostNotAllowed`, `FetchFailed` and every constant exactly as they are.

Add near the top:

```python
import ipaddress
import socket
from collections.abc import Callable
```

Replace `check_host` and `_redirect_host_hook` with:

```python
class HostPolicy:
    """Which hosts a fetch may reach. One method, called before the request
    and again on every redirect hop.

    A POLICY rather than a widened `allowed_host` string, because the two
    answers this app needs are different KINDS of answer -- "exactly this one
    host" and "any public host a human has approved" -- and expressing both
    through one loosened parameter is how a security control acquires a mode
    nobody remembers is there. Two policies, one guard, one redirect hook.
    """

    def check(self, url: str) -> None:
        raise NotImplementedError


class PinnedHost(HostPolicy):
    """https, and exactly one host. The original guard, unchanged: an
    allowlist of one, never a blocklist."""

    def __init__(self, host: str) -> None:
        self.host = host

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self.host:
            raise HostNotAllowed(f"only https://{self.host}/... URLs are supported")


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Its own function so a test can
    replace it without a network, and so the policy below reads as policy."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


class ApprovedPublicHosts(HostPolicy):
    """https, a host a human has approved, and only public addresses.

    The completion pass reads a draft's `official_url`, which is by nature an
    arbitrary host -- that is what an official page IS. Three things stand
    between that and an SSRF:

      1. https only, as everywhere else here;
      2. `is_approved(host)`, so nothing is fetched from a host an admin has
         not personally approved (see `FetchDomain`) -- and, because this same
         check runs on every redirect hop, a redirect off an approved host
         onto an unapproved one is refused rather than followed;
      3. every address the host resolves to must be GLOBAL, so a private,
         loopback, link-local or CGNAT target is refused. That covers the
         instance metadata endpoint at 169.254.169.254, which on this deploy
         is a real credential source. ALL addresses must pass, not any: a host
         answering with one public and one private address is a rebinding
         setup, not a deployment to accommodate.

    Accepted residual risk, recorded rather than ignored: DNS rebinding
    between this resolution and the connection httpx makes. Closing it means
    connecting to the resolved address with a Host override and re-doing TLS
    verification by name; the exposure is an attacker who both controls a host
    an admin explicitly approved and can flip its DNS inside the request
    window.
    """

    def __init__(self, is_approved: Callable[[str], bool]) -> None:
        self.is_approved = is_approved

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host:
            raise HostNotAllowed("only https:// URLs can be read")
        if not self.is_approved(host):
            raise HostNotAllowed(f"{host} has not been approved for fetching")
        try:
            addresses = _resolve(host)
        except OSError as exc:
            raise HostNotAllowed(f"{host} does not resolve: {exc}") from exc
        if not addresses:
            raise HostNotAllowed(f"{host} does not resolve")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise HostNotAllowed(f"{host} resolved to something unreadable") from exc
            if not ip.is_global:
                raise HostNotAllowed(f"{host} resolves to a non-public address ({address})")


def _redirect_hook(policy: HostPolicy):
    """The httpx response event hook, called for every hop.

    follow_redirects=True alone would chase a redirect issued by an allowed
    host (a compromised host, or an open-redirect endpoint there) to an
    arbitrary address, silently defeating the policy. Built PER CALL so it
    closes over THIS caller's policy -- a module-level hook pinned to one
    policy is the obvious extraction bug and is exactly what a shared guard
    must not have.
    """

    async def _check_redirect(response: httpx.Response) -> None:
        if response.is_redirect:
            location = response.headers.get("location", "")
            policy.check(urljoin(str(response.url), location))

    return _check_redirect
```

Change `fetch_html`'s signature from `allowed_host: str` to `policy: HostPolicy`,
replace `check_host(url, allowed_host)` with `policy.check(url)` and
`_redirect_host_hook(allowed_host)` with `_redirect_hook(policy)`. Nothing else
in the function changes.

- [ ] **Step 4: Update the three call sites**

```
grep -rn "allowed_host" src/ tests/
```
Expect hits in `src/app/web/routes/imports.py`, `src/app/discovery.py`,
`src/app/triage.py`, and their tests. In each, replace
`allowed_host=X` with `policy=PinnedHost(X)` and import `PinnedHost` from
`app.fetching`. Behaviour is identical — `PinnedHost.check` is `check_host`'s
body verbatim.

If any test calls `check_host` directly, change it to `PinnedHost(host).check(url)`.

- [ ] **Step 5: Run the affected tests**

```
uv run --isolated pytest tests/test_fetch_policies.py tests/test_discovery_fetch.py tests/test_triage_run.py -q
uv run --isolated pytest -q -k "import or fetch or ramen"
```
Expected: all pass. Any failure here is a missed call site, not a design problem.

- [ ] **Step 6: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/fetching.py src/app/discovery.py src/app/triage.py src/app/web/routes/imports.py tests/
git commit -m "feat: fetching takes a host policy -- pinned, or approved-public"
```

---

### Task 6: service helpers — fetch domains, completion candidates, run kinds

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_fetch_domain_service.py`

**Interfaces:**
- Consumes: `FetchDomain`, `PendingDraft`, `TriageRun` from Task 1.
- Produces:
  - `note_fetch_domain(session, host: str, url: str, now: datetime) -> FetchDomain`
  - `approved_fetch_hosts(session) -> set[str]`
  - `fetch_domain_rows(session) -> list[FetchDomain]` (newest first)
  - `pending_fetch_domain_count(session) -> int`
  - `decide_fetch_domain(session, domain_id: int, approve: bool, now: datetime,
    decided_by: int) -> bool`
  - `completion_candidates(session, user_id: int) -> list[PendingDraft]`
  - `request_triage(session, now, requested_by, kind: str = "classify") -> TriageRun`
  - `pending_triage_run(session, kind: str | None = None) -> TriageRun | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_domain_service.py`:

```python
"""The approval queue's reads and writes, and the completion candidate list."""

from datetime import UTC, datetime

import pytest

from app.db.models import PendingDraft
from app.db.service import (
    approved_fetch_hosts,
    completion_candidates,
    decide_fetch_domain,
    fetch_domain_rows,
    note_fetch_domain,
    pending_fetch_domain_count,
    pending_triage_run,
    request_triage,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_noting_a_host_twice_makes_one_pending_row(session):
    a = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    b = await note_fetch_domain(session, "EPLUS.JP", "https://eplus.jp/b", NOW)
    assert a.id == b.id
    # The FIRST url is kept: it is what the approver was told about.
    assert b.first_seen_url == "https://eplus.jp/a"
    assert await pending_fetch_domain_count(session) == 1


@pytest.mark.asyncio
async def test_only_approved_hosts_come_back(session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    assert await approved_fetch_hosts(session) == set()
    await decide_fetch_domain(session, row.id, True, NOW, 1)
    assert await approved_fetch_hosts(session) == {"eplus.jp"}
    assert await pending_fetch_domain_count(session) == 1


@pytest.mark.asyncio
async def test_a_declined_host_is_neither_approved_nor_pending(session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await decide_fetch_domain(session, row.id, False, NOW, 1)
    assert await approved_fetch_hosts(session) == set()
    assert await pending_fetch_domain_count(session) == 0


@pytest.mark.asyncio
async def test_deciding_an_already_decided_host_does_not_flip_it(session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    assert await decide_fetch_domain(session, row.id, True, NOW, 1) is True
    assert await decide_fetch_domain(session, row.id, False, NOW, 1) is False
    assert await approved_fetch_hosts(session) == {"eplus.jp"}


@pytest.mark.asyncio
async def test_noting_a_decided_host_never_reopens_it(session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await decide_fetch_domain(session, row.id, False, NOW, 1)
    again = await note_fetch_domain(session, "spam.example", "https://spam.example/b", NOW)
    assert again.declined_at is not None
    assert await pending_fetch_domain_count(session) == 0


def _draft(text, user=1, **over):
    row = PendingDraft(draft_text=text, title="t", created_by=user)
    for k, v in over.items():
        setattr(row, k, v)
    return row


@pytest.mark.asyncio
async def test_completion_candidates_are_this_users_open_roundless_untried_drafts(session):
    wanted = _draft("title: a\nrounds: []\n")
    has_rounds = _draft("title: b\nrounds:\n- label: r\n")
    tried = _draft("title: c\nrounds: []\n", completion_yaml="rejected: []\n")
    other_user = _draft("title: d\nrounds: []\n", user=2)
    discarded = _draft("title: e\nrounds: []\n", discarded_at=NOW)
    session.add_all([wanted, has_rounds, tried, other_user, discarded])
    await session.flush()

    rows = await completion_candidates(session, 1)
    assert [r.title for r in rows] == ["t"]
    assert [r.id for r in rows] == [wanted.id]


@pytest.mark.asyncio
async def test_requesting_two_kinds_queues_two_runs(session):
    a = await request_triage(session, NOW, 1)
    b = await request_triage(session, NOW, 1, kind="complete")
    assert a.id != b.id
    # Idempotent PER KIND: a second press of the same button reuses its row.
    assert (await request_triage(session, NOW, 1, kind="complete")).id == b.id
    # The scheduler asks for the oldest of any kind.
    assert (await pending_triage_run(session)).id == a.id
    assert (await pending_triage_run(session, kind="complete")).id == b.id
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_fetch_domain_service.py -q
```
Expected: `ImportError: cannot import name 'note_fetch_domain'`.

- [ ] **Step 3: Change `request_triage` and `pending_triage_run`**

In `src/app/db/service.py`, replace both functions (currently around line 7818):

```python
async def request_triage(
    session: AsyncSession, now: datetime, requested_by: int, kind: str = "classify"
) -> TriageRun:
    """Ask for a run of `kind`, or hand back the one of that kind already waiting.

    Idempotent PER KIND, not globally: the classify button and the completion
    button are two different asks, and a completion request arriving while a
    classify run is still queued must make its own row rather than silently
    returning -- and re-rendering as -- the other button's pending run.
    """
    pending = await pending_triage_run(session, kind=kind)
    if pending is not None:
        return pending
    run = TriageRun(requested_at=now, requested_by=requested_by, kind=kind)
    session.add(run)
    await session.flush()
    return run


async def pending_triage_run(
    session: AsyncSession, kind: str | None = None
) -> TriageRun | None:
    """The oldest run still waiting to be picked up, or None.

    `kind=None` means any kind, which is what the SCHEDULER asks: one tick
    runs one run, so the two kinds serialize against each other by
    construction. A button asks for its own kind, to render its own
    disabled state.
    """
    query = select(TriageRun).where(TriageRun.status == "requested")
    if kind is not None:
        query = query.where(TriageRun.kind == kind)
    return (await session.execute(
        query.order_by(TriageRun.id).limit(1)
    )).scalar_one_or_none()
```

- [ ] **Step 4: Add the new helpers**

Append to `src/app/db/service.py`, in a new banner section after the triage
helpers:

```python
# -- AI draft completion (phase 2) ----------------------------------------


async def note_fetch_domain(
    session: AsyncSession, host: str, url: str, now: datetime
) -> FetchDomain:
    """Record that something wanted to fetch `host`, and hand back its row.

    The SINGLE write path that creates a `FetchDomain`, and the only place the
    host is lowercased -- two casings of one host must never become two rows
    with two different verdicts. An existing row (pending, approved OR
    declined) comes back untouched: re-noting must never reopen a decision a
    human already made, and must never overwrite `first_seen_url`, which is
    what the approver was actually told about.
    """
    host = host.strip().lower()
    existing = (await session.execute(
        select(FetchDomain).where(FetchDomain.host == host)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    row = FetchDomain(host=host, first_seen_at=now, first_seen_url=url[:1000])
    session.add(row)
    await session.flush()
    return row


async def approved_fetch_hosts(session: AsyncSession) -> set[str]:
    """Every host an admin has approved. Loaded once per run and closed over
    by the fetch policy, so a run makes one query rather than one per draft."""
    rows = (await session.execute(
        select(FetchDomain.host).where(FetchDomain.approved_at.is_not(None))
    )).scalars().all()
    return set(rows)


async def fetch_domain_rows(session: AsyncSession) -> list[FetchDomain]:
    """Every recorded host, pending first and newest first within that --
    the approval screen's whole content."""
    rows = await session.execute(
        select(FetchDomain).order_by(
            FetchDomain.approved_at.is_not(None) | FetchDomain.declined_at.is_not(None),
            FetchDomain.first_seen_at.desc(),
        )
    )
    return list(rows.scalars())


async def pending_fetch_domain_count(session: AsyncSession) -> int:
    """How many hosts are waiting on a human. Drives the callout on the
    pending-drafts page: a blocked completion run has to be discoverable from
    where the button was pressed, not only from an admin page nobody opened."""
    return (await session.execute(
        select(func.count())
        .select_from(FetchDomain)
        .where(FetchDomain.approved_at.is_(None), FetchDomain.declined_at.is_(None))
    )).scalar_one()


async def decide_fetch_domain(
    session: AsyncSession, domain_id: int, approve: bool, now: datetime, decided_by: int
) -> bool:
    """Approve or decline one host. False when it is unknown or already
    decided -- the same double-submit rule `discard_pending_draft` follows, so
    a refreshed POST cannot flip a verdict."""
    row = await session.get(FetchDomain, domain_id)
    if row is None or row.approved_at is not None or row.declined_at is not None:
        return False
    if approve:
        row.approved_at = now
    else:
        row.declined_at = now
    row.decided_by = decided_by
    await session.flush()
    return True


async def completion_candidates(session: AsyncSession, user_id: int) -> list[PendingDraft]:
    """This user's open drafts that an AI completion pass should try.

    Three filters, and the third is the containment rule: still open, no rounds
    yet, and not already attempted. `completion_yaml` is written only when an
    LLM call actually happened, so a draft skipped for a missing URL, an
    unapproved domain or a dead fetch stays a candidate and the next press
    retries it once the reason is fixed.

    "No rounds yet" is decided by parsing, because that is where the answer
    lives -- the pending list already re-parses every row for its counts, and
    caching a flag at write time would freeze today's parser against
    tomorrow's (PendingDraft's own reason for storing text, not a parse).
    """
    rows = await session.execute(
        select(PendingDraft)
        .where(
            PendingDraft.created_by == user_id,
            PendingDraft.committed_at.is_(None),
            PendingDraft.discarded_at.is_(None),
            PendingDraft.completion_yaml == "",
        )
        .order_by(PendingDraft.id)
    )
    candidates = []
    for row in rows.scalars():
        try:
            if not parse_draft(row.draft_text).rounds:
                candidates.append(row)
        except DraftError:
            # A row that no longer parses cannot be completed, and is already
            # surfaced as "couldn't be re-read" on the list. Skipping it here
            # keeps one unreadable row from costing the batch.
            continue
    return candidates
```

Add `FetchDomain` to the models import at the top of `service.py`, and check
whether `parse_draft`/`DraftError` are already imported there —
`grep -n "parse_draft\|DraftError" src/app/db/service.py`. If not, import them
from `app.domain.yaml_import`.

- [ ] **Step 5: Run the tests**

```
uv run --isolated pytest tests/test_fetch_domain_service.py tests/test_triage_run.py tests/test_triage_tick.py -q
```
Expected: all pass. `request_triage`'s new keyword is defaulted, so existing
callers are unaffected.

- [ ] **Step 6: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/db/service.py tests/test_fetch_domain_service.py
git commit -m "feat: fetch-domain approval and completion-candidate service helpers"
```

---

### Task 7: `app/draft_completion.py` — the runner

**Files:**
- Create: `src/app/draft_completion.py`
- Test: `tests/test_draft_completion_run.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces:
  - `@dataclass CompletionReport` with `drafts_seen, completed, rounds_added,
    rounds_rejected, blocked_domains, skipped, tokens_in, tokens_out: int` and
    `budget_exhausted: bool`
  - `async complete_one(session, row: PendingDraft, page_text: str, source_url:
    str, *, llm_chat=llm.chat) -> tuple[int, int, int, int]` returning
    `(rounds_added, rounds_rejected, tokens_in, tokens_out)`
  - `async run_completion(session, run: TriageRun, now, *, fetcher=None,
    llm_chat=llm.chat) -> CompletionReport`
  - `COMPLETION_DRAFT_CAP = 15`, `COMPLETION_DELAY_SECONDS = 1.0`,
    `FETCH_DEADLINE_SECONDS = 30.0`, `COMPLETION_BUDGET_SECONDS = 240.0`
  - `draft_source_url(draft_text: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_completion_run.py`:

```python
"""What one completion press does, and what each failure inside it costs.

No network and no key: the fetcher and the LLM client are injected, exactly as
run_sweep and run_triage take theirs.
"""

from datetime import UTC, datetime

import pytest
import yaml

from app.db.models import FetchDomain, PendingDraft, TriageRun
from app.db.service import decide_fetch_domain, note_fetch_domain
from app.draft_completion import complete_one, draft_source_url, run_completion
from app.llm import LlmReply

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

SKELETON = """\
# source: https://www.eventernote.com/events/486243
title: 例）ライブ
official_url: https://eplus.jp/sf/detail/1234
performances:
- label: Day 1
  venue: Zepp Haneda
rounds: []
"""

PAGE = (
    "<html><body><p>1次先行抽選 申込締切 2026年1月10日(土)23:59</p></body></html>"
)

GOOD_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
"""

UNGROUNDED_REPLY = """\
rounds:
  - label: 2次先行抽選
    kind: lottery
    apply_closes_jst: 2026-02-20 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年2月20日(土)23:59"
"""


def fake_llm(reply_text):
    async def _chat(system, user, **kw):
        return LlmReply(text=reply_text, tokens_in=100, tokens_out=50)
    return _chat


async def fake_fetch(url):
    return PAGE


async def _seed(session, user_id=1, text=SKELETON):
    row = PendingDraft(draft_text=text, title="t", created_by=user_id)
    session.add(row)
    await session.flush()
    return row


def test_draft_source_url_prefers_official_over_source():
    assert draft_source_url(SKELETON) == "https://eplus.jp/sf/detail/1234"
    assert draft_source_url("source_url: https://x.example/a\nrounds: []\n") == (
        "https://x.example/a"
    )
    # eventernote_url is never used: Eventernote carries no ticket information.
    assert draft_source_url("eventernote_url: https://www.eventernote.com/events/1\n") is None


@pytest.mark.asyncio
async def test_a_grounded_round_lands_in_the_draft(session):
    row = await _seed(session)
    added, rejected, _ti, _to = await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/sf/detail/1234", llm_chat=fake_llm(GOOD_REPLY),
    )
    assert (added, rejected) == (1, 0)
    data = yaml.safe_load(row.draft_text.split("\n", 1)[1])
    assert data["rounds"][0]["apply_closes_jst"] == "2026-01-10 23:59"
    # Evidence stays OUT of the draft and beside it.
    assert "evidence" not in data["rounds"][0]
    assert "申込締切" in yaml.safe_load(row.completion_yaml)["evidence"][0]["apply_closes_jst"]


@pytest.mark.asyncio
async def test_the_source_line_survives_a_completion(session):
    row = await _seed(session)
    await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=fake_llm(GOOD_REPLY),
    )
    assert row.draft_text.startswith(
        "# source: https://www.eventernote.com/events/486243\n"
    )


@pytest.mark.asyncio
async def test_an_ungrounded_round_is_dropped_and_reported(session):
    row = await _seed(session)
    added, rejected, _ti, _to = await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=fake_llm(UNGROUNDED_REPLY),
    )
    assert (added, rejected) == (0, 1)
    assert yaml.safe_load(row.draft_text.split("\n", 1)[1])["rounds"] == []
    record = yaml.safe_load(row.completion_yaml)
    assert record["rejected"] and "not on the page" in record["rejected"][0]


@pytest.mark.asyncio
async def test_an_attempt_that_found_nothing_still_marks_the_draft_tried(session):
    row = await _seed(session)
    await complete_one(session, row, "nothing here", "https://eplus.jp/x",
                       llm_chat=fake_llm("rounds: []\n"))
    # The call was paid for; a second press must not pay again.
    assert row.completion_yaml != ""


@pytest.mark.asyncio
async def test_an_unapproved_host_is_never_fetched(session):
    row = await _seed(session)
    run = TriageRun(requested_at=NOW, requested_by=1, kind="complete")
    session.add(run)
    await session.flush()

    async def explode(url):
        raise AssertionError("an unapproved host must never be fetched")

    report = await run_completion(
        session, run, NOW, fetcher=explode, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.blocked_domains == 1
    assert report.completed == 0
    # The host is now waiting for a human, and the draft is still a candidate.
    assert row.completion_yaml == ""
    assert (await session.get(FetchDomain, 1)).host == "eplus.jp"


@pytest.mark.asyncio
async def test_an_approved_host_is_fetched_and_completed(session):
    row = await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, 1)
    run = TriageRun(requested_at=NOW, requested_by=1, kind="complete")
    session.add(run)
    await session.flush()

    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.completed == 1 and report.rounds_added == 1
    assert run.status == "done"
    assert run.drafts_completed == 1
    assert run.tokens_in == 100 and run.tokens_out == 50


@pytest.mark.asyncio
async def test_a_draft_with_no_url_is_skipped_and_left_retryable(session):
    row = await _seed(session, text="title: x\nperformances: []\nrounds: []\n")
    run = TriageRun(requested_at=NOW, requested_by=1, kind="complete")
    session.add(run)
    await session.flush()
    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.skipped == 1 and report.completed == 0
    assert row.completion_yaml == ""


@pytest.mark.asyncio
async def test_a_dead_fetch_costs_one_draft_not_the_run(session):
    good = await _seed(session)
    bad = await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, 1)
    run = TriageRun(requested_at=NOW, requested_by=1, kind="complete")
    session.add(run)
    await session.flush()

    calls = {"n": 0}

    async def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return PAGE

    report = await run_completion(
        session, run, NOW, fetcher=flaky, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.skipped == 1 and report.completed == 1
    assert run.status == "done"


@pytest.mark.asyncio
async def test_an_empty_queue_costs_nothing_and_announces_nothing(session):
    run = TriageRun(requested_at=NOW, requested_by=1, kind="complete")
    session.add(run)
    await session.flush()

    async def explode(url):
        raise AssertionError("nothing to fetch")

    report = await run_completion(session, run, NOW, fetcher=explode,
                                  llm_chat=fake_llm(GOOD_REPLY))
    assert report.drafts_seen == 0
    assert run.status == "done"
```

For the announce test, check how `tests/test_triage_run.py` asserts on the
queued `Notification` and mirror it — do not invent a different assertion shape.

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_draft_completion_run.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.draft_completion'`.

- [ ] **Step 3: Write the runner**

Create `src/app/draft_completion.py`. Model the module docstring, the budget
constants, the heartbeat discipline and the exception handling on
`src/app/triage.py` — read it first, then write this:

```python
"""AI draft completion: fill a pending skeleton's rounds from its official page.

Phase 2 of the AI pipeline. Sits ABOVE `db/` exactly like `triage.py` and
`discovery.py`: it imports `domain/`, `app.llm`, `app.fetching` and
`db.service`, and nothing in `db/` imports it. This module is the RUN ORDER
only -- which drafts, in what sequence, and what a failure at each step costs.
The prompt and the merge are pure (`domain/round_completion.py`); the rule that
decides whether a proposed round may exist at all is pure too
(`domain/round_evidence.py`), and that separation is the point: the safety
property is testable without a database, a network or a key.

WHAT PHASE 1 LEFT. A skeleton draft has legs, a cast and `rounds: []`, always,
because a round is the one promise this app makes to a user -- "a deadline it
names is real" -- and phase 1 could only keep that promise by emitting none.
This fills them in, and the promise is kept a different way: EVERY round the
model proposes must quote the page text it read each timestamp from, and every
quote must be findable in the same text the model was given. What the code
decides, not what the prompt asked for. `verify_rounds` is this module's
`strip_rounds`.

NOTHING IS DROPPED SILENTLY. A rejected round is recorded with its reason on
the draft's own `completion_yaml` and rendered on its preview. A real deadline
quietly discarded is exactly as harmful as a fake one quietly kept: in both
cases the operator has no way to know to look.

THE FETCH IS THE WIDENING, AND IT IS PAID FOR. A draft's `official_url` is by
nature an arbitrary host, so this is the first fetch in this app that is not
host-pinned. Three things stand in for the pin: a host is fetched only after an
admin approved it by name (`FetchDomain`), only over https, and only when every
address it resolves to is public. The approval queue is the part a human is in,
and an unapproved host costs a skipped draft rather than a refused run -- the
draft stays a candidate and the next press picks it up.

WHAT IT NEVER DOES. It creates no concert: a completed draft is still a
`PendingDraft` whose preview a human presses commit on, so `import_commit`
stays the only write path into `concerts`. It never fetches `eventernote_url`
(Eventernote carries no ticket information at all, so the request could not
contain the answer). It never searches for a page: the URL is in the draft, or
the page is pasted by hand.

THE BUDGET, and it is the same shape as triage's. At most COMPLETION_DRAFT_CAP
fetch+call pairs per press whatever the queue's size, fetches SEQUENTIAL with a
pause (parallel requests at a third party is how an IP gets blocked), a TOTAL
deadline per fetch because httpx's timeout is per READ, and a wall clock over
the whole loop because the cap bounds the CALLS and only a clock bounds the
TIME. `heartbeat.beat()` per draft: the scheduler beats before `tick()` and
/healthz goes unhealthy at 180s, so a run that fetches fifteen pages with a
pause each would otherwise page the owner about a perfectly healthy app. The
loop genuinely is alive, so beating in it is honest.

It FLUSHES, never commits -- the scheduler's block owns the transaction and its
own rollback, and the run row's failure marking happens THERE, on a cleaned
transaction, or a rollback would restore the row to "requested" and a dead run
would re-fire fifteen fetches and fifteen paid calls every sixty seconds
forever. `SQLAlchemyError` is the one failure this module does not absorb: a
poisoned session cannot persist anything, so stepping over it would spend
fourteen more paid calls writing nothing at all.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlparse

import yaml
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.config import settings
from app.db.models import Notification, PendingDraft, TriageRun, User
from app.db.service import (
    approved_fetch_hosts,
    completion_candidates,
    ensure_user,
    note_fetch_domain,
)
from app.domain.page_text import html_to_text, normalize_page_text
from app.domain.round_completion import (
    completion_prompt,
    draft_leg_labels,
    merge_rounds,
    parse_completion_response,
)
from app.domain.round_evidence import verify_rounds
from app.fetching import ApprovedPublicHosts, fetch_html
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

COMPLETION_USER_AGENT = "dekimasen.app/1.0 (draft completion)"
# At most this many fetch+call pairs per press. Lower than triage's 25 because
# an official ticket page is a far bigger read than an Eventernote event page.
COMPLETION_DRAFT_CAP = 15
COMPLETION_DELAY_SECONDS = 1.0
FETCH_DEADLINE_SECONDS = 30.0
COMPLETION_BUDGET_SECONDS = 240.0


@dataclass
class CompletionReport:
    drafts_seen: int = 0
    completed: int = 0
    rounds_added: int = 0
    rounds_rejected: int = 0
    # A draft whose host is waiting on a human. Counted apart from `skipped`
    # for the reason SweepReport counts calendar skips apart: nothing failed,
    # somebody just has not answered yet, and the remedy is a click rather
    # than a fix.
    blocked_domains: int = 0
    skipped: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    budget_exhausted: bool = False


def draft_source_url(draft_text: str) -> str | None:
    """The page to read for this draft's rounds, or None.

    `official_url` first, then `source_url`. NEVER `eventernote_url`: that page
    has no ticket information on it at all, so fetching it would spend a
    request on a page that cannot contain the answer -- the same fact phase 1's
    draft prompt states to the model.
    """
    try:
        data = yaml.safe_load(draft_text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("official_url", "source_url"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return None


async def _fetch_page(url: str, hosts: set[str]) -> str:
    """One official page, under the approved-public policy."""
    return await fetch_html(
        url,
        policy=ApprovedPublicHosts(lambda host: host in hosts),
        user_agent=COMPLETION_USER_AGENT,
    )


async def complete_one(
    session: AsyncSession,
    row: PendingDraft,
    page_text: str,
    source_url: str,
    *,
    llm_chat=llm.chat,
) -> tuple[int, int, int, int]:
    """One draft, one call: propose rounds, keep the grounded ones.

    Shared by the batch runner and the paste fallback route, so the two cannot
    drift on what counts as a grounded round -- the fallback exists because the
    fetch declined, not because the rules are different when it does.

    Returns (rounds_added, rounds_rejected, tokens_in, tokens_out) and writes
    `completion_yaml` whether or not any round survived: the call was paid for,
    and a second press must not pay for it again.
    """
    page = normalize_page_text(page_text)
    reply = await llm_chat(*completion_prompt(row.draft_text, page))
    proposed, warnings = parse_completion_response(reply.text)
    for warning in warnings:
        log.warning("completion: draft %s: %s", row.id, warning)

    # UTC's date, not a local one: the plausibility window is ±2/+3 YEARS, so
    # a day's drift at the boundary cannot matter, and a naive datetime.now()
    # in a codebase whose one hard rule is aware-UTC is a bad example to leave
    # lying around (invariant 1).
    verdict = verify_rounds(
        proposed, page, draft_leg_labels(row.draft_text), datetime.now(UTC).date()
    )
    row.draft_text = merge_rounds(row.draft_text, [r.data for r in verdict.accepted])
    row.completion_yaml = yaml.safe_dump(
        {
            "source_url": source_url,
            "evidence": [dict(r.evidence) for r in verdict.accepted],
            "rejected": list(verdict.rejected) + list(warnings),
        },
        allow_unicode=True,
        sort_keys=False,
    )
    await session.flush()
    return len(verdict.accepted), len(verdict.rejected), reply.tokens_in, reply.tokens_out


def _finish(run: TriageRun, now: datetime, report: CompletionReport) -> None:
    """Copy the report onto the run row. Every count is written, zeros
    included: on this table a written 0 means "looked, found none" and NULL
    means "never got there"."""
    run.status = "done"
    run.finished_at = now
    run.drafts_completed = report.completed
    run.rounds_added = report.rounds_added
    run.rounds_rejected = report.rounds_rejected
    run.blocked_domains = report.blocked_domains
    run.skipped = report.skipped
    run.tokens_in = report.tokens_in
    run.tokens_out = report.tokens_out


async def _announce(session: AsyncSession, report: CompletionReport) -> None:
    """Queue ONE admin notice per run. Never sends a DM itself (invariant 4).

    Reuses kind="triage": it reports on a model's proposals exactly as phase
    1's notice does, and a second kind behaving identically would only be a
    second thing to remember to keep out of UNREPORTED_NOTE_KINDS.
    """
    body = (
        f"Draft completion finished: {report.completed} draft(s) completed, "
        f"{report.rounds_added} round(s) added, {report.rounds_rejected} rejected, "
        f"{report.blocked_domains} waiting on domain approval, "
        f"{report.skipped} skipped.\n"
        "Review: https://dekimasen.app/concerts/import/pending"
    )
    for admin_id in sorted(settings.admin_ids):
        # An admin who has never logged in has no users row, and
        # Notification.user_id is an FK to it. Guarded on absence rather than
        # calling ensure_user unconditionally: that refreshes the username and
        # would overwrite a real admin's name with this placeholder every run.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(user_id=admin_id, body=body, kind="triage"))


async def run_completion(
    session: AsyncSession,
    run: TriageRun,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] | None = None,
    llm_chat=llm.chat,
) -> CompletionReport:
    """Complete this run's requester's open skeleton drafts.

    `fetcher` and `llm_chat` are injected so tests never touch the network or
    spend a real key -- one seam per external system, the shape `run_sweep` and
    `run_triage` both use. The transaction stays the caller's: this flushes,
    never commits.
    """
    report = CompletionReport()
    run.started_at = now

    rows = await completion_candidates(session, run.requested_by)
    report.drafts_seen = len(rows)
    if not rows:
        # Nothing to complete, so nothing to pay for and nobody to tell.
        _finish(run, now, report)
        await session.flush()
        return report

    hosts = await approved_fetch_hosts(session)
    fetch = fetcher or (lambda url: _fetch_page(url, hosts))
    deadline = monotonic() + COMPLETION_BUDGET_SECONDS
    attempts = 0

    for index, row in enumerate(rows):
        # Checked at the TOP, before anything is fetched: the budget caps how
        # long the reminder tick is held, so the answer must be "stop" before
        # the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            log.warning(
                "completion: %.0fs budget spent after %d draft(s); %d left for the next press",
                COMPLETION_BUDGET_SECONDS, index, len(rows) - index,
            )
            break
        if attempts >= COMPLETION_DRAFT_CAP:
            log.info(
                "completion: cap (%d) reached; %d draft(s) left for the next press",
                COMPLETION_DRAFT_CAP, len(rows) - attempts,
            )
            break

        url = draft_source_url(row.draft_text)
        if not url:
            # No page to read. Left retryable on purpose: an editor can add the
            # URL, or paste the page, and the next press picks it up.
            report.skipped += 1
            continue
        host = (urlparse(url).hostname or "").lower()
        if not host:
            report.skipped += 1
            continue
        if host not in hosts:
            # Record it for a human and move on. NOT a failure: the remedy is a
            # click on /admin/fetch-domains, and the draft stays a candidate.
            await note_fetch_domain(session, host, url, now)
            report.blocked_domains += 1
            continue

        attempts += 1
        heartbeat.beat()
        try:
            await asyncio.sleep(COMPLETION_DELAY_SECONDS)
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                html = await fetch(url)
            added, rejected, tokens_in, tokens_out = await complete_one(
                session, row, html_to_text(html), url, llm_chat=llm_chat
            )
            report.tokens_in += tokens_in
            report.tokens_out += tokens_out
            report.rounds_added += added
            report.rounds_rejected += rejected
            report.completed += 1
        except SQLAlchemyError:
            # NOT one skipped draft. A failed flush POISONS the session, so
            # nothing after this point can persist -- absorbing it would pay for
            # up to fourteen more calls to write nothing and then close the run
            # out as "done". Re-raised BEFORE the generic handler below; the
            # scheduler's block rolls back and marks the row failed on a
            # cleaned transaction.
            log.exception("completion: the session is poisoned; abandoning the run")
            raise
        except Exception:
            # Fetch, LLM and parse failures are one thing here: a draft that did
            # not get completed. One must not cost the rest.
            log.exception("completion: could not complete draft %s from %s", row.id, url)
            report.skipped += 1
            continue

    _finish(run, now, report)
    await _announce(session, report)
    await session.flush()
    return report
```

Note the `fetcher` seam: tests pass a plain `async def f(url)`, production
passes None and gets the policy-bound fetch closed over this run's approved
host set.

- [ ] **Step 4: Run the tests**

```
uv run --isolated pytest tests/test_draft_completion_run.py -q
```
Expected: 10 passed. If the `_announce` assertion shape differs from
`test_triage_run.py`'s, align this test to that file rather than changing
`_announce`.

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/draft_completion.py tests/test_draft_completion_run.py
git commit -m "feat: the draft-completion runner"
```

---

### Task 8: scheduler dispatch on run kind

**Files:**
- Modify: `src/app/scheduler/loop.py` (the triage block, around lines 307–344)
- Test: `tests/test_draft_completion_tick.py`

**Interfaces:**
- Consumes: `run_completion` from Task 7, `TriageRun.kind` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_completion_tick.py`, modelled on
`tests/test_triage_tick.py` (read it first — reuse its fixtures and its
monkeypatching style verbatim):

```python
"""The tick picks up a completion run, and marks a dead one failed."""

from datetime import UTC, datetime

import pytest

from app.db.models import TriageRun
from app.db.service import request_triage

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_requested_completion_run_is_dispatched_to_run_completion(
    session, monkeypatch, tick_env
):
    calls = []

    async def fake_run_completion(s, run, now, **kw):
        calls.append(run.id)
        run.status = "done"
        from app.draft_completion import CompletionReport
        return CompletionReport()

    monkeypatch.setattr("app.scheduler.loop.run_completion", fake_run_completion)

    async def never(*a, **kw):
        raise AssertionError("a completion run must not go to run_triage")

    monkeypatch.setattr("app.scheduler.loop.run_triage", never)
    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    run = await request_triage(session, NOW, 1, kind="complete")
    await session.commit()
    await tick_env.tick()
    assert calls == [run.id]


@pytest.mark.asyncio
async def test_a_classify_run_still_goes_to_run_triage(session, monkeypatch, tick_env):
    calls = []

    async def fake_run_triage(s, run, now, **kw):
        calls.append(run.id)
        run.status = "done"
        from app.triage import TriageReport
        return TriageReport()

    monkeypatch.setattr("app.scheduler.loop.run_triage", fake_run_triage)
    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    run = await request_triage(session, NOW, 1)
    await session.commit()
    await tick_env.tick()
    assert calls == [run.id]


@pytest.mark.asyncio
async def test_a_dead_completion_run_is_marked_failed_on_a_cleaned_transaction(
    session, monkeypatch, tick_env
):
    # The failure mode this exists for: a rollback restores the row to
    # "requested", and a dead run then re-fires fifteen fetches and fifteen
    # paid calls every sixty seconds forever.
    async def explode(s, run, now, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.scheduler.loop.run_completion", explode)
    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    run = await request_triage(session, NOW, 1, kind="complete")
    run_id = run.id
    await session.commit()
    await tick_env.tick()

    session.expire_all()
    row = await session.get(TriageRun, run_id)
    assert row.status == "failed"
```

`tick_env` stands in for whatever `tests/test_triage_tick.py` uses to drive one
tick — copy that fixture's usage exactly rather than inventing one.

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_draft_completion_tick.py -q
```
Expected: `AttributeError: module 'app.scheduler.loop' has no attribute
'run_completion'`.

- [ ] **Step 3: Dispatch on the kind**

In `src/app/scheduler/loop.py`, add to the imports beside `from app.triage
import run_triage`:

```python
from app.draft_completion import run_completion
```

Then, inside the existing triage block, replace the single
`triage_report = await run_triage(...)` call and its log line with:

```python
            if triage_run is not None:
                # ONE run per tick whatever its kind: pending_triage_run asks
                # for the oldest of any kind, so the classify and completion
                # halves serialize against each other by construction and
                # neither can starve the reminder tick behind the other.
                if triage_run.kind == "complete":
                    completion_report = await run_completion(session, triage_run, now)
                    log.info(
                        "completion run %d: %d drafts completed, %d rounds added, "
                        "%d rejected, %d waiting on a domain, %d skipped",
                        triage_run_id, completion_report.completed,
                        completion_report.rounds_added, completion_report.rounds_rejected,
                        completion_report.blocked_domains, completion_report.skipped,
                    )
                else:
                    triage_report = await run_triage(session, triage_run, now)
                    log.info(
                        "triage run %d: %d leads, %d dismissals proposed, %d drafts, %d skipped",
                        triage_run_id, triage_report.leads_seen, triage_report.dismissals,
                        triage_report.drafts, triage_report.skipped,
                    )
```

Keep the surrounding `try` / `except` / `mark_triage_failed` block byte-for-byte
as it is — including the comment explaining why `triage_run_id` is captured
before the run. Both kinds need that path identically.

- [ ] **Step 4: Run the tests**

```
uv run --isolated pytest tests/test_draft_completion_tick.py tests/test_triage_tick.py -q
```
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/scheduler/loop.py tests/test_draft_completion_tick.py
git commit -m "feat: the tick dispatches a picked-up run on its kind"
```

---

### Task 9: the button, the request route, and the pending-list callout

**Files:**
- Modify: `src/app/web/routes/imports.py`
- Modify: `src/app/web/templates/import_pending.html`
- Test: `tests/test_draft_completion_web.py`

**Interfaces:**
- Consumes: `request_triage(kind=)`, `pending_triage_run(kind=)`,
  `pending_fetch_domain_count`, `completion_candidates` (Task 6).
- Produces: `POST /concerts/import/pending/complete`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_completion_web.py`. Read
`tests/test_triage_run.py` and any existing `tests/test_import_*.py` first for
the app/client fixture and the sign-in helper; reuse them exactly.

```python
"""The completion button, its gate, and what the pending list says about it."""

import pytest
from sqlalchemy import select

from app.db.models import TriageRun


@pytest.mark.asyncio
async def test_the_button_is_absent_when_the_flag_is_off(admin_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", False)
    body = (await admin_client.get("/concerts/import/pending")).text
    assert "/concerts/import/pending/complete" not in body


@pytest.mark.asyncio
async def test_the_button_is_present_for_an_admin_when_the_flag_is_on(
    admin_client, monkeypatch
):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    body = (await admin_client.get("/concerts/import/pending")).text
    assert "/concerts/import/pending/complete" in body


@pytest.mark.asyncio
async def test_a_plain_editor_never_sees_the_button(editor_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    body = (await editor_client.get("/concerts/import/pending")).text
    assert "/concerts/import/pending/complete" not in body


@pytest.mark.asyncio
async def test_a_plain_editor_pressing_it_anyway_gets_403(editor_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    r = await editor_client.post("/concerts/import/pending/complete")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_pressing_it_queues_one_completion_run(admin_client, session, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    r = await admin_client.post("/concerts/import/pending/complete", follow_redirects=False)
    assert r.status_code == 303
    runs = (await session.execute(select(TriageRun))).scalars().all()
    assert [run.kind for run in runs] == ["complete"]


@pytest.mark.asyncio
async def test_pressing_it_twice_queues_one_run(admin_client, session, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    await admin_client.post("/concerts/import/pending/complete")
    await admin_client.post("/concerts/import/pending/complete")
    runs = (await session.execute(select(TriageRun))).scalars().all()
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_waiting_domains_are_called_out_on_the_list(
    admin_client, session, monkeypatch
):
    from datetime import UTC, datetime

    from app.db.service import note_fetch_domain

    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", datetime.now(UTC))
    await session.commit()
    body = (await admin_client.get("/concerts/import/pending")).text
    assert "/admin/fetch-domains" in body
```

If `admin_client` / `editor_client` fixtures do not exist, build them from
whatever `tests/` already uses to sign a request in as an admin vs a
non-admin editor — `grep -rn "require_admin" tests/ | head` will show the
pattern.

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_draft_completion_web.py -q
```
Expected: the POST 404s (route does not exist).

- [ ] **Step 3: Add the route**

In `src/app/web/routes/imports.py`, add to the service imports
`pending_fetch_domain_count`, `pending_triage_run`, `request_triage`, and add
`require_admin` to the auth imports (check what `routes/discoveries.py` imports
it from and match).

Register the route **before** `import_pending_detail` — `/pending/complete`
would otherwise be captured by `/pending/{pending_id}`, which is the same
literal-vs-template ordering rule that makes `imports.py` register before
`concerts.py` (CLAUDE.md). `pending_id: int` would reject "complete" with a 422,
not fall through, so the order is load-bearing rather than merely tidy.

```python
@router.post("/pending/complete")
async def import_pending_complete(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Ask the scheduler for an AI completion pass over this admin's own open
    skeleton drafts.

    Same request/drain shape as the triage button and for the same reason: a
    run that fetches up to fifteen official pages and makes fifteen LLM calls
    is far too slow for an HTTP request to hold, so this writes a row the next
    tick picks up. `request_triage` is idempotent per kind while one is still
    "requested", so a double-press queues one pass.

    Admin, not editor: the press spends real money, exactly as the triage
    button does. 303, never 307 -- the POST must not be replayed against the
    page it lands on.
    """
    if not settings.triage_enabled:
        raise HTTPException(status_code=404)
    await request_triage(session, datetime.now(UTC), user.id, kind="complete")
    await session.commit()
    return RedirectResponse("/concerts/import/pending", status_code=303)
```

Check `settings` and `HTTPException` are imported in this module already; add
them if not.

- [ ] **Step 4: Feed the template**

In `import_pending_list`, add to the template context:

```python
        # The completion button and its two status lines. Gated on the flag
        # ITSELF, not merely on the route's existence -- a deploy that has not
        # opted in should not see a control that spends a real key per press --
        # and on admin, because pressing it costs money.
        "can_complete": settings.triage_enabled and user.id in settings.admin_ids,
        "completion_pending": await pending_triage_run(session, kind="complete") is not None,
        "waiting_domains": await pending_fetch_domain_count(session),
```

Check how `user.id in settings.admin_ids` is normally spelled in this codebase
(`grep -rn "admin_ids" src/app/web/ | head`) and match it — there may be an
`is_admin` helper on `SessionUser`.

- [ ] **Step 5: Add the button and callout to `import_pending.html`**

Insert after the `.lede` block:

```jinja
{% if can_complete %}
{#- The AI completion button. Rounds are the one thing phase 1 deliberately
    never fills, and this is where they get filled: it reads each draft's own
    official_url and proposes rounds, keeping only those it can find quoted on
    that page. Gated on the flag itself, not merely on the route existing. -#}
<form method="post" action="/concerts/import/pending/complete" style="margin:1rem 0">
  <button class="act" type="submit" {% if completion_pending %}disabled{% endif %}>
    {{ _("Complete drafts with AI") }}</button>
  {% if completion_pending %}
  <span class="dim">{{ _("Completion requested — the scheduler will start it within a minute.") }}</span>
  {% else %}
  <span class="dim">{% trans %}Reads each draft's official ticket page and proposes rounds, keeping only
    deadlines it can quote from that page. Costs a few cents per press.{% endtrans %}</span>
  {% endif %}
</form>
{% if waiting_domains %}
<div class="banner warn">
  {% trans n=waiting_domains %}One website is waiting for your approval before its page can be
  read.{% pluralize %}{{ n }} websites are waiting for your approval before their pages can be
  read.{% endtrans %}
  <a href="/admin/fetch-domains">{{ _("Review them") }}</a>
</div>
{% endif %}
{% endif %}
```

- [ ] **Step 6: Run the tests**

```
uv run --isolated pytest tests/test_draft_completion_web.py -q
```
Expected: 7 passed.

- [ ] **Step 7: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/web/routes/imports.py src/app/web/templates/import_pending.html tests/test_draft_completion_web.py
git commit -m "feat: the Complete drafts button and its blocked-domain callout"
```

---

### Task 10: evidence on the preview, and the paste fallback

**Files:**
- Modify: `src/app/web/routes/imports.py`
- Modify: `src/app/web/templates/_editor_round_card.html`
- Modify: `src/app/web/templates/import_preview.html`
- Test: `tests/test_draft_completion_preview.py`

**Interfaces:**
- Consumes: `complete_one` (Task 7), `PendingDraft.completion_yaml` (Task 1).
- Produces: `POST /concerts/import/pending/{pending_id}/complete`;
  `_draft_preview_response(..., completion: dict | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_draft_completion_preview.py`:

```python
"""Evidence renders where the proofreading happens, and the paste fallback
works when everything else declined."""

import pytest
import yaml

from app.db.models import PendingDraft

COMPLETED = """\
title: 例）ライブ
performances:
- label: Day 1
  venue: Zepp Haneda
rounds:
- label: 1次先行抽選
  kind: lottery
  apply_closes_jst: 2026-01-10 23:59
"""

RECORD = yaml.safe_dump(
    {
        "source_url": "https://eplus.jp/x",
        "evidence": [{"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}],
        "rejected": ["round '2次先行': the quote for apply_closes_jst is not on the page"],
    },
    allow_unicode=True,
)


async def _seed(session, user_id, **over):
    row = PendingDraft(draft_text=COMPLETED, title="t", created_by=user_id, **over)
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_evidence_renders_beside_the_round_it_grounds(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id, completion_yaml=RECORD)
    body = (await admin_client.get(f"/concerts/import/pending/{row.id}")).text
    assert "申込締切 2026年1月10日(土)23:59" in body


@pytest.mark.asyncio
async def test_a_rejected_round_is_reported_on_the_preview(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id, completion_yaml=RECORD)
    body = (await admin_client.get(f"/concerts/import/pending/{row.id}")).text
    assert "is not on the page" in body


@pytest.mark.asyncio
async def test_a_draft_with_no_completion_record_renders_exactly_as_before(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id)
    body = (await admin_client.get(f"/concerts/import/pending/{row.id}")).text
    assert body.count("evidence-quote") == 0


@pytest.mark.asyncio
async def test_the_concert_editor_surfaces_render_no_evidence_block(admin_client):
    # The round card is shared with concert_new/concert_edit. Neither passes
    # evidence, and neither may grow a block because this feature exists.
    body = (await admin_client.get("/concerts/new")).text
    assert "evidence-quote" not in body


@pytest.mark.asyncio
async def test_pasting_a_page_completes_the_draft(
    admin_client, session, admin_user_id, monkeypatch
):
    from app.llm import LlmReply

    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def fake_chat(system, user, **kw):
        assert "申込締切" in user  # the pasted page reached the prompt
        return LlmReply(
            text=(
                "rounds:\n  - label: 1次先行抽選\n    kind: lottery\n"
                "    apply_closes_jst: 2026-01-10 23:59\n"
                "    evidence:\n"
                '      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"\n'
            ),
            tokens_in=10,
            tokens_out=5,
        )

    monkeypatch.setattr("app.draft_completion.llm.chat", fake_chat)
    row = await _seed(session, admin_user_id)
    row.draft_text = "title: x\nperformances: []\nrounds: []\n"
    await session.commit()

    r = await admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "1次先行抽選 申込締切 2026年1月10日(土)23:59"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    refreshed = await session.get(PendingDraft, row.id)
    assert yaml.safe_load(refreshed.draft_text)["rounds"][0]["label"] == "1次先行抽選"


@pytest.mark.asyncio
async def test_an_oversized_paste_is_refused_before_any_call(
    admin_client, session, admin_user_id, monkeypatch
):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def explode(*a, **kw):
        raise AssertionError("an oversized paste must not reach the model")

    monkeypatch.setattr("app.draft_completion.llm.chat", explode)
    row = await _seed(session, admin_user_id)
    r = await admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "x" * 150_001},
        follow_redirects=False,
    )
    assert r.status_code == 422
```

`admin_user_id` must be the discord id the `admin_client` fixture signs in as —
`PendingDraft.created_by` is scoped by `_own_open_pending`, so a row created by
anyone else 404s. Find it in the fixture and use it.

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_draft_completion_preview.py -q
```
Expected: assertion failures on the evidence strings, 404/405 on the POST.

- [ ] **Step 3: Thread the completion record into the preview**

In `_draft_preview_response`, add a parameter and two context keys:

```python
async def _draft_preview_response(
    request: Request,
    user: SessionUser,
    session: AsyncSession,
    parsed: ParsedConcert,
    pending_id: int | None = None,
    completion: dict | None = None,
) -> HTMLResponse:
```

and in the returned context dict:

```python
            # What an AI completion pass read, when one ran on this row. None
            # for a fresh paste and for a draft nobody completed, which is what
            # keeps the round card's output byte-identical on every other
            # surface. `evidence` is positional -- index i belongs to round i,
            # because both lists were written by the same merge.
            "evidence": (completion or {}).get("evidence") or [],
            "completion_rejected": (completion or {}).get("rejected") or [],
            "completion_source": (completion or {}).get("source_url") or "",
```

In `import_pending_detail`, parse the stored record and pass it:

```python
    completion = None
    if row.completion_yaml:
        try:
            loaded = yaml.safe_load(row.completion_yaml)
            completion = loaded if isinstance(loaded, dict) else None
        except yaml.YAMLError:
            # Proofreading scaffolding, not data anything depends on: a record
            # that no longer parses costs the quotes, never the preview.
            completion = None
    return await _draft_preview_response(
        request, user, session, parsed, pending_id=row.id, completion=completion
    )
```

Every OTHER caller of `_draft_preview_response` passes nothing and gets `None`.
Add `import yaml` to the module if it is not already there.

- [ ] **Step 4: Render it**

In `_editor_round_card.html`, add `evidence=none` to the macro signature's
final line and insert this block immediately before the closing `</div>` of
`.redit`:

```jinja
  {#- What an AI completion pass quoted for this round's timestamps, when one
      ran. Optional and absent everywhere else: concert_new and concert_edit
      pass nothing, so their cards render exactly as they did before this
      feature existed. `.edgecard` because it is ongoing state rather than
      something needing attention (the two-shape callout grammar). -#}
  {% if evidence %}
  <div class="edgecard ok evidence-quote">
    <p class="dim tiny">{{ _("Read from the ticket page:") }}</p>
    {% for fieldname, quote in evidence.items() %}
    <p class="tiny"><b>{{ fieldname }}</b> — {{ quote }}</p>
    {% endfor %}
  </div>
  {% endif %}
```

In `import_preview.html`'s rounds loop, pass the positional record and add the
rejection banner above the loop:

```jinja
  {% if completion_rejected %}
  <div class="banner warn">
    <p>{% trans %}The AI proposed these and I could not find them on the page — check them by
      hand before trusting them:{% endtrans %}</p>
    <ul>{% for reason in completion_rejected %}<li class="tiny">{{ reason }}</li>{% endfor %}</ul>
    {% if completion_source %}
    <p class="tiny">{{ _("Source:") }} <a href="{{ completion_source }}" rel="nofollow noopener">{{ completion_source }}</a></p>
    {% endif %}
  </div>
  {% endif %}
```

and inside the `{% for r in parsed.rounds %}` loop change the call to add:

```jinja
        evidence=(evidence[loop.index0] if evidence|length > loop.index0 else none),
```

Leave the `<template>` clone-row's `round_card(with_qualifiers=False)` call
untouched — a newly added row has no evidence by definition.

- [ ] **Step 5: Add the paste fallback route**

In `imports.py`, after `import_pending_discard`:

```python
# A pasted page is capped well below Starlette's hard 1MB per-field limit,
# which applies to EVERY Form field whatever an app constant says: Japanese
# costs 3 bytes a character in UTF-8, so 150k characters is ~450KB and stays
# clear of a wall that would otherwise arrive as an opaque failure.
MAX_PASTED_PAGE_CHARS = 150_000


@router.post("/pending/{pending_id}/complete")
async def import_pending_complete_one(
    request: Request,
    pending_id: int,
    page_text: str = Form(""),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Complete ONE draft from a page the operator pasted.

    The fallback for every way the automatic half declines -- no URL in the
    draft, a host nobody approved, a dead fetch, a page rendered by JavaScript.
    It needs no fetch and no approval, which is what makes it safe to keep the
    automatic half narrow.

    Inline rather than queued, unlike the batch button: one LLM call is a
    bounded wait in a request. It runs the SAME `complete_one` the batch runner
    does, so the two cannot drift on what counts as a grounded round.
    """
    if not settings.triage_enabled:
        raise HTTPException(status_code=404)
    row = await _own_open_pending(session, pending_id, user)
    if row is None:
        raise HTTPException(status_code=404)
    text = page_text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="paste the ticket page's text first")
    if len(text) > MAX_PASTED_PAGE_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"that page is longer than {MAX_PASTED_PAGE_CHARS} characters",
        )
    try:
        await complete_one(session, row, text, "(pasted by hand)")
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await session.commit()
    return RedirectResponse(f"/concerts/import/pending/{pending_id}", status_code=303)
```

Import `complete_one` from `app.draft_completion` and `LlmError` from `app.llm`.

- [ ] **Step 6: Add the paste box to the preview**

In `import_preview.html`, immediately after the rounds section's
`editor-actions` div, add (it must be OUTSIDE the main preview `<form>` — a
nested form is invalid HTML and the browser drops it):

```jinja
{% if pending_id and can_complete %}
<details class="fold" data-fold="paste-page">
  <summary>{{ _("Fill rounds from a page I paste") }}</summary>
  <form method="post" action="/concerts/import/pending/{{ pending_id }}/complete">
    <p class="dim tiny">{% trans %}Open the official ticket page, select all, and paste it here. Only
      deadlines quoted from what you paste will be kept.{% endtrans %}</p>
    <textarea name="page_text" rows="6" style="width:100%"></textarea>
    <button class="btn" type="submit">{{ _("Read this page") }}</button>
  </form>
</details>
{% endif %}
```

Add `"can_complete": settings.triage_enabled and <the admin test used in Task 9>`
to `_draft_preview_response`'s context so this renders only where the button does.

Check `import_preview.html`'s form structure first (`grep -n "</form>"`) to place
this after the main form closes.

- [ ] **Step 7: Run the tests**

```
uv run --isolated pytest tests/test_draft_completion_preview.py -q
uv run --isolated pytest -q -k "preview or import or editor or theme"
```
Expected: all pass. The theme/token sweep tests matter here — they forbid a
local backdrop-click handler on a dialog and pin radius values; this task adds
neither, but run them to be sure the round-card edit did not trip one.

- [ ] **Step 8: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/web/routes/imports.py src/app/web/templates/_editor_round_card.html src/app/web/templates/import_preview.html tests/test_draft_completion_preview.py
git commit -m "feat: evidence on the preview, and the paste fallback"
```

---

### Task 11: `/admin/fetch-domains`

**Files:**
- Create: `src/app/web/routes/fetch_domains.py`
- Create: `src/app/web/templates/admin_fetch_domains.html`
- Modify: `src/app/web/app.py` (register the router)
- Modify: `src/app/web/templates/preferences.html` (the admin-tools link)
- Test: `tests/test_admin_fetch_domains.py`

**Interfaces:**
- Consumes: `fetch_domain_rows`, `decide_fetch_domain` (Task 6).
- Produces: `GET /admin/fetch-domains`,
  `POST /admin/fetch-domains/{domain_id}/approve`,
  `POST /admin/fetch-domains/{domain_id}/decline`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_admin_fetch_domains.py`:

```python
"""The approval screen: admin-only, English-only, and one decision per host."""

from datetime import UTC, datetime

import pytest

from app.db.models import FetchDomain
from app.db.service import note_fetch_domain

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_non_admin_editor_is_refused(editor_client):
    assert (await editor_client.get("/admin/fetch-domains")).status_code == 403


@pytest.mark.asyncio
async def test_the_page_lists_a_waiting_host_and_what_wanted_it(admin_client, session):
    await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/sf/detail/1", NOW)
    await session.commit()
    body = (await admin_client.get("/admin/fetch-domains")).text
    assert "eplus.jp" in body
    assert "https://eplus.jp/sf/detail/1" in body


@pytest.mark.asyncio
async def test_approving_makes_the_host_fetchable(admin_client, session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    await session.commit()
    r = await admin_client.post(
        f"/admin/fetch-domains/{row.id}/approve", follow_redirects=False
    )
    assert r.status_code == 303
    session.expire_all()
    assert (await session.get(FetchDomain, row.id)).approved_at is not None


@pytest.mark.asyncio
async def test_declining_sticks(admin_client, session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await session.commit()
    await admin_client.post(f"/admin/fetch-domains/{row.id}/decline")
    # A second, contradictory press must not flip a decision already made.
    await admin_client.post(f"/admin/fetch-domains/{row.id}/approve")
    session.expire_all()
    refreshed = await session.get(FetchDomain, row.id)
    assert refreshed.declined_at is not None and refreshed.approved_at is None


@pytest.mark.asyncio
async def test_an_unknown_id_404s(admin_client):
    assert (await admin_client.post("/admin/fetch-domains/999/approve")).status_code == 404


@pytest.mark.asyncio
async def test_the_page_is_linked_from_preferences(admin_client):
    assert "/admin/fetch-domains" in (await admin_client.get("/preferences")).text
```

- [ ] **Step 2: Run it and watch it fail**

```
uv run --isolated pytest tests/test_admin_fetch_domains.py -q
```
Expected: 404s everywhere.

- [ ] **Step 3: Write the router**

Create `src/app/web/routes/fetch_domains.py`, modelled closely on
`src/app/web/routes/discoveries.py` (read it first for the exact import set,
the `templates` module global and the `require_admin` dependency):

```python
"""Which websites the AI completion pass may read.

  GET  /admin/fetch-domains                    every host it has wanted, and its verdict
  POST /admin/fetch-domains/{id}/approve       yes, read pages from this host
  POST /admin/fetch-domains/{id}/decline       no, and stop asking

Its own module rather than a section of `admin.py`, for the reason
`discoveries.py` is its own: a router registers whole, and this is a fifth
unrelated operational concern beside the delivery log, the broadcast, the
catalogue round-trip and the discovery queue.

WHY A HUMAN IS IN THIS LOOP. Every other fetch this app makes is pinned to one
host named in code. A draft's `official_url` cannot be -- an official page is
by definition somebody else's domain -- so the pin is replaced by a person: a
host is fetched only after an admin approved it by name, and a redirect off an
approved host onto an unapproved one is refused on the hop
(`fetching.ApprovedPublicHosts`). A declined host is never proposed again,
because an approval queue that keeps re-asking becomes one nobody reads.

English-only and NOT wrapped in `_()`, exactly like /admin/deliveries and
/admin/discoveries; only the Preferences LINK is translated.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import decide_fetch_domain, fetch_domain_rows
from app.web.deps import SessionUser, get_session, require_admin

router = APIRouter()
templates = None  # set by web/app.py, as every other router here does it


@router.get("/admin/fetch-domains", response_class=HTMLResponse)
async def fetch_domains_page(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "admin_fetch_domains.html",
        {"user": user, "rows": await fetch_domain_rows(session)},
    )


async def _decide(
    session: AsyncSession, domain_id: int, approve: bool, user: SessionUser
) -> RedirectResponse:
    """One verdict, or a 404. Shared by both POSTs so they cannot drift on
    what an already-decided host does -- `decide_fetch_domain` returns False
    for unknown AND already-decided, and both mean "there is nothing here to
    decide", which is a 404 either way (the same rule `_own_open_pending`
    follows for three different absences)."""
    if not await decide_fetch_domain(session, domain_id, approve, datetime.now(UTC), user.id):
        raise HTTPException(status_code=404)
    await session.commit()
    return RedirectResponse("/admin/fetch-domains", status_code=303)


@router.post("/admin/fetch-domains/{domain_id}/approve")
async def approve_domain(
    domain_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(session, domain_id, True, user)


@router.post("/admin/fetch-domains/{domain_id}/decline")
async def decline_domain(
    domain_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(session, domain_id, False, user)
```

Check `app.web.deps`' real module path and export names against
`routes/discoveries.py` — if that file imports from somewhere else, match it.

Note: `test_declining_sticks` expects the contradictory second press to leave
the row declined. `decide_fetch_domain` returns False there, so this route
404s — which the test does not assert on, only the row's state. That is
correct behaviour, not a bug to work around.

- [ ] **Step 4: Write the template**

Create `src/app/web/templates/admin_fetch_domains.html`, modelled on
`admin_discoveries.html` (read it for the wrapper markup and table classes):

```jinja
{% extends "base.html" %}
{% block title %}fetch domains — dekimasen.app{% endblock %}
{% block content %}
<div class="lede">
  <h1>Websites the AI may read</h1>
  <p>When a draft's official ticket page sits on a website nobody has approved yet,
    the completion pass records it here and skips that draft. Approve a website
    and its pages can be read from then on; decline it and it will not be asked
    about again. Every other fetch this app makes is locked to a single site
    named in the code — this list is what stands in for that lock.</p>
</div>

{% if rows %}
<table class="dtable" style="margin-top:1.5rem">
  <thead><tr><th>Website</th><th>First wanted for</th><th>Status</th><th></th></tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td><b>{{ r.host }}</b></td>
    <td class="tiny dim">{{ r.first_seen_url }}</td>
    <td>
      {% if r.approved_at %}approved
      {% elif r.declined_at %}declined
      {% else %}<b>waiting</b>{% endif %}
    </td>
    <td>
      {% if not r.approved_at and not r.declined_at %}
      <form method="post" action="/admin/fetch-domains/{{ r.id }}/approve" style="display:inline">
        <button class="act" type="submit">Approve</button>
      </form>
      <form method="post" action="/admin/fetch-domains/{{ r.id }}/decline" style="display:inline">
        <button class="act no" type="submit">Decline</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="clock" style="display:block; margin-top:1.5rem">
  No website has been asked about yet.</p>
{% endif %}
{% endblock %}
```

Verify `.dtable` exists (`grep -n "dtable" src/app/web/static/style.css`); if
not, use whatever table class `admin_deliveries.html` uses.

- [ ] **Step 5: Register the router and link it from Preferences**

In `src/app/web/app.py`, beside the `discoveries_routes` registration:

```python
    # /admin/fetch-domains, likewise a literal path -- order-independent. Its
    # own router for the reason discoveries.py has one: a router registers
    # whole, and this is a fifth unrelated operational concern.
    fetch_domain_routes.templates = templates
    app.include_router(fetch_domain_routes.router)
```

with the matching import at the top of the file (follow the existing
`from app.web.routes import ... as discoveries_routes` style).

In `preferences.html`'s admin-tools section, after the Discoveries row:

```jinja
    <div class="subrow two">
      <span class="nm3"><a href="/admin/fetch-domains">{{ _("Fetch domains") }}</a></span>
      <span class="sw"><span class="clock">{{ _("Websites the AI draft completion may read pages from.") }}</span></span>
    </div>
```

- [ ] **Step 6: Run the tests**

```
uv run --isolated pytest tests/test_admin_fetch_domains.py -q
uv run --isolated pytest -q -k "preferences or error_pages"
```
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/web/routes/fetch_domains.py src/app/web/templates/admin_fetch_domains.html src/app/web/app.py src/app/web/templates/preferences.html tests/test_admin_fetch_domains.py
git commit -m "feat: /admin/fetch-domains, the approval queue for AI page reads"
```

---

### Task 12: catalogues, docs, and the full-suite gate

**Files:**
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`
- Modify: `src/app/translations/zh/LC_MESSAGES/messages.po`
- Modify: `CLAUDE.md`
- Modify: `docs/deploy.md`
- Modify: `WISHLIST.md`

**Interfaces:**
- Consumes: every user-facing string added in Tasks 9 and 10.
- Produces: nothing code-level.

- [ ] **Step 1: Extract and update both catalogues**

```
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill every new and fuzzy msgstr BY HAND in both `.po` files, then delete
`messages.pot` (gitignored, regenerable). A fuzzy entry counts as untranslated
— `i18n.py` compiles with `use_fuzzy=False` — so remove the `#, fuzzy` marker
once you have checked the string.

The new msgids are the ones added in Tasks 9 and 10: "Complete drafts with AI",
the two status lines, the waiting-domains plural, "Review them", "Read from the
ticket page:", the rejection banner sentence, "Source:", "Fill rounds from a
page I paste", its instruction sentence, "Read this page", "Fetch domains" and
its Preferences blurb. Nothing from `admin_fetch_domains.html` — that page is
English-only by the same rule as the other admin pages.

- [ ] **Step 2: Run the catalogue test**

```
uv run --isolated pytest tests/test_i18n_catalogues.py -q
```
Expected: pass. It extracts every msgid in-process and fails on anything
untranslated, and it checks placeholder integrity — a `{n}` dropped from a
plural is caught here.

- [ ] **Step 3: Update CLAUDE.md**

Add to the layout section, after the `src/app/triage.py` entry:

```
- `src/app/draft_completion.py` — phase 2: filling a pending skeleton's rounds
  from the official page its own draft names. Same layer and discipline as
  `triage.py`, and it reuses that feature's `TriageRun` row via a `kind`
  column, so the request/pickup handshake and the re-stamp-after-rollback rule
  exist once. **The rule that replaces `strip_rounds` is EVIDENCE GROUNDING**:
  the model must quote the page text it read each timestamp from, and
  `domain/round_evidence.py` drops any round whose quote it cannot find in the
  same text the model was given — plus the nastier case, a quote that IS on the
  page but does not carry that timestamp. Nothing is dropped silently; every
  rejection reaches the preview with its reason, because a real deadline
  quietly discarded is as harmful as a fake one quietly kept. `domain/
  page_text.py` produces that text ONCE for both the prompt and the check —
  two normalizations would make the guarantee theatre. The completion pass
  rewrites exactly ONE key of the stored draft, `rounds:`, and preserves the
  leading comment prefix, because phase 1's duplicate containment matches the
  whole `# source: ...` line and a naive YAML round-trip drops it. Evidence
  lives BESIDE the draft (`PendingDraft.completion_yaml`), never inside it: a
  draft is a document that gets committed into `concerts`.
- **`fetching.py` takes a host POLICY, not a host string.** `PinnedHost` is
  the original guard (the ramen.events importer, the sweep, phase-1 triage);
  `ApprovedPublicHosts` is the completion pass's, and it is the FIRST fetch in
  this app that is not pinned to a host named in code — because a draft's
  `official_url` is by nature somebody else's domain. Three things stand in for
  the pin: https only, every resolved address must be global (the Lightsail
  metadata endpoint is a real target), and the host must be in `fetch_domains`
  as approved by an admin. The same policy runs on every redirect hop, so a
  redirect off an approved host onto an unapproved one is refused. Don't add a
  third policy or a "just this once" bypass; the paste fallback
  (`POST /concerts/import/pending/{id}/complete`) is what exists for the cases
  the policy declines, and it needs no fetch at all.
```

Add to the invariants, under invariant 4's neighbourhood or as a note on the
completion feature — keep it to the two facts above; do not restate the whole
spec.

- [ ] **Step 4: Update `docs/deploy.md`**

Add a section after "Calibrating the first AI-triage run":

```
### The completion pass, and its approval queue

Phase 2 brings one migration (columns plus `fetch_domains`) and no new env
vars — it reuses `TRIAGE_ENABLED` and the DeepSeek keys.

The first press will complete NOTHING, and that is correct: every host it
wants is unknown, so it records them and stops. Open /admin/fetch-domains,
approve the ticket vendors and franchise sites you recognise, decline the
rest, and press again. Thereafter only genuinely new hosts interrupt.

Two things to check on the first real completion:

- Read the quotes, not just the timestamps. Every round the pass keeps carries
  the line it was read from, rendered under the round on the preview. A round
  whose quote does not say what the timestamp says is the failure this feature
  is built to make visible.
- Read the rejection banner. A rejected round is often a REAL deadline the
  model quoted loosely rather than an invented one; those are the ones to type
  in by hand.

If a page comes back empty or useless (a JavaScript-rendered vendor page is
the usual cause), use "Fill rounds from a page I paste" on that draft: select
all on the real page in a browser, paste, and the same rules apply to what you
pasted.
```

- [ ] **Step 5: Update WISHLIST.md**

Move entry #3 ("AI completion of a skeleton draft (AI triage, phase 2)") into
the Shipped section with today's date, following the format of the phase-1
entry immediately above it. Then do the full revision pass CLAUDE.md requires:
re-rank every remaining Proposed entry by impact and reconsider which are still
useful. In particular, re-read #1 (round watch) — phase 1 made it worse by
queueing skeletons with empty ladders, and phase 2 makes filling one cheap, so
its rank may genuinely move. Say so explicitly in its entry either way; a
position that did not change still gets a sentence recording that it was
re-read and why.

- [ ] **Step 6: Run the full suite, in halves, in the foreground**

```
uv run --isolated pytest -q tests/test_a*.py tests/test_[b-m]*.py
uv run --isolated pytest -q tests/test_[n-z]*.py
uv run --isolated ruff check .
```
Expected: green, with roughly 60 more tests than the 2287 phase 1 finished on.
Do NOT background these runs.

- [ ] **Step 7: Commit**

```bash
git add src/app/translations CLAUDE.md docs/deploy.md WISHLIST.md
git commit -m "docs: catalogues, runbook and wishlist for AI draft completion"
```

---

## Self-review notes for the executing agent

Three things this plan deliberately does NOT decide, because they need the
running app rather than a guess:

1. **The exact fixture names** in Tasks 9–11 (`admin_client`, `editor_client`,
   `admin_user_id`, `tick_env`). Find the real ones in `tests/conftest.py` and
   the existing triage/import tests, and use those. Inventing a parallel
   fixture is a plan failure, not a shortcut.
2. **Whether `SessionUser` already has an admin predicate.** Task 9 spells the
   check as `user.id in settings.admin_ids`; if the codebase has a helper, use
   it and use it in all three places consistently.
3. **The `.dtable` class name** in Task 11's template. Match whatever
   `admin_deliveries.html` actually uses.

And one property worth re-checking by hand after Task 10, because no test can
assert it convincingly: open a completed draft's preview and confirm the quote
under each round is genuinely enough to check that round WITHOUT opening the
ticket page. That is the entire user-facing point of the feature; if it is not
true, the evidence rendering needs another pass, not the verification rules.
