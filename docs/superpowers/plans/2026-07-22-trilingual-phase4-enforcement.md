# Trilingual Concert Pages — Phase 4: Variant Enforcement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A translatable field is filled in all three languages or none, and untranslated records are visible rather than invisible.

**Architecture:** One pure rule in `domain/`, applied three ways. The browser blocks a bad submit inline so nothing typed is lost. The server enforces the same rule as a 422 backstop, so the rule holds for anything that bypasses the form. Existing records are never blocked — the edit page and the Tags page *show* what is missing, computed from current database state, so the backlog is visible and clearable without ever standing between an editor and a save.

**Tech Stack:** Python 3.14, SQLAlchemy async, FastAPI, Jinja2, vanilla JS, pytest / pytest-asyncio.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any commit. `tests/test_web.py::test_healthz` is a genuine wall-clock flake (passes in isolation); anything else failing means something broke.
- **Run the full suite in the FOREGROUND.** A background `pytest` in this repo has silently died or stalled mid-run five times, leaving output that reads like a pass.
- Before trusting any bulk/scripted edit, read `git diff --stat` and sanity-check line counts.
- `src/app/domain/` is pure: NO I/O, no discord/fastapi/sqlalchemy imports.
- Business logic lives in `src/app/db/service.py`; routes are thin shells.
- Sentence case in UI copy. New user-visible strings are `_()`-wrapped and filled BY HAND, non-fuzzy, into BOTH `src/app/translations/ja/LC_MESSAGES/messages.po` and the `zh` one; then delete `messages.pot`. **pybabel fuzzy-matches unrelated msgids here** — it has proposed "delete this performance" for "Forget this label" and "member" for "Remembered". Check every fuzzy entry by hand. NEVER edit an existing English msgid.
- **Language names are never translated** — `English` and `中文` render in their own script in every locale.
- Never interpolate user-controlled or translated text into an inline `on*` handler. Use `data-` attributes read via `dataset`. `data-name` is reserved app-wide for the shared `filterChips` selector.
- Test fixtures: a `db` sessionmaker (`async with db() as session:`) and a SYNC `TestClient` for web routes.
- No migration in this phase — every variant column is already nullable and stays that way. **Do not add a NOT NULL constraint**; existing rows have blanks by design and the rule is enforced at the boundary, not the schema.

## The rule

Given a field's three values, stripped:

| Case | Verdict |
|---|---|
| All three filled | OK |
| All three blank | OK — the field is simply unused |
| Some filled, some blank | **Rejected** — name the blank ones |
| All three blank, but the field is *mandatory* | **Rejected** — name all three |

Mandatory fields: `Concert.title`, `Tag.name`. Everything else (`Concert.notes`, `Tag.city`, `ConcertDay.label`, `Round.label`) is all-or-nothing only.

`Concert.venue`/`venue_en`/`venue_zh` are **out of scope** — the editor no longer types them and phase 5 drops the columns.

## Out of scope

Phase 5 (dropping the free-text venue columns). Franchise-aware phrase ranking. Do not add a server-side re-render-the-form-with-errors mechanism — the browser-side block plus the 422 backstop is the agreed shape, and building form-state restoration for a dozen parallel arrays is its own project.

---

### Task 1: Guard the phrase upsert (carried forward from phase 3)

`record_round_label_phrase` does check-then-insert inside the concert save's transaction with no guard. Two editors saving the same never-before-seen triple in one flush window would hit the unique index, and the exception would roll back the editor's whole concert save. The collateral is the editor's real work, for the sake of a convenience feature.

**Files:**
- Modify: `src/app/db/service.py` (`record_round_label_phrase`, ~line 3528)
- Test: `tests/test_round_phrases.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_a_racing_duplicate_insert_bumps_instead_of_exploding(db, monkeypatch):
    """Simulate the race: the pre-check finds nothing, but the row exists by
    the time we flush. The editor's save must survive."""
    async with db() as session:
        session.add(RoundLabelPhrase(label="A", label_en="A", label_zh="A"))
        await session.commit()

    async with db() as session:
        # Force the check-then-insert path even though the row now exists,
        # which is exactly what a concurrent writer produces.
        real_execute = session.execute

        async def blind_first_lookup(stmt, *a, **kw):
            result = await real_execute(stmt, *a, **kw)
            if getattr(blind_first_lookup, "used", False):
                return result
            blind_first_lookup.used = True

            class _Blind:
                def scalar_one_or_none(self):
                    return None
            return _Blind()

        monkeypatch.setattr(session, "execute", blind_first_lookup)
        await record_round_label_phrase(session, "A", "A", "A")
        await session.commit()

    async with db() as session:
        rows = (await session.execute(select(RoundLabelPhrase))).scalars().all()
        assert len(rows) == 1, "no duplicate row"
        assert rows[0].used_count == 2, "the loser of the race still counts the use"
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/test_round_phrases.py -k racing -q`
Expected: FAIL with an `IntegrityError` (UNIQUE constraint) propagating out.

- [ ] **Step 3: Guard the insert**

Wrap the insert in a savepoint and fall back to re-select-and-bump. That is the semantically correct answer anyway: losing the race means *someone else already remembered this*.

```python
    if existing is None:
        # A concurrent save may have inserted this exact triple between our
        # lookup and this flush. Remembering a label is a convenience -- it
        # must never cost the editor the concert save it rode in on -- so the
        # insert gets its own savepoint and a lost race degrades to a bump.
        try:
            async with session.begin_nested():
                session.add(RoundLabelPhrase(label=label, label_en=label_en, label_zh=label_zh))
        except IntegrityError:
            existing = (await session.execute(
                select(RoundLabelPhrase).where(
                    RoundLabelPhrase.label == label,
                    RoundLabelPhrase.label_en == label_en,
                    RoundLabelPhrase.label_zh == label_zh,
                )
            )).scalar_one_or_none()

    if existing is not None:
        existing.used_count += 1
        existing.last_used_at = _now()
    await session.flush()
```

Import `IntegrityError` from `sqlalchemy.exc`. Note this is the first `try/except IntegrityError` in `src/app/` — every other site pre-checks instead. Add a comment saying why this one differs: the pre-check is still there, the catch exists only for the race, and the cost of an unguarded loss is someone else's unrelated work.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_round_phrases.py -q && uv run pytest -q && uv run ruff check .
git add src/app/db/service.py tests/test_round_phrases.py
git commit -m "fix: a raced phrase insert must not cost the editor their save"
```

---

### Task 2: The rule, as a pure function

**Files:**
- Create: `src/app/domain/translations.py`
- Test: `tests/test_domain_translations.py` (create)

**Interfaces:**
- Produces: `def missing_variants(base: str, en: str, zh: str, *, mandatory: bool = False) -> tuple[str, ...]` returning which of `("ja", "en", "zh")` are blank and must not be, or `()` when the field is fine. Tasks 3–5 all consume it.

`src/app/domain/` is pure — no I/O, no framework imports. This is the single definition of the rule; nothing else may re-implement it.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.domain.translations import missing_variants


@pytest.mark.parametrize("base,en,zh,expected", [
    ("あ", "a", "a", ()),                 # complete
    ("", "", "", ()),                     # unused entirely
    ("  ", "\t", "", ()),                 # whitespace counts as blank
    ("あ", "", "", ("en", "zh")),          # started, so finish it
    ("", "a", "", ("ja", "zh")),
    ("あ", "a", "", ("zh",)),
    ("", "", "a", ("ja", "en")),
])
def test_optional_field_is_all_or_nothing(base, en, zh, expected):
    assert missing_variants(base, en, zh) == expected


@pytest.mark.parametrize("base,en,zh,expected", [
    ("あ", "a", "a", ()),
    ("", "", "", ("ja", "en", "zh")),     # mandatory: blank is not an option
    ("あ", "", "", ("en", "zh")),
])
def test_mandatory_field_must_be_complete(base, en, zh, expected):
    assert missing_variants(base, en, zh, mandatory=True) == expected


def test_order_is_stable_so_messages_read_the_same_every_time():
    assert missing_variants("", "", "", mandatory=True) == ("ja", "en", "zh")
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/test_domain_translations.py -q`
Expected: FAIL — `ModuleNotFoundError: app.domain.translations`

- [ ] **Step 3: Implement**

```python
"""The all-or-nothing rule for locale variants.

A translatable field is filled in every language or none of them. A field
half-translated is worse than one left alone: `loc_field` falls back to the
original when a variant is empty, so a partial fill renders as a silent
mix of languages that nobody notices is wrong.

Pure by design (domain/): the browser, the route and the templates all
apply the SAME rule, and this is the only place it is written down.
"""

_SLOTS = ("ja", "en", "zh")


def missing_variants(
    base: str, en: str, zh: str, *, mandatory: bool = False
) -> tuple[str, ...]:
    """Which of ja/en/zh are blank but must not be. Empty tuple means fine.

    `base` IS the Japanese value -- there is no `_ja` column; the original
    column is the Japanese side (see i18n.loc_field).

    A non-mandatory field may be left blank in all three; a mandatory one
    (a concert title, a tag name) may not, because the record cannot be
    rendered without it.
    """
    values = dict(zip(_SLOTS, (base.strip(), en.strip(), zh.strip()), strict=True))
    if not mandatory and not any(values.values()):
        return ()
    return tuple(slot for slot in _SLOTS if not values[slot])
```

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_domain_translations.py -q && uv run ruff check .
git add src/app/domain/translations.py tests/test_domain_translations.py
git commit -m "feat: the all-or-nothing rule for locale variants"
```

---

### Task 3: The server-side backstop

**Files:**
- Create: `src/app/web/forms.py` addition — a boundary wrapper turning a gap into a 422 (this module already exists and holds `form_url`, the same shape of thing)
- Modify: `src/app/web/routes/concerts.py` (create route), `src/app/web/routes/tags.py` (`create_tag`), `src/app/web/routes/imports.py` (commit route)
- Test: `tests/test_variant_enforcement.py` (create)

**Interfaces:**
- Consumes: `missing_variants` (Task 2).
- Produces: `def require_variants(field_label: str, base: str, en: str, zh: str, *, mandatory: bool = False) -> None` raising `HTTPException(422)` naming the field and the missing slots.

`web/forms.py` is the established home for HTTP-boundary wrappers around domain validators — `form_url` wraps `domain.urls.clean_url` exactly this way. Follow it.

The 422 detail must name the FIELD and WHICH languages are missing, because a JS-disabled editor sees only that string. `"round label needs 中文"` is actionable; `"validation error"` is not.

- [ ] **Step 1: Write the failing tests**

```python
def test_creating_a_concert_with_a_half_translated_title_is_422(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={**_minimal_concert(),
                                       "title": "ラブライブ", "title_en": "Love Live",
                                       "title_zh": ""})
    assert r.status_code == 422
    assert "中文" in r.json()["detail"] or "zh" in r.json()["detail"]


def test_creating_a_concert_with_no_title_translations_is_422(client):
    """title is mandatory: blank in all three is not the escape hatch it is
    for an optional field."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={**_minimal_concert(),
                                       "title": "ラブライブ", "title_en": "", "title_zh": ""})
    assert r.status_code == 422


def test_creating_a_concert_with_an_untouched_optional_field_is_fine(client):
    """notes left blank in all three is not an error."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={**_minimal_concert(full_title=True),
                                       "notes": "", "notes_en": "", "notes_zh": ""})
    assert r.status_code in (200, 303)


def test_creating_a_tag_requires_all_three_names(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/tags", data={"name": "蓮ノ空", "name_en": "Hasunosora",
                                   "name_zh": "", "kind": "group"})
    assert r.status_code == 422


def test_a_half_translated_round_label_is_422(client):
    ...  # same shape, via the round arrays

def test_a_half_translated_leg_label_is_422(client):
    ...  # same shape, via the day arrays
```

Write `_minimal_concert()` as a helper returning the smallest valid payload (the suite has several such payloads already — copy the shape from `tests/test_round_phrases.py`).

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_variant_enforcement.py -q`
Expected: FAIL — the posts currently succeed.

- [ ] **Step 3: Add the boundary wrapper**

```python
_SLOT_LABEL = {"ja": "日本語", "en": "English", "zh": "中文"}


def require_variants(
    field_label: str, base: str, en: str, zh: str, *, mandatory: bool = False
) -> None:
    """422 unless a field is filled in all three languages or none.

    The detail names the field and the missing languages, because a caller
    without JS sees nothing but this string -- the browser-side check that
    normally catches this paints an inline error instead and never submits.
    """
    gaps = missing_variants(base, en, zh, mandatory=mandatory)
    if not gaps:
        return
    raise HTTPException(
        status_code=422,
        detail=f"{field_label} needs {', '.join(_SLOT_LABEL[g] for g in gaps)}",
    )
```

The language names are deliberately NOT `_()`-wrapped — language names never translate in this project.

- [ ] **Step 4: Call it at each create boundary**

Concert create: title (mandatory), notes, and per-row leg and round labels inside their loops. Tag create: name (mandatory), and city for a VENUE tag. Import commit: the same round/leg labels as concert create.

**Do NOT add it to the EDIT routes.** Editing a legacy record must never be blocked — Task 5 surfaces the gap instead. This is the whole reason the phase ships without a backfill.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/app/web/ tests/test_variant_enforcement.py
git commit -m "feat: reject a half-translated field at the create boundary"
```

---

### Task 4: Block it in the browser

**Files:**
- Modify: `concert_new.html`, `concert_edit.html`, `import_preview.html`, `tags.html` (the create dialog), plus a small shared script
- Test: `tests/test_variant_enforcement.py`

**Interfaces:**
- Consumes: nothing server-side — this re-implements the rule in JS deliberately, because the point is to never submit.

The rule is small enough to duplicate, but the duplication must be *marked*: put a comment in both `domain/translations.py` and the JS pointing at each other, so a future change to one is obviously a change to both.

On submit, for each group of three variant inputs: if some are filled and some blank (or, for a mandatory field, any blank), prevent the submit, reveal an inline message beside the offending group, and focus the first blank input. Clear the message when the field becomes valid.

The message text rides in a `data-` attribute — never interpolated into an inline `on*` handler.

- [ ] **Step 1: Write the failing test**

```python
def test_the_editor_ships_the_client_side_variant_check(client):
    login_as(client, EDITOR_ID, "reiji")
    for url in ("/concerts/new", "/tags"):
        body = client.get(url).text
        assert "data-variant-group" in body, url
        assert "submit" in body  # the guard is wired to the form's submit
```

Plus a markup test that each variant trio is wrapped in a group the script can find.

- [ ] **Step 2: Run and see it fail**

- [ ] **Step 3: Implement**

Group each trio with a marker attribute the script can enumerate (`data-variant-group`, with `data-variant-mandatory` on title/name). Reuse the existing `.vfld` wrappers from phase 2 rather than restructuring markup.

- [ ] **Step 4: Verify in a real browser**

Serve the app locally and confirm: a half-filled trio blocks submit and focuses the blank box; completing it clears the message; an all-blank optional trio submits fine. **Measure, do not reason** — this project's rule after two wrong CSS fixes shipped from reasoning alone. Report what you observed.

- [ ] **Step 5: Catalogues, verify, commit**

---

### Task 5: Show what's missing, never block the edit

**Files:**
- Modify: `src/app/db/service.py` (a helper computing gaps for a concert / a tag), `concert_edit.html`, `tags.html`
- Test: `tests/test_variant_enforcement.py`

**Interfaces:**
- Consumes: `missing_variants` (Task 2).

Computed from current database state and rendered on the page — there is no flash mechanism in this codebase, and this is better than one: the gap is visible while you are looking at the record, not after you save.

- [ ] **Step 1: Write the failing test**

```python
def test_the_edit_page_names_what_is_missing(client, db):
    """A legacy record must still save -- the page tells you what is missing
    rather than standing in the way."""
    # seed a concert with title but no title_zh
    r = client.get("/concerts/legacy/edit")
    assert "中文" in r.text
    # and it still saves untouched
    assert client.post("/concerts/legacy/edit", data=...).status_code in (200, 303)
```

- [ ] **Step 2-4:** implement, verify, commit.

---

### Task 6: The untranslated backlog is visible

**Files:**
- Modify: `src/app/db/service.py` (`tag_directory_context`'s summary), `tags.html` (the `.tags-head` note), `tags.html`'s create dialog (add `name_en`/`name_zh`)
- Test: `tests/test_variant_enforcement.py`, `tests/test_tags.py`

Two things, both on the Tags page:

1. **The create dialog omits `name_en`/`name_zh`** even though `create_tag` already accepts them — so today a new tag can only be translated by creating it, then reopening it in the edit dialog. With Task 3's backstop that becomes an outright 422 wall. Add the two inputs, matching the edit dialog's `Name · English` / `Name · 中文` labels.
2. **A count of still-untranslated tags** in the existing `.tags-head` note, which already aggregates counts from `tag_directory_context`'s `summary`. Link it to a filtered view if that is cheap; a bare count is acceptable.

- [ ] Steps: failing test, implement, catalogues, verify, commit.

---

## Done when

- `uv run pytest -q` and `uv run ruff check .` are green.
- A half-translated field cannot be submitted from the editor, and 422s if posted directly.
- A field left blank in all three languages is still fine, except concert title and tag name.
- Editing a legacy record is never blocked; the page names what is missing.
- The Tags page shows how many tags are still untranslated, and a new tag can be created trilingual in one step.
- A raced phrase insert can no longer cost an editor their concert save.

**Not in this phase:** dropping the free-text venue columns (phase 5), franchise-aware ranking, any server-side form-state restoration.
