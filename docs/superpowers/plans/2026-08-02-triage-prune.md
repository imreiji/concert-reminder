# Triage phase 1: prune by imported list — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste an agent-authored list of Eventernote event ids grouped by
dismissal reason, see exactly what it would do, then apply it — so 300 leads are
pruned in one reviewed act instead of 300 form submits.

**Architecture:** A pure parser in `domain/prune_list.py`, a plan builder in
`db/service.py` that joins the parsed file against `discovered_events`, and three
routes in `routes/discoveries.py` mirroring `/admin/import/tags` exactly: a paste
form, a plan that writes nothing, and an apply that RE-PARSES from the pasted
text rather than trusting the browser.

**Tech Stack:** PyYAML (`safe_load` only), SQLAlchemy 2.0 async, FastAPI, Jinja2.

**Spec:** `docs/superpowers/specs/2026-08-02-triage-leads-design.md`, Phase 1.

## Global Constraints

- `/admin/discoveries` and everything added here is **admin-only, English-only,
  and NOT wrapped in `_()`**. No gettext calls, no `.po` edits.
- **`yaml.safe_load` ONLY.** This is pasted text from outside the app.
- This surface **writes only to `discovered_events`**. It never creates a
  concert; `import_commit` stays the only write path into `concerts`.
- Dismissals go through **`dismiss_lead`**, one call per lead — never a bulk
  UPDATE. A second writer drifts from the single-writer rule exactly as a second
  `record_round_outcome` would.
- **Nothing un-dismisses.** No reverse operation, no `restore` key.
- `POST` handlers that redirect use **303, never 307**. The plan and apply
  handlers here render HTML instead (mirroring `import_tags_preview`), so this
  applies only if you add a redirect.
- The file keys on **`eventernote_event_id`** (the external id in the copy
  block's URLs), never the internal `DiscoveredEvent.id`.
- No migration in this phase. No schema change at all.
- `uv run --isolated pytest -q` must pass and `uv run --isolated ruff check .`
  must be clean. Always `--isolated` — an external process holds a `.venv` lock.

---

### Task 1: The parser

**Files:**
- Create: `src/app/domain/prune_list.py`
- Test: `tests/test_prune_list.py`

**Interfaces:**
- Produces: `parse_prune_list(text: str) -> PruneList` and
  `PruneListError(Exception)`. `PruneList` is a frozen dataclass with
  `entries: tuple[PruneEntry, ...]` and `warnings: tuple[str, ...]`;
  `PruneEntry` has `event_id: str` and `reason: DismissReason`.
  Task 2 consumes both.

**Why a pure module:** it does no I/O and belongs beside `tags_yaml.py` and
`yaml_import.py`, which are the other two halves of the paste-a-file vocabulary.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from app.domain.prune_list import PruneListError, parse_prune_list
from app.domain.types import DismissReason


def test_a_list_parses_into_entries():
    got = parse_prune_list("""
dismiss:
  stage:
    - 481833
    - 481832
  release:
    - 466181
""")
    assert [(e.event_id, e.reason) for e in got.entries] == [
        ("481833", DismissReason.STAGE),
        ("481832", DismissReason.STAGE),
        ("466181", DismissReason.RELEASE),
    ]


def test_ids_are_strings_even_when_yaml_reads_them_as_ints():
    """`- 481833` is an int to YAML, and eventernote_event_id is a String
    column. Comparing int to str silently matches nothing, which would look
    like a stale file rather than a bug."""
    got = parse_prune_list("dismiss:\n  free:\n    - 481300\n")
    assert got.entries[0].event_id == "481300"
    assert isinstance(got.entries[0].event_id, str)


def test_an_unknown_reason_is_an_error_naming_the_key():
    """Not a warning. This file's whole purpose is to write a column whose
    value is that every row in it is a real judgment, so an unrecognised class
    must not become a silent skip."""
    with pytest.raises(PruneListError) as e:
        parse_prune_list("dismiss:\n  nonsense:\n    - 1\n")
    assert "nonsense" in str(e.value)


def test_the_same_id_under_two_reasons_is_refused():
    """Refused outright rather than resolved by ordering: last-one-wins would
    make the result depend on dict iteration order."""
    with pytest.raises(PruneListError) as e:
        parse_prune_list("dismiss:\n  stage:\n    - 42\n  release:\n    - 42\n")
    assert "42" in str(e.value)


def test_an_empty_or_missing_dismiss_block_is_an_error_not_a_no_op():
    """A file that parses to zero dismissals is almost always a mistake --
    wrong key, bad indentation -- and applying it cheerfully would report
    success for nothing."""
    for text in ("", "dismiss:\n", "something_else:\n  stage:\n    - 1\n"):
        with pytest.raises(PruneListError):
            parse_prune_list(text)


def test_a_non_list_under_a_reason_is_an_error():
    with pytest.raises(PruneListError):
        parse_prune_list("dismiss:\n  stage: 481833\n")


def test_yaml_that_is_not_a_mapping_is_an_error_not_a_crash():
    for text in ("- just\n- a\n- list\n", "plain string\n", "[1, 2]\n"):
        with pytest.raises(PruneListError):
            parse_prune_list(text)


def test_duplicate_id_under_the_SAME_reason_is_deduped_with_a_warning():
    """Harmless -- the second dismiss is a no-op anyway -- so warn rather than
    refuse, following the draft parser's warnings-over-failures philosophy."""
    got = parse_prune_list("dismiss:\n  stage:\n    - 7\n    - 7\n")
    assert len(got.entries) == 1
    assert any("7" in w for w in got.warnings)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_prune_list.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.prune_list'`

- [ ] **Step 3: Write the module**

Create `src/app/domain/prune_list.py`. Module docstring must say: pure, no I/O;
`yaml.safe_load` only because this is pasted text from outside the app; it keys
on the Eventernote event id because that is the only id the copy block exposes
to whoever writes the file.

```python
from __future__ import annotations

import dataclasses

import yaml

from app.domain.types import DismissReason


class PruneListError(Exception):
    """The file cannot be used. Unlike the draft parser, which prefers a
    warning and a skipped row, an unusable prune list must raise: every entry
    becomes a permanent dismissal, so a half-understood file is worse than
    no file."""


@dataclasses.dataclass(frozen=True)
class PruneEntry:
    event_id: str
    reason: DismissReason


@dataclasses.dataclass(frozen=True)
class PruneList:
    entries: tuple[PruneEntry, ...]
    warnings: tuple[str, ...]


def parse_prune_list(text: str) -> PruneList:
    ...
```

Implement to satisfy every test above. Keep `_VALID = {r.value for r in DismissReason}` derived from the enum rather than written out — a ninth reason must not need editing here.

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_prune_list.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/domain/prune_list.py tests/test_prune_list.py
git commit -m "feat: parse an agent-authored prune list"
```

---

### Task 2: The plan builder

**Files:**
- Modify: `src/app/db/service.py` (beside `open_leads` / `dismiss_lead`, ~line 7060)
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Consumes: `PruneList`, `PruneEntry` from Task 1.
- Produces: `plan_prune(session, prune) -> PrunePlan` and
  `apply_prune(session, plan, now) -> int`. `PrunePlan` is a frozen dataclass:
  `to_dismiss: tuple[PlannedDismissal, ...]` (each carrying the lead's id,
  event_id, title, date and reason), `unknown: tuple[str, ...]`,
  `already: tuple[PlannedDismissal, ...]`. Task 3 renders all three.

**The property that matters:** a plan writes NOTHING. Looking is not doing, and
the plan is rendered before a human has agreed to anything.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_plan_sorts_leads_into_dismiss_unknown_and_already(client):
    """Three buckets, all shown. An unknown id is usually a stale file and an
    already-dismissed lead is usually a re-paste -- both are worth seeing, and
    neither should stop the rest."""
    # seed: one open lead 111, one already-dismissed lead 222
    # prune list names 111, 222 and 999
    ...
    assert [d.event_id for d in plan.to_dismiss] == ["111"]
    assert [d.event_id for d in plan.already] == ["222"]
    assert plan.unknown == ("999",)


async def test_planning_writes_nothing(client):
    """Rendering a plan must leave the queue exactly as it found it."""
    # seed one open lead, build a plan naming it, do NOT apply
    ...
    assert lead.dismissed_at is None
    assert lead.dismiss_reason is None


async def test_apply_dismisses_with_each_lead_s_own_reason(client):
    # two leads under different reasons in one file
    ...
    assert reasons == {"111": "stage", "222": "release"}


async def test_apply_skips_the_already_dismissed_without_restamping(client):
    """dismiss_lead already returns False for these. The original reason and
    timestamp must survive -- a re-paste is not a re-decision."""
    ...
    assert lead.dismissed_at == original_ts
    assert lead.dismiss_reason == "free"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_admin_discoveries.py -q -k "plan or apply_prune"`
Expected: FAIL — `plan_prune` does not exist.

- [ ] **Step 3: Implement both**

`plan_prune` issues ONE query — `select(DiscoveredEvent).where(
DiscoveredEvent.eventernote_event_id.in_([e.event_id for e in prune.entries]))` —
and sorts the results, rather than a query per entry. 300 entries must not be 300
round trips.

`apply_prune` loops the plan's `to_dismiss` calling `dismiss_lead` per lead, and
returns how many were written. It does NOT re-derive the plan; the caller passes
one built from a fresh parse.

- [ ] **Step 4: Pin the query count**

Add a statement-count test in the style the codebase already uses for
`performer_clusters` — plan a list naming 20 leads and assert the SELECT count
does not grow with the entry count.

- [ ] **Step 5: Run and commit**

Run: `uv run --isolated pytest tests/test_admin_discoveries.py -q`

```bash
git add src/app/db/service.py tests/test_admin_discoveries.py
git commit -m "feat: plan a prune before writing any of it"
```

---

### Task 3: The routes and the page

**Files:**
- Modify: `src/app/web/routes/discoveries.py`
- Create: `src/app/web/templates/admin_prune.html`
- Modify: `src/app/web/templates/admin_discoveries.html` (link to it)
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.

Mirror `/admin/import/tags` — read `routes/admin.py:319-367` and
`templates/admin_import_tags.html` first and follow their shape.

- [ ] **Step 1: Write the failing tests**

```python
async def test_an_editor_cannot_reach_the_prune_page(client):
    """Signed in and unauthorized IS an error (invariant 5), and the write half
    is guarded too -- a page that only hides a form is not access control."""
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/discoveries/prune").status_code == 403
    assert client.post("/admin/discoveries/prune", data={"text": ""}).status_code == 403
    assert client.post("/admin/discoveries/prune/apply", data={"text": ""}).status_code == 403


async def test_the_plan_page_shows_all_three_buckets(client):
    ...


async def test_previewing_writes_nothing(client):
    """The whole point of plan-before-apply. Post to the PLAN route and assert
    the lead is untouched."""
    ...


async def test_apply_reparses_and_ignores_injected_ids(client):
    """The browser sends the FILE back, never a list of ids. A post carrying an
    extra lead id in any field must not dismiss it -- this is the property that
    makes the apply route safe to expose at all."""
    # post apply with the file naming lead 111, plus data={"lead_id": <222's id>,
    # "event_id": "222", "dismiss": "222"}
    ...
    assert lead_222.dismissed_at is None


async def test_a_bad_file_shows_the_error_and_writes_nothing(client):
    ...
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_admin_discoveries.py -q -k prune`

- [ ] **Step 3: Add the three routes**

`GET /admin/discoveries/prune` (paste form), `POST /admin/discoveries/prune`
(plan — writes nothing), `POST /admin/discoveries/prune/apply` (parse, plan,
apply, commit, render the report). All three `Depends(require_admin)`.

Apply must **re-parse from `text`** and rebuild the plan. Do not accept any
lead id, event id, or reason from the form.

Docstrings must state the re-parse rule and why, matching `import_tags_apply`'s.

- [ ] **Step 4: The template**

`admin_prune.html`, extending the same base as `admin_import_tags.html`. Show
the paste box; after planning, three sections — "Will dismiss" (grouped by
reason, with title and date so a human can spot a mistake), "Not in the queue",
"Already dismissed" — and an Apply button that re-submits the same text. Use
existing classes (`banner warn`, `edgecard`, `dim`); invent no new styling, and
if you add CSS it must not use a 6px or 8px border-radius.

Link it from `admin_discoveries.html` near the copy block.

- [ ] **Step 5: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 6: Commit**

```bash
git add src/app/web/routes/discoveries.py src/app/web/templates tests/test_admin_discoveries.py
git commit -m "feat: paste a prune list, see the plan, apply it"
```
