# RoundKind: FCFS and overseas tour package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two missing `RoundKind` values (true first-come-first-served,
and the overseas tour package lottery), fix the mislabeled `GENERAL_SALE`
kind's doc comment and emoji, and replace every ad-hoc round-kind label
derivation in the templates with a single source-of-truth dict.

**Architecture:** A pure enum + dict-table change (`domain/types.py`,
`db/service.py`), a small display-plumbing change (one new Jinja global in
`web/app.py`, 6 template call-site swaps), a one-line emoji-dict update
(`bot/messages.py`), and a keyword-table update in the ramen.events
importer (`domain/ingest.py`). No new DB table, no migration, no new
routes — `RoundKind` stays a pure classification label throughout.

**Tech Stack:** Python enums (StrEnum), Jinja2 templates, pytest.

## Global Constraints

- No `Round`/`RoundOutcome` schema changes — the existing four optional
  timestamps plus `applies_to`/`url`/`notes` already fit both new kinds.
- `GENERAL_SALE`'s stored value (`"general_sale"`) is untouched — no
  migration, no reclassification of existing rows. Only its doc comment,
  emoji, and forward meaning change.
- No behavioral differences by kind anywhere in `sync_rule`,
  `plan_for_rule`, or suppression/auto-arm logic — `RoundKind` remains a
  pure classification label.
- No companion/2-person tracking, no cancellation-policy tracking, no
  minute-level reminder offsets — all explicitly out of scope.
- ruff `line-length = 100` applies to everything under `src/` and
  `tests/` (not `alembic/**`).
- Every logged-in GET render test that already covers an affected
  template must keep passing unchanged (a regression guard — the 7
  existing kinds' rendered label text must not visibly change).

---

### Task 1: New `RoundKind` values + `LABEL_BY_ROUND_KIND`

**Files:**
- Modify: `src/app/domain/types.py` (the `RoundKind` enum)
- Modify: `src/app/db/service.py` (new `LABEL_BY_ROUND_KIND` dict, placed
  right after the existing `LABEL_BY_ANCHOR` dict at line 675-681)
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `RoundKind.FCFS_SALE` (value `"fcfs_sale"`),
  `RoundKind.TOUR_PACKAGE` (value `"tour_package"`) — both later tasks
  import these from `app.domain.types`. `LABEL_BY_ROUND_KIND: dict[RoundKind, str]`
  in `app.db.service` — Task 2 imports and wires this into a Jinja global.

- [ ] **Step 1: Write the failing test**

In `tests/test_service.py`, add (near the other module-level dict/table
tests, or at the end of the file):

```python
def test_label_by_round_kind_covers_every_kind():
    from app.db.service import LABEL_BY_ROUND_KIND

    assert set(LABEL_BY_ROUND_KIND) == set(RoundKind)


def test_label_by_round_kind_exact_text():
    from app.db.service import LABEL_BY_ROUND_KIND

    assert LABEL_BY_ROUND_KIND[RoundKind.LOTTERY_ROUND] == "Lottery round"
    assert LABEL_BY_ROUND_KIND[RoundKind.ELIGIBILITY_ITEM_SALE] == "Eligibility item sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.STREAM_TICKET_SALE] == "Stream ticket sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.GENERAL_SALE] == "General sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.RESULT_ANNOUNCEMENT] == "Result announcement"
    assert LABEL_BY_ROUND_KIND[RoundKind.PAYMENT_DEADLINE] == "Payment deadline"
    assert LABEL_BY_ROUND_KIND[RoundKind.OTHER] == "Other"
    assert LABEL_BY_ROUND_KIND[RoundKind.FCFS_SALE] == "First come, first served"
    assert LABEL_BY_ROUND_KIND[RoundKind.TOUR_PACKAGE] == "Overseas tour package"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k label_by_round_kind -v`
Expected: both FAIL with `ImportError: cannot import name 'LABEL_BY_ROUND_KIND'`
(and `RoundKind.FCFS_SALE`/`RoundKind.TOUR_PACKAGE` don't exist yet either).

- [ ] **Step 3: Add the two new `RoundKind` values and fix the `GENERAL_SALE` comment**

In `src/app/domain/types.py`, find:

```python
    LOTTERY_ROUND = "lottery_round"                  # 先行抽選 round (最速/1次/2次...)
    ELIGIBILITY_ITEM_SALE = "eligibility_item_sale"  # serial-code item on sale (CD/BD)
    STREAM_TICKET_SALE = "stream_ticket_sale"        # 配信チケット, often per concert day
    GENERAL_SALE = "general_sale"                    # 一般発売, first-come-first-served
    RESULT_ANNOUNCEMENT = "result_announcement"      # 当落発表 (usually a single moment)
    PAYMENT_DEADLINE = "payment_deadline"             # 入金期限 after winning
    OTHER = "other"                                  # future franchise inventions
```

Replace with:

```python
    LOTTERY_ROUND = "lottery_round"                  # 先行抽選 round (最速/1次/2次...)
    ELIGIBILITY_ITEM_SALE = "eligibility_item_sale"  # serial-code item on sale (CD/BD)
    STREAM_TICKET_SALE = "stream_ticket_sale"        # 配信チケット, often per concert day
    # 一般発売: a free-to-enter lottery round requiring no serial code --
    # NOT first-come-first-served (see FCFS_SALE for that).
    GENERAL_SALE = "general_sale"
    RESULT_ANNOUNCEMENT = "result_announcement"      # 当落発表 (usually a single moment)
    PAYMENT_DEADLINE = "payment_deadline"             # 入金期限 after winning
    # True first-come-first-served: buy outright the instant it opens, no
    # application/lottery step. Per the guide, always the last round for a
    # concert and not guaranteed to happen (only if lottery rounds leave
    # tickets unsold).
    FCFS_SALE = "fcfs_sale"
    # The overseas tour package ("gaijin pack") lottery track: a hotel +
    # ticket bundle sold via its own lottery, structurally separate from
    # the eplus serial-code system. Not guaranteed to exist per concert.
    TOUR_PACKAGE = "tour_package"
    OTHER = "other"                                  # future franchise inventions
```

(`OTHER` moves to the end so it still reads as the catch-all bucket.)

- [ ] **Step 4: Add `LABEL_BY_ROUND_KIND`**

In `src/app/db/service.py`, find the top-level import line:

```python
from app.domain.types import Anchor, LotteryOutcome, TagKind
```

Replace with:

```python
from app.domain.types import Anchor, LotteryOutcome, RoundKind, TagKind
```

Then find the existing `LABEL_BY_ANCHOR` dict:

```python
LABEL_BY_ANCHOR: dict[Anchor, str] = {
    Anchor.OPENS: "opens",
    Anchor.CLOSES: "closes",
    Anchor.RESULTS: "results announced",
    Anchor.PAYMENT: "payment due",
    Anchor.EVENT_START: "event",
}
```

Add immediately after it:

```python
LABEL_BY_ROUND_KIND: dict[RoundKind, str] = {
    RoundKind.LOTTERY_ROUND: "Lottery round",
    RoundKind.ELIGIBILITY_ITEM_SALE: "Eligibility item sale",
    RoundKind.STREAM_TICKET_SALE: "Stream ticket sale",
    RoundKind.GENERAL_SALE: "General sale",
    RoundKind.RESULT_ANNOUNCEMENT: "Result announcement",
    RoundKind.PAYMENT_DEADLINE: "Payment deadline",
    RoundKind.FCFS_SALE: "First come, first served",
    RoundKind.TOUR_PACKAGE: "Overseas tour package",
    RoundKind.OTHER: "Other",
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k label_by_round_kind -v`
Expected: both PASS.

- [ ] **Step 6: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/domain/types.py src/app/db/service.py tests/test_service.py
git commit -m "Add FCFS_SALE and TOUR_PACKAGE round kinds with a label table"
```

---

### Task 2: Wire `round_kind_label` and swap every template call site

**Files:**
- Modify: `src/app/web/app.py`
- Modify: `src/app/web/templates/concert_edit.html` (2 sites)
- Modify: `src/app/web/templates/concert_new.html` (1 site)
- Modify: `src/app/web/templates/import_preview.html` (2 sites)
- Modify: `src/app/web/templates/_performances.html` (1 site)
- Test: `tests/test_crud.py`, `tests/test_imports.py`

**Interfaces:**
- Consumes: `LABEL_BY_ROUND_KIND` from `app.db.service` (Task 1).
- Produces: a `round_kind_label` Jinja global usable as
  `{{ round_kind_label(k) }}` in any template, mirroring the existing
  `deadline_label` global for `Anchor`.

**Note on scope:** the design spec listed 5 round-kind label sites, but
`concert_new.html:63-65` also has one it missed (the "add round" `<template>`
on the create-event page, same shape as the other 5) — this task covers
all 6 real sites.

- [ ] **Step 1: Write the failing tests**

In `tests/test_crud.py`, add these tests near `test_new_concert_page_is_editor_only`
(around line 630) and `test_edit_page_prefills_every_field` (around line 753):

```python
def test_new_concert_page_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/new")
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text


async def test_edit_page_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C", "event_id": "c"})
    r = client.get("/concerts/c/edit")
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text
```

In `tests/test_imports.py`, add this test after `test_preview_renders_parsed_draft`
(around line 152) — the import-preview page's round-kind `<select>` always
lists all 9 kinds as options regardless of what got parsed from the
fixture, so this doesn't need a fixture that actually contains an FCFS or
tour-package round:

```python
def test_preview_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crud.py -k new_round_kind_labels tests/test_imports.py -k new_round_kind_labels -v`
Expected: all three FAIL — the pages render "Fcfs sale" and "Tour
package" (the auto-derived text), not the assertions' strings.

- [ ] **Step 3: Wire the Jinja global**

In `src/app/web/app.py`, find:

```python
from app.db.service import LABEL_BY_ANCHOR
```

Replace with:

```python
from app.db.service import LABEL_BY_ANCHOR, LABEL_BY_ROUND_KIND
```

Then find:

```python
templates.env.globals["deadline_label"] = lambda anchor: LABEL_BY_ANCHOR[anchor]
```

Add immediately after it:

```python
templates.env.globals["round_kind_label"] = lambda kind: LABEL_BY_ROUND_KIND[kind]
```

- [ ] **Step 4: Swap the 6 template call sites**

In `src/app/web/templates/concert_edit.html`, find (line 68):

```html
        <option value="{{ k.value }}" {% if k == r.kind %}selected{% endif %}>{{ k.value.replace("_", " ") | capitalize }}</option>
```

Replace with:

```html
        <option value="{{ k.value }}" {% if k == r.kind %}selected{% endif %}>{{ round_kind_label(k) }}</option>
```

In the same file, find (line 128, inside `<template id="round-row-template">`):

```html
      <option value="{{ k.value }}">{{ k.value.replace("_", " ") | capitalize }}</option>
```

Replace with:

```html
      <option value="{{ k.value }}">{{ round_kind_label(k) }}</option>
```

In `src/app/web/templates/concert_new.html`, find (line 64, inside
`<template id="round-row-template">`):

```html
      <option value="{{ k.value }}">{{ k.value.replace("_", " ") | capitalize }}</option>
```

Replace with:

```html
      <option value="{{ k.value }}">{{ round_kind_label(k) }}</option>
```

In `src/app/web/templates/import_preview.html`, find (line 37):

```html
        <option value="{{ k.value }}" {% if k == r.kind %}selected{% endif %}>{{ k.value.replace("_", " ") | capitalize }}</option>
```

Replace with:

```html
        <option value="{{ k.value }}" {% if k == r.kind %}selected{% endif %}>{{ round_kind_label(k) }}</option>
```

In the same file, find (line 65, inside `<template id="round-row-template">`):

```html
      <option value="{{ k.value }}">{{ k.value.replace("_", " ") | capitalize }}</option>
```

Replace with:

```html
      <option value="{{ k.value }}">{{ round_kind_label(k) }}</option>
```

In `src/app/web/templates/_performances.html`, find (line 8):

```html
      <em class="kind">{{ r.kind.value.replace("_", " ") }}</em>
```

Replace with:

```html
      <em class="kind">{{ round_kind_label(r.kind) }}</em>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crud.py -k new_round_kind_labels tests/test_imports.py -k new_round_kind_labels -v`
Expected: all three PASS.

- [ ] **Step 6: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/web/app.py src/app/web/templates/concert_edit.html src/app/web/templates/concert_new.html src/app/web/templates/import_preview.html src/app/web/templates/_performances.html tests/test_crud.py tests/test_imports.py
git commit -m "Wire round_kind_label into every round-kind template site"
```

---

### Task 3: `KIND_EMOJI` updates in the DM builder

**Files:**
- Modify: `src/app/bot/messages.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks (`KIND_EMOJI` is keyed by plain
  strings, e.g. `"general_sale"`, already matching `RoundKind`'s stored
  values regardless of when the enum itself changes).
- Produces: nothing later tasks depend on.

**Note:** `KIND_EMOJI` currently has zero test coverage anywhere in the
repo (confirmed via search) — this task adds the first tests for it,
rather than updating pre-existing ones.

- [ ] **Step 1: Write the failing tests**

In `tests/test_messages.py`, add these tests after `test_format_round_reminder`
(around line 44):

```python
def test_general_sale_uses_ticket_emoji_not_running_emoji():
    item = DueReminder(
        queue_id=3,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="General sale",
        round_kind="general_sale",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "🎫" in msg
    assert "🏃" not in msg


def test_fcfs_sale_gets_its_own_emoji():
    item = DueReminder(
        queue_id=4,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.OPENS,
        fire_at_utc=dt(6, 22, 14),
        round_label="FCFS sale",
        round_kind="fcfs_sale",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "🏁" in msg


def test_tour_package_gets_its_own_emoji():
    item = DueReminder(
        queue_id=5,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="Overseas tour package",
        round_kind="tour_package",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "✈️" in msg
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_messages.py -k "ticket_emoji or fcfs_sale_gets or tour_package_gets" -v`
Expected: `test_general_sale_uses_ticket_emoji_not_running_emoji` FAILS on
the `assert "🎫" in msg` line (message currently has 🏃 instead); the
other two FAIL on their emoji assertion (both currently fall back to the
default 🗓️, since `"fcfs_sale"`/`"tour_package"` aren't in `KIND_EMOJI` yet).

- [ ] **Step 3: Update `KIND_EMOJI`**

In `src/app/bot/messages.py`, find:

```python
KIND_EMOJI = {
    "lottery_round": "🎟️",
    "eligibility_item_sale": "💿",
    "stream_ticket_sale": "📺",
    "general_sale": "🏃",
    "result_announcement": "📣",
    "payment_deadline": "💴",
    "other": "📌",
}
```

Replace with:

```python
KIND_EMOJI = {
    "lottery_round": "🎟️",
    "eligibility_item_sale": "💿",
    "stream_ticket_sale": "📺",
    "general_sale": "🎫",
    "result_announcement": "📣",
    "payment_deadline": "💴",
    "fcfs_sale": "🏁",
    "tour_package": "✈️",
    "other": "📌",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_messages.py -v`
Expected: all PASS, including the 3 new tests and every pre-existing test
in the file.

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/bot/messages.py tests/test_messages.py
git commit -m "Fix general_sale's emoji and add FCFS/tour-package emoji"
```

---

### Task 4: ramen.events import heuristics

**Files:**
- Modify: `src/app/domain/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces: nothing later tasks depend on.

**Note on test approach:** every existing test in `test_ingest.py` runs
the full `parse_ramen_event(html, url)` against a saved HTML fixture.
Building new fixture files just to exercise 3 keyword-table entries would
be disproportionate — this task instead imports and calls the private
`_guess_kind(text)` function directly, a plain pure function with no
dependencies, which is a faithful and much lighter-weight test of the
exact thing that changed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ingest.py`, add these tests after `test_parses_lottery_rounds`
(around line 55):

```python
def test_first_come_classifies_as_fcfs_not_general_sale():
    from app.domain.ingest import _guess_kind

    assert _guess_kind("First-come, first-served round") is RoundKind.FCFS_SALE


def test_general_sale_text_still_classifies_as_general_sale():
    from app.domain.ingest import _guess_kind

    assert _guess_kind("General sale") is RoundKind.GENERAL_SALE


def test_tour_package_text_classifies_as_tour_package():
    from app.domain.ingest import _guess_kind

    assert _guess_kind("Overseas Tour Package Lottery") is RoundKind.TOUR_PACKAGE


def test_overseas_text_classifies_as_tour_package():
    from app.domain.ingest import _guess_kind

    assert _guess_kind("Overseas Fan Lottery") is RoundKind.TOUR_PACKAGE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ingest.py -k "fcfs_not_general or tour_package or overseas_text" -v`
Expected: `test_first_come_classifies_as_fcfs_not_general_sale` FAILS
(currently returns `RoundKind.GENERAL_SALE`); the two tour-package tests
FAIL (currently return `RoundKind.OTHER`, no keyword matches).
`test_general_sale_text_still_classifies_as_general_sale` PASSES already
(unaffected by this change — included as a regression guard).

- [ ] **Step 3: Update the keyword table**

In `src/app/domain/ingest.py`, find:

```python
_KIND_KEYWORDS: list[tuple[str, RoundKind]] = [
    ("general sale", RoundKind.GENERAL_SALE),
    ("first-come", RoundKind.GENERAL_SALE),
    ("result", RoundKind.RESULT_ANNOUNCEMENT),
    ("announce", RoundKind.RESULT_ANNOUNCEMENT),
    ("payment", RoundKind.PAYMENT_DEADLINE),
    ("stream", RoundKind.STREAM_TICKET_SALE),
    ("配信", RoundKind.STREAM_TICKET_SALE),
    ("lottery", RoundKind.LOTTERY_ROUND),
    ("抽選", RoundKind.LOTTERY_ROUND),
]
```

Replace with:

```python
_KIND_KEYWORDS: list[tuple[str, RoundKind]] = [
    ("general sale", RoundKind.GENERAL_SALE),
    ("first-come", RoundKind.FCFS_SALE),
    ("tour package", RoundKind.TOUR_PACKAGE),
    ("overseas", RoundKind.TOUR_PACKAGE),
    ("result", RoundKind.RESULT_ANNOUNCEMENT),
    ("announce", RoundKind.RESULT_ANNOUNCEMENT),
    ("payment", RoundKind.PAYMENT_DEADLINE),
    ("stream", RoundKind.STREAM_TICKET_SALE),
    ("配信", RoundKind.STREAM_TICKET_SALE),
    ("lottery", RoundKind.LOTTERY_ROUND),
    ("抽選", RoundKind.LOTTERY_ROUND),
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: all PASS, including every pre-existing test in the file
(`test_parses_lottery_rounds` still resolves `RoundKind.LOTTERY_ROUND`
via the untouched `"lottery"`/`"抽選"` entries).

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/domain/ingest.py tests/test_ingest.py
git commit -m "Fix first-come import heuristic and add tour-package keywords"
```

---

### Task 5: Final step — docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `WISHLIST.md`

- [ ] **Step 1: Update CLAUDE.md**

Run `uv run pytest -q` and read the final passing count from its output
(do not hard-code a guessed number). Update the intro sentence's test
count to that real number, and append to the shipped-features list:
"a corrected first-come-first-served round kind (split out from the
previously-conflated general-sale kind) and a new overseas tour package
round kind, both reflected in round-kind labels/emoji and the
ramen.events import heuristics".

- [ ] **Step 2: Update WISHLIST.md**

This idea wasn't tracked as a `## Proposed` WISHLIST.md entry before
being brainstormed (it went straight from a domain-guide discussion to a
spec), so there's no existing entry to move. Add a new entry directly to
`## Shipped` with today's date, summarizing what shipped: the two new
`RoundKind` values, the `GENERAL_SALE` emoji/comment fix, the
`LABEL_BY_ROUND_KIND` table replacing 6 inline label derivations, and the
`ingest.py` keyword-table fix.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md WISHLIST.md
git commit -m "Update CLAUDE.md and WISHLIST.md for round-kind-fcfs-tour-package"
```
