# Trilingual Concert Pages — Phase 3: Round-Label Phrase Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every trilingual round label you type once becomes a one-click suggestion on every future concert.

**Architecture:** A round label stays free text — real labels do not decompose into a taxonomy (see the spec's Liella! analysis: 0 of 9 decompose, because the missing axis is *channel* and channels are proper nouns). Instead the app remembers triples. Saving a round whose three label fields are all filled upserts a `RoundLabelPhrase` row and bumps its use count. A `<dialog class="picker">` on each editor page lists remembered triples, ranked by use; clicking one fills all three inputs of the round row that opened it. Each row carries a × that forgets the phrase without touching concerts that already used it.

**Tech Stack:** Python 3.14, SQLAlchemy async, Alembic (SQLite batch mode), FastAPI, Jinja2, vanilla JS, pytest / pytest-asyncio.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any commit. The suite is currently fully green. `tests/test_web.py::test_healthz` is a genuine wall-clock flake and may fail either way; anything else failing means something broke.
- **Run the full suite in the FOREGROUND.** A background `pytest` in this repo has silently died mid-run three times, stalling around 93–96% and leaving output that reads like a pass.
- Before trusting any bulk/scripted edit, read `git diff --stat` and sanity-check line counts — a script silently deleted 879 lines of a test file here once.
- Current alembic head is `a589d82c11b4`. The migration in Task 1 uses it as `down_revision`.
- Every `batch_alter_table` on a pre-existing table MUST pass `naming_convention=NAMING_CONVENTION`. **A brand-new table needs no batch mode at all** — `op.create_table` is fine and this migration should use it.
- **Migration tests must monkeypatch `settings.database_url`.** `alembic/env.py` builds its URL from settings and IGNORES `cfg.set_main_option("sqlalchemy.url", ...)`, so a test that only sets the config option runs against the real repo database. Copy the helper in `tests/test_migration_label_variants.py`, which also stamps `alembic_version` and pins to an explicit revision rather than `"head"`.
- Business logic lives in `src/app/db/service.py`; routes are thin shells.
- Sentence case in UI copy. New user-visible strings are `_()`-wrapped and filled BY HAND, non-fuzzy, into BOTH `src/app/translations/ja/LC_MESSAGES/messages.po` and the `zh` one; then delete the regenerable `messages.pot`. **pybabel fuzzy-matches unrelated msgids on this project** — check every fuzzy entry by hand. NEVER edit an existing English msgid; that orphans both translations.
- Never interpolate user-controlled or translated text into an inline `on*` handler — the browser HTML-decodes the attribute before parsing it as JS. Use `data-` attributes read via `dataset`.
- Test fixtures: the suite uses a `db` sessionmaker (`async with db() as session:`); HTTP client fixtures are in `tests/test_crud.py`.

## Out of scope

Phase 4 (required-variant enforcement) and phase 5 (dropping the free-text venue columns). **Franchise-aware suggestion ranking is explicitly deferred** — this phase ranks by use count and recency only. The table's shape leaves room for it later.

---

### Task 1: The `RoundLabelPhrase` table

**Files:**
- Modify: `src/app/db/models.py` (after `RoundQualifier`, around line 400)
- Create: `alembic/versions/<generated>_round_label_phrases.py`
- Test: `tests/test_round_phrases.py` (create), `tests/test_migration_round_phrases.py` (create)

**Interfaces:**
- Produces: `RoundLabelPhrase` with `id`, `label` (`String(200)`), `label_en` (`String(200)`), `label_zh` (`String(200)`), `used_count` (`int`, default 1), `created_at`, `last_used_at`. Tasks 2–5 read and write it.

Follow the house style shown by `LegOptOut` and `ConcertSubscription`: surrogate PK, a **named** `Index(..., unique=True)` in `__table_args__` (never a bare `unique=True` on a column — SQLite batch mode refuses unnamed constraints), and `created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)`.

All three label columns are NOT NULL: a phrase is only recorded when all three are filled, so a partial triple has no meaning here. The unique index spans all three, so two phrases sharing a Japanese label but differing in translation are distinct rows — which is what lets a corrected phrase coexist with the typo until you forget the typo.

- [ ] **Step 1: Write the failing test**

Create `tests/test_round_phrases.py`:

```python
"""The remembered round-label triples behind the phrase picker."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models import RoundLabelPhrase


def test_phrase_columns_and_unique_index():
    cols = RoundLabelPhrase.__table__.columns
    for name in ("label", "label_en", "label_zh"):
        assert name in cols, f"RoundLabelPhrase.{name} missing"
        assert not cols[name].nullable, f"{name} must be NOT NULL"
        assert cols[name].type.length == 200
    assert cols["used_count"].default is not None

    # A named unique index over the whole triple: two phrases sharing a
    # Japanese label but differing in translation are distinct rows, which is
    # what lets a corrected phrase coexist with the typo it replaces.
    idx = {i.name: i for i in RoundLabelPhrase.__table__.indexes}
    assert "uq_round_label_phrase" in idx, f"got {list(idx)}"
    target = idx["uq_round_label_phrase"]
    assert target.unique
    assert [c.name for c in target.columns] == ["label", "label_en", "label_zh"]


async def test_a_phrase_round_trips(db):
    async with db() as session:
        session.add(RoundLabelPhrase(
            label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行",
        ))
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 1
        assert row.created_at.tzinfo is not None, "timestamps are aware UTC"
```

Copy the `db` fixture from `tests/test_editor_legs.py` — it registers the `PRAGMA foreign_keys=ON` connect listener that production registers.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: FAIL — `ImportError: cannot import name 'RoundLabelPhrase'`

- [ ] **Step 3: Add the model**

```python
class RoundLabelPhrase(Base):
    """A trilingual round label the editor has used before, offered as a
    one-click suggestion on later concerts.

    Round labels do not decompose into a taxonomy -- real ones carry a
    CHANNEL (オフィシャル, ファミリーマート, 「Liella! CLUB 2025」) that is a
    proper noun, so no enum can enumerate them. Remembering whole triples
    sidesteps that: the app never parses a label, it just recalls one.

    The unique index spans all three columns, so a corrected phrase and the
    typo it replaces are separate rows -- forgetting the typo is a delete,
    not an edit, and never touches concerts that already used it.
    """

    __tablename__ = "round_label_phrases"
    __table_args__ = (
        Index("uq_round_label_phrase", "label", "label_en", "label_zh", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    label_en: Mapped[str] = mapped_column(String(200))
    label_zh: Mapped[str] = mapped_column(String(200))
    # Ranking inputs. used_count is bumped on every save that reuses the
    # triple; last_used_at breaks ties so a recently-revived phrase outranks
    # an equally-used stale one.
    used_count: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
```

- [ ] **Step 4: Write the migration**

```bash
uv run alembic revision -m "round label phrases"
```

Set `down_revision = 'a589d82c11b4'`. Do NOT use `--autogenerate`. A brand-new table needs no batch mode:

```python
def upgrade() -> None:
    op.create_table(
        'round_label_phrases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('label_en', sa.String(length=200), nullable=False),
        sa.Column('label_zh', sa.String(length=200), nullable=False),
        sa.Column('used_count', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_round_label_phrases')),
    )
    op.create_index(
        'uq_round_label_phrase', 'round_label_phrases',
        ['label', 'label_en', 'label_zh'], unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_round_label_phrase', table_name='round_label_phrases')
    op.drop_table('round_label_phrases')
```

Note `sa.DateTime()`, never `app.db.models.UTCDateTime()`, and no `import app.db.models`.

- [ ] **Step 5: Test the migration**

Create `tests/test_migration_round_phrases.py`, copying the `_run_upgrade` helper from `tests/test_migration_label_variants.py` **including its `settings.database_url` monkeypatch**. Assert the table and the unique index exist after upgrade, that inserting a duplicate triple raises, and that `downgrade` removes both.

- [ ] **Step 6: Apply and verify**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
uv run pytest -q
uv run ruff check .
```

- [ ] **Step 7: Commit**

```bash
git add src/app/db/models.py alembic/versions/ tests/test_round_phrases.py tests/test_migration_round_phrases.py
git commit -m "feat: add the round-label phrase table"
```

---

### Task 2: Service layer — record, list, forget

**Files:**
- Modify: `src/app/db/service.py` (new section `# ── Round-label phrases ───`)
- Test: `tests/test_round_phrases.py`

**Interfaces:**
- Consumes: `RoundLabelPhrase` (Task 1).
- Produces:
  - `async def record_round_label_phrase(session, label, label_en, label_zh) -> None`
  - `async def round_label_phrases(session, limit=50) -> list[RoundLabelPhrase]`
  - `async def forget_round_label_phrase(session, phrase_id) -> bool`

Follow the file's upsert idiom exactly (`set_concert_subscription` is the reference): `select(...).where(...)` → `scalar_one_or_none()` → `if None: session.add(...)` `else:` mutate → `await session.flush()`. This project does not use `INSERT ... ON CONFLICT`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_recording_a_phrase_twice_bumps_its_use_count(db):
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 2, "the second save reuses the row, not a duplicate"


async def test_a_partial_triple_is_not_recorded(db):
    """A phrase is only worth remembering when all three languages are there —
    a half-filled triple would be offered as a suggestion that leaves the
    editor with blanks to fill anyway."""
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "")
        await record_round_label_phrase(session, "", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "  ", "  ", "  ")
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_phrases_rank_by_use_then_recency(db):
    async with db() as session:
        for _ in range(3):
            await record_round_label_phrase(session, "A", "A", "A")
        await record_round_label_phrase(session, "B", "B", "B")
        await record_round_label_phrase(session, "C", "C", "C")
        await session.commit()

    async with db() as session:
        rows = await round_label_phrases(session)
        assert rows[0].label == "A", "most-used first"
        assert [r.label for r in rows[1:]] == ["C", "B"], "then most-recent first"


async def test_forgetting_a_phrase_removes_only_the_suggestion(db):
    async with db() as session:
        await record_round_label_phrase(session, "typo", "Offical", "官方")
        await session.commit()
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert await forget_round_label_phrase(session, row.id) is True
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_forgetting_an_unknown_phrase_is_false_not_an_error(db):
    async with db() as session:
        assert await forget_round_label_phrase(session, 9999) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: FAIL — `ImportError: cannot import name 'record_round_label_phrase'`

- [ ] **Step 3: Implement**

```python
# ── Round-label phrases ──────────────────────────────────────────────────


async def record_round_label_phrase(
    session: AsyncSession, label: str, label_en: str, label_zh: str
) -> None:
    """Remember a trilingual round label so later concerts can reuse it.

    Only a COMPLETE triple is recorded: a suggestion that fills two of three
    boxes still leaves the editor typing, which is the cost this exists to
    remove. Reusing an existing triple bumps its count rather than inserting
    a duplicate -- that count is what ranks the picker.
    """
    label, label_en, label_zh = label.strip(), label_en.strip(), label_zh.strip()
    if not (label and label_en and label_zh):
        return

    existing = (await session.execute(
        select(RoundLabelPhrase).where(
            RoundLabelPhrase.label == label,
            RoundLabelPhrase.label_en == label_en,
            RoundLabelPhrase.label_zh == label_zh,
        )
    )).scalar_one_or_none()
    if existing is None:
        session.add(RoundLabelPhrase(label=label, label_en=label_en, label_zh=label_zh))
    else:
        existing.used_count += 1
        existing.last_used_at = _now()
    await session.flush()


async def round_label_phrases(
    session: AsyncSession, limit: int = 50
) -> list[RoundLabelPhrase]:
    """The picker's list: most-used first, most-recent breaking ties."""
    return list((await session.execute(
        select(RoundLabelPhrase)
        .order_by(RoundLabelPhrase.used_count.desc(), RoundLabelPhrase.last_used_at.desc())
        .limit(limit)
    )).scalars())


async def forget_round_label_phrase(session: AsyncSession, phrase_id: int) -> bool:
    """Stop offering a phrase. Returns False when it was already gone.

    Deliberately does NOT touch rounds that used it -- a phrase is a
    suggestion, not a foreign key, so forgetting a typo leaves the concerts
    that carry it exactly as they are.
    """
    existing = await session.get(RoundLabelPhrase, phrase_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_round_phrases.py
git commit -m "feat: record, rank and forget round-label phrases"
```

---

### Task 3: Record phrases on every save path

**Files:**
- Modify: `src/app/web/routes/concerts.py` (create route ~`:795`, edit route ~`:1334` and `:1340`), `src/app/web/routes/imports.py` (the round loop in the commit route)
- Test: `tests/test_round_phrases.py`

**Interfaces:**
- Consumes: `record_round_label_phrase` (Task 2).

There are THREE save paths. The edit route has TWO branches (update an existing round, insert a new one) and both must record. Grep for `build_round` and `apply_round_fields` to find every call site rather than trusting the line numbers above — they drift.

- [ ] **Step 1: Write the failing test**

```python
async def test_saving_a_concert_records_its_round_phrases(editor_client, db):
    resp = await editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph1",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert (row.label, row.label_en, row.label_zh) == (
            "1次先行抽選", "1st-round lottery", "第一轮先行",
        )


async def test_a_round_saved_in_japanese_only_records_nothing(editor_client, db):
    """The common case today. It must not litter the picker with unusable
    partial suggestions."""
    resp = await editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph2",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": [""], "round_label_zh": [""],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: FAIL — no phrase row is created.

- [ ] **Step 3: Wire the three paths**

In each round loop, immediately after the round is built or updated:

```python
        await record_round_label_phrase(session, label, label_en, label_zh)
```

Import it alongside the other service helpers each route already pulls in.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/app/web/routes/ tests/test_round_phrases.py
git commit -m "feat: remember a round's labels whenever a concert is saved"
```

---

### Task 4: The picker dialog

**Files:**
- Create: `src/app/web/templates/_round_phrase_dialog.html`
- Modify: `concert_new.html`, `concert_edit.html`, `import_preview.html` (opener button per round row, plus the include), `src/app/web/routes/concerts.py` and `imports.py` (add `round_phrases` to each template context)
- Test: `tests/test_round_phrases.py`

**Interfaces:**
- Consumes: `round_label_phrases` (Task 2).

Mirror `_venue_create_dialog.html` closely — it is the established shape: `<dialog class="picker" id="...">`, a `.picker-head` with an `<h3>` and a `×`, a `.picker-body`, delegated `click` listeners on `document`, an `opener` variable remembering which row asked, and a single `close` listener that resets state so ×, backdrop and Esc all behave identically.

Two things specific to this dialog:

**Search.** `filterChips` is a shared helper in `base.html:128-134`, scoped by selector. Reuse it rather than writing a second filter: give the search input `oninput="filterChips(this, '#round-phrase-picker')"` and give each phrase row `data-name="{{ (p.label ~ ' ' ~ p.label_en ~ ' ' ~ p.label_zh) | lower }}"` so typing matches any of the three languages. `data-name` is reserved app-wide for exactly this — do not use it for anything else on these rows.

**Carrying the triple to the client.** The three label values must reach JS without being interpolated into an `on*` handler. Put them in `data-` attributes on the row button and read them via `dataset`. Do NOT name any of them `data-name`; use `data-ja` / `data-en` / `data-zh`.

The dialog goes **outside** the `<form>`, like the venue dialog, and the opener is a chip beside the round-label group in every round row — including each blank-row `<template>`, or the button is missing on JS-added rows.

- [ ] **Step 1: Write the failing test**

```python
async def test_the_phrase_picker_lists_remembered_triples(editor_client, db):
    async with db() as session:
        await record_round_label_phrase(session, "1次先行抽選", "1st-round lottery", "第一轮先行")
        await session.commit()

    r = await editor_client.get("/concerts/new")
    assert r.status_code == 200

    start = r.text.index('id="round-phrase-picker"')
    dialog = r.text[start : r.text.index("</dialog>", start)]

    # All three languages visible before you pick.
    assert "1次先行抽選" in dialog
    assert "1st-round lottery" in dialog
    assert "第一轮先行" in dialog
    # Invariant 7: no inline on* handler may carry user-controlled text.
    assert not re.search(r"\son(?!input)\w+\s*=", dialog), dialog
    # Searchable in any language via the shared filterChips helper.
    assert 'data-name="1次先行抽選 1st-round lottery 第一轮先行"' in dialog


async def test_every_round_row_can_open_the_phrase_picker(editor_client):
    r = await editor_client.get("/concerts/new")
    tpl_start = r.text.index('id="round-row-template"')
    tpl = r.text[tpl_start : r.text.index("</template>", tpl_start)]
    assert "data-open-phrases" in tpl, "a JS-added round row must offer the picker too"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: FAIL — `ValueError: substring not found`

- [ ] **Step 3: Build the dialog**

```jinja
{#- Remembered round labels. Clicking a row fills all three inputs of the
    round row that opened it. Outside the <form> on purpose (mirrors
    _venue_create_dialog.html): nothing in here may ride along on save. -#}
<dialog class="picker" id="round-phrase-picker">
  <div class="picker-head">
    <h3>{{ _("Remembered labels") }}</h3>
    <button type="button" class="x" data-phrase-cancel>×</button>
  </div>
  <div class="picker-body">
    <input type="search" placeholder="{{ _('Search…') }}"
           oninput="filterChips(this, '#round-phrase-picker')">
    <div class="taglist phrase-list">
      {% for p in round_phrases %}
      <div class="phrase-row" data-name="{{ (p.label ~ ' ' ~ p.label_en ~ ' ' ~ p.label_zh) | lower }}">
        <button type="button" class="chip phrase-pick"
                data-ja="{{ p.label }}" data-en="{{ p.label_en }}" data-zh="{{ p.label_zh }}">
          <span class="ph-ja">{{ p.label }}</span>
          <span class="ph-alt dim tiny">{{ p.label_en }} · {{ p.label_zh }}</span>
        </button>
        <button type="button" class="x phrase-forget" data-phrase-id="{{ p.id }}"
                title="{{ _('Forget this label') }}">×</button>
      </div>
      {% endfor %}
      <p class="dim tiny" data-phrase-empty {% if round_phrases %}hidden{% endif %}>
        {{ _("Labels you fill in all three languages are remembered here.") }}
      </p>
    </div>
  </div>
</dialog>
```

Add a `.phrase-row` / `.ph-alt` block to `style.css` only if the existing `.taglist` / `.chip` rules do not lay it out acceptably — check first; the goal is to inherit `.picker` styling, not to add CSS.

The script mirrors the venue dialog's structure: a delegated `click` on `[data-open-phrases]` records `opener = btn.closest(".redit")` and calls `showModal()`; a click on `.phrase-pick` reads `dataset.ja/en/zh`, writes them into that `.redit`'s three inputs by `name`, and closes; backdrop-click closes; one `close` listener clears the search box and re-shows all rows.

- [ ] **Step 4: Add the opener and the include**

Beside the round-label group in every round row (pre-filled AND `<template>`) across all three templates:

```jinja
        <button type="button" class="chip add" data-open-phrases>{{ _("Remembered") }}</button>
```

Include the dialog after `</form>` in each page, next to the existing `_venue_create_dialog.html` include.

- [ ] **Step 5: Feed the context**

Add `"round_phrases": await round_label_phrases(session),` to the template context in `new_concert_form`, the edit form handler, and the import preview handler.

- [ ] **Step 6: Catalogues**

```bash
uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run pybabel update -i messages.pot -d src/app/translations -l ja
uv run pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill every new and fuzzy msgstr by hand in both files, clear the fuzzy flags, then `rm messages.pot`. Check pybabel's fuzzy matches individually — it has mismatched unrelated entries on this project before.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/app/web/ src/app/translations/ tests/
git commit -m "feat: pick a remembered round label from a dialog"
```

---

### Task 5: Forget a phrase

**Files:**
- Modify: `src/app/web/routes/concerts.py` (new endpoint), `_round_phrase_dialog.html` (wire the ×)
- Test: `tests/test_round_phrases.py`

**Interfaces:**
- Consumes: `forget_round_label_phrase` (Task 2).
- Produces: `POST /round-phrases/{phrase_id}/forget` → 204 on success, 404 when unknown.

Editor-gated with `require_editor`. Note the project's auth semantics: being SIGNED OUT is not an error — `require_editor` raises `LoginRequired` and the handler redirects to `/`. Being signed in and NOT an editor IS a 403. A test expecting 403 for an anonymous caller would be wrong.

- [ ] **Step 1: Write the failing tests**

```python
async def test_forgetting_a_phrase_removes_it_from_the_picker(editor_client, db):
    async with db() as session:
        await record_round_label_phrase(session, "typo", "Offical", "官方")
        await session.commit()
        pid = (await session.execute(select(RoundLabelPhrase))).scalar_one().id

    resp = await editor_client.post(f"/round-phrases/{pid}/forget")
    assert resp.status_code == 204

    r = await editor_client.get("/concerts/new")
    assert "Offical" not in r.text

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_forgetting_a_phrase_leaves_the_rounds_that_used_it(editor_client, db):
    """A phrase is a suggestion, not a foreign key."""
    # create a concert whose round carries the triple, then forget the phrase
    # and assert the Round row still has all three labels.


async def test_forgetting_an_unknown_phrase_is_404(editor_client):
    assert (await editor_client.post("/round-phrases/9999/forget")).status_code == 404


async def test_forget_requires_an_editor(client):
    """Signed out is a redirect, not a 403 — see the auth invariant."""
    resp = await client.post("/round-phrases/1/forget")
    assert resp.status_code in (200, 303, 204)
```

Fill in the body of the second test — it is the one that proves the "suggestion, not a foreign key" claim, so it must actually create a round and assert its labels survive.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_phrases.py -q`
Expected: FAIL — 404 (route does not exist)

- [ ] **Step 3: Add the endpoint**

```python
@router.post("/round-phrases/{phrase_id}/forget", status_code=204)
async def forget_phrase(
    phrase_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stop offering a remembered label. Never touches the rounds using it."""
    if not await forget_round_label_phrase(session, phrase_id):
        raise HTTPException(status_code=404, detail="no such phrase")
    return Response(status_code=204)
```

- [ ] **Step 4: Wire the ×**

In the dialog's script, a delegated click on `.phrase-forget` reads `dataset.phraseId`, `fetch`es the endpoint with `{method: "POST"}`, and on success removes that `.phrase-row` from the DOM. On failure, leave the row and surface nothing louder than a disabled state — a failed forget is not worth a modal.

Read the id from `data-phrase-id` via `dataset`, never from an inline handler.

- [ ] **Step 5: Run the full suite and commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/app/web/ tests/
git commit -m "feat: forget a remembered round label"
```

---

## Done when

- `uv run pytest -q` and `uv run ruff check .` are green.
- Saving a concert whose round labels are filled in all three languages remembers the triple; saving one in Japanese only remembers nothing.
- The picker lists remembered triples with all three languages visible, ranked by use, searchable in any of the three.
- Clicking a row fills all three inputs of the round row that opened it.
- The × forgets a phrase without altering any concert that used it.

**Not in this phase:** required-variant enforcement (phase 4), dropping the free-text venue columns (phase 5), franchise-aware suggestion ranking (deferred — the table shape leaves room).
