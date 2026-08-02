# Triage phase 2: many drafts, one paste — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste one file holding many concert drafts, then work through them one
reviewed preview at a time across as many sittings as it takes — instead of
copy-pasting fifty to a hundred YAML blocks by hand.

**Architecture:** A multi-document splitter in `domain/yaml_import.py` built on
`yaml.safe_load_all`, leaving `parse_draft` untouched; a `PendingDraft` table
holding each document verbatim; and routes that reuse the EXISTING preview and
the EXISTING `import_commit` rather than growing a second write path.

**Tech Stack:** PyYAML (`safe_load_all` only), SQLAlchemy 2.0 async, Alembic
(SQLite batch mode), FastAPI, Jinja2.

**Spec:** `docs/superpowers/specs/2026-08-02-triage-leads-design.md`, Phase 2.

## Global Constraints

- **`import_commit` stays the ONLY write path into `concerts`.** Nothing here
  creates a concert by another route, and every concert still passes through one
  human-reviewed preview. This feature removes the copy-paste, never the review.
- **`yaml.safe_load_all` only** — never `load_all`, never `full_load_all`. This
  is pasted text from outside the app.
- `domain/` is pure: no I/O, no sqlalchemy/fastapi/discord imports.
- Editor-only (`require_editor`), matching the rest of `routes/imports.py` — NOT
  admin-only. Signed-in-but-unauthorized is 403.
- These pages are part of the translated app, unlike the admin surfaces: wrap
  user-facing strings in `_()` and update BOTH `src/app/translations/{ja,zh}/
  LC_MESSAGES/messages.po`. `tests/test_i18n_catalogues.py` fails on any
  untranslated or fuzzy msgid. Keep every edited msgid byte-identical.
- Migration: additive only, ASCII-only, no `import app.db.models`, use
  `sa.DateTime()` not `app.db.models.UTCDateTime()`. Chains off the current head
  — run `uv run --isolated alembic heads` to find it.
- Invariant 1: the DB stores aware UTC only; `UTCDateTime` rejects naive
  datetimes.
- `POST` handlers redirect **303, never 307**.
- CSS: 3px radius (999px chips); never 6px or 8px.
- `uv run --isolated pytest -q` must pass, `uv run --isolated ruff check .` clean.
  Always `--isolated` — an external process holds a `.venv` lock.

---

### Task 1: Split a multi-document paste

**Files:**
- Modify: `src/app/domain/yaml_import.py`
- Test: `tests/test_yaml_import.py`

**Interfaces:**
- Consumes: the existing `parse_draft(text) -> ParsedConcert` (from
  `domain/draft.py`) and `DraftError`, both unchanged.
- Produces: `split_documents(text: str) -> list[str]` and
  `parse_drafts(text: str) -> DraftBatch`. `DraftBatch` is a frozen dataclass
  with `drafts: tuple[ParsedDraft, ...]` and `errors: tuple[str, ...]`;
  `ParsedDraft` carries `text: str` (the single document, verbatim) and
  `parsed: ParsedConcert`. Tasks 2 and 3 consume both.

**The property that carries this task:** one malformed document must not lose
the other forty-nine. At fifty concerts a single typo cannot cost the batch.
This is the draft vocabulary's existing warnings-over-failures rule, applied to
a new axis.

**Why `text` is kept per draft:** the `PendingDraft` row stores the document
verbatim so the preview re-parses it later, exactly as if it had been pasted
alone. Storing the parsed object instead would freeze today's parser against
tomorrow's.

- [ ] **Step 1: Write the failing tests**

```python
def test_three_documents_parse_into_three_drafts():
    batch = parse_drafts(ONE + "\n---\n" + TWO + "\n---\n" + THREE)
    assert len(batch.drafts) == 3
    assert batch.errors == ()


def test_one_bad_document_does_not_lose_the_others():
    """The whole point at fifty concerts: a typo in draft 2 must not cost
    drafts 1 and 3."""
    batch = parse_drafts(ONE + "\n---\n" + "title: [unclosed\n" + "\n---\n" + THREE)
    assert len(batch.drafts) == 2
    assert len(batch.errors) == 1
    assert "2" in batch.errors[0], "the error must say WHICH document failed"


def test_a_single_document_still_works():
    """A file with no --- separator is a batch of one, so one paste box can
    serve both cases and nobody has to know which they have."""
    assert len(parse_drafts(ONE).drafts) == 1


def test_each_draft_keeps_its_own_text_verbatim():
    """The row stores the document, not the parse, so a later preview re-parses
    it exactly as if it had been pasted alone."""
    batch = parse_drafts(ONE + "\n---\n" + TWO)
    assert batch.drafts[0].text.strip().startswith(ONE.strip()[:20])
    assert "---" not in batch.drafts[0].text


def test_empty_documents_are_skipped_not_errors():
    """Trailing separators and blank stanzas are formatting, not mistakes --
    `a\n---\n` is one draft, and a stray `---` at the end must not report a
    phantom failure."""
    batch = parse_drafts(ONE + "\n---\n\n---\n" + TWO)
    assert len(batch.drafts) == 2
    assert batch.errors == ()


def test_a_wholly_empty_paste_is_an_error_not_an_empty_batch():
    for text in ("", "   \n", "---\n---\n"):
        batch = parse_drafts(text)
        assert batch.drafts == ()
        assert batch.errors, "an empty paste must say so, not report success"


def test_safe_load_all_only():
    """A YAML tag that would construct a Python object must not."""
    batch = parse_drafts("!!python/object/apply:os.system ['echo hi']\n")
    assert batch.drafts == ()
    assert batch.errors
```

Define `ONE`, `TWO`, `THREE` as minimal valid drafts — reuse the shape from the
existing tests in this file, or from
`.claude/skills/add-concert/references/example-draft.yaml`.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest tests/test_yaml_import.py -q -k "documents or drafts or batch"`
Expected: FAIL — `parse_drafts` does not exist.

- [ ] **Step 3: Implement**

`split_documents` splits on YAML document boundaries. Do NOT hand-roll a
`text.split("---")` — a `---` inside a quoted string or a block scalar would cut
a draft in half. Use `yaml.safe_load_all` to find document boundaries, or
`yaml.compose_all`, and reconstruct each document's source text; if that proves
awkward, splitting on a line that is exactly `---` (after strip) is acceptable
BUT must be documented as a known limitation with the block-scalar case named.

`parse_drafts` calls `parse_draft` per document, collecting successes into
`drafts` and `DraftError` messages into `errors` prefixed with the 1-based
document number.

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_yaml_import.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domain/yaml_import.py tests/test_yaml_import.py
git commit -m "feat: split and parse a multi-document draft paste"
```

---

### Task 2: The `PendingDraft` table

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<rev>_pending_drafts.py`
- Modify: `src/app/db/service.py`
- Test: `tests/test_pending_drafts.py`

**Interfaces:**
- Consumes: `DraftBatch` from Task 1.
- Produces: the `PendingDraft` model, and in `service.py`:
  `create_pending_drafts(session, batch, created_by) -> list[PendingDraft]`,
  `pending_drafts(session, user_id) -> list[PendingDraft]`,
  `mark_pending_committed(session, pending_id, concert_id, now) -> bool`,
  `discard_pending_draft(session, pending_id, now) -> bool`.

**The model**, in `db/models.py`. Its docstring must say WHY this table exists
in an app that otherwise avoids step state: it is a **work batch**, not flow
state — fifty to a hundred concerts each needing a human to read a preview, which
is not one sitting, and a hidden form field would lose the batch to a closed tab.

```python
class PendingDraft(Base):
    __tablename__ = "pending_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The single YAML document, VERBATIM. Not the parse: a row outlives a
    # deploy, and storing the parsed shape would freeze today's parser
    # against tomorrow's.
    draft_text: Mapped[str] = mapped_column(Text)
    # Parsed out at paste time so the list renders without re-parsing every
    # row on every page load.
    title: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.discord_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    # A row is DONE when either of these is set. Nothing cleans up: a
    # committed row is the only trace linking a draft to the concert it
    # produced.
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    concert_id: Mapped[int | None] = mapped_column(
        ForeignKey("concerts.id", ondelete="SET NULL")
    )
    discarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

`ON DELETE SET NULL` on `concert_id` for the reason `ConcertDay.venue_tag_id`
has it: deleting a concert must not take the record of where it came from with
it.

- [ ] **Step 1: Write the failing tests**

Cover: a batch of three creates three rows carrying their own text; `pending_drafts`
returns only rows with both `committed_at` and `discarded_at` NULL, **scoped to
the pasting user**; committing stamps both `committed_at` and `concert_id`;
discarding stamps `discarded_at` and leaves `concert_id` NULL; committing an
already-committed row returns False without re-stamping (the same double-submit
rule `dismiss_lead` follows); and — with `PRAGMA foreign_keys=ON` registered on
the fixture, which production has and cascades silently need — deleting the
concert leaves the row with `concert_id` NULL rather than deleting it.

- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Add the model, then generate and hand-edit the migration**

Run: `uv run --isolated alembic revision --autogenerate -m "pending drafts"`
Then edit: replace `app.db.models.UTCDateTime()` with `sa.DateTime()`, delete
the `import app.db.models` line, confirm ASCII-only, confirm it is a plain
`create_table` with no `drop_`.

Apply: `uv run --isolated alembic upgrade head`

- [ ] **Step 4: Write the service functions, run the tests**
- [ ] **Step 5: Commit**

```bash
git add src/app/db/models.py src/app/db/service.py alembic/versions tests/test_pending_drafts.py
git commit -m "feat: a pending_drafts work batch that survives a closed tab"
```

---

### Task 3: The paste box, the list, and the commit hook

**Files:**
- Modify: `src/app/web/routes/imports.py`
- Create: `src/app/web/templates/import_pending.html`
- Modify: `src/app/web/templates/import_form.html` (a second paste box)
- Modify: `src/app/web/templates/import_preview.html` (carry `pending_id`)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_pending_drafts.py`

**Interfaces:** consumes everything from Tasks 1 and 2.

Four routes plus one change to an existing one:

- `POST /concerts/import/batch` — paste, `parse_drafts`, create rows, **303** to
  the pending list. Errors render on the form with the failing document numbers
  named, and any drafts that DID parse are still created — the batch is not
  all-or-nothing, because that would reintroduce exactly the "one typo costs
  fifty" problem Task 1 exists to solve. Say so in the docstring.
- `GET /concerts/import/pending` — the list: title, leg count, round count, and
  how many tags did not match, per row.
- `GET /concerts/import/pending/{id}` — re-parse `draft_text` and render the
  EXISTING `import_preview.html` through the EXISTING path, with `pending_id` in
  the context.
- `POST /concerts/import/pending/{id}/discard` — stamp `discarded_at`, 303 back.
- `import_commit` gains `pending_id: int | None = Form(None)`. When set and the
  commit succeeds, stamp the row and **303 to `/concerts/import/pending`**
  instead of the concert, so the next one is one click away. When unset, behave
  exactly as today.

- [ ] **Step 1: Write the failing tests**

The properties that matter:

```python
async def test_a_batch_of_three_becomes_three_pending_rows(...)
async def test_a_bad_document_does_not_stop_the_good_ones(...)
    """Two rows created, one error named. All-or-nothing here would undo Task 1."""
async def test_committing_a_pending_draft_returns_to_the_list_not_the_concert(...)
async def test_committing_stamps_the_row_with_its_concert(...)
async def test_a_committed_row_leaves_the_list(...)
async def test_the_list_is_scoped_to_its_owner(...)
    """Another editor's batch must not appear -- two editors triaging at once is
    the expected case, not an exotic one."""
async def test_a_non_editor_cannot_reach_any_of_it(...)
    """All five routes, GET and POST. A page that hides a form is not access
    control."""
async def test_the_same_draft_committed_twice_does_not_make_two_concerts(...)
    """event_id is unique and already answers 409; pin that it still fires
    through the pending path rather than being bypassed by it."""
```

- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Implement the routes and templates**

Reuse `import_preview.html` unchanged apart from carrying `pending_id` as a
hidden input. Do NOT fork the preview.

- [ ] **Step 4: Update both `.po` catalogues by hand**

Run `uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .`,
then `pybabel update -i messages.pot -d src/app/translations -l ja` and again
`-l zh`, fill in every new/fuzzy msgstr by hand in both files, then delete
`messages.pot` (gitignored).

- [ ] **Step 5: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
`tests/test_i18n_catalogues.py` must pass — it fails on any untranslated or
fuzzy msgid.

- [ ] **Step 6: Commit**

```bash
git add src/app/web src/app/translations tests/test_pending_drafts.py
git commit -m "feat: paste many drafts, work through them one preview at a time"
```
