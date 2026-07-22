# Trilingual Concert Pages — Phase 2: Leg and Round Labels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A leg label ("2日目 夜公演") and a round label ("1次先行抽選") render in the viewer's language, everywhere they appear.

**Architecture:** `ConcertDay.label` gains `label_en`/`label_zh`; `Round` gains `label_zh` to pair with its existing `label_en`. `Round.label_en` changes meaning — today it is a pre-i18n English gloss shown to *every* viewer at once (`_round_rows.html:52`), and it becomes a true locale variant selected by `loc_field`. Existing data survives unchanged because the values are already English. Labels reach the screen through eleven sites; five are templates holding the ORM object (fixed with `loc()`), and six are service-layer dataclasses that copy the label string (fixed where the dataclass is built).

**Tech Stack:** Python 3.14, SQLAlchemy async, Alembic (SQLite batch mode), FastAPI, Jinja2, htmx, pytest / pytest-asyncio.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any commit.
- `tests/test_web.py::test_healthz` is a genuine wall-clock flake (3 missed scheduler ticks under a slow suite). Deselect or ignore it; do not fix it.
- Branch is `i18n-phase2-labels`, based on `i18n-trilingual-pages` (phase 1). Current alembic head is `789bbcc95bc3` — the migration in Task 2 uses it as `down_revision`.
- `loc_field(obj, field, locale)` resolves en → `{field}_en`, zh → `{field}_zh`, ja → the original column. Empty string counts as unfilled. **No cross-locale chaining** — zh must never fall back through en.
- `loc(obj, field)` is the Jinja global wrapping it. Display ONLY — form values, `data-*` filter keys and URLs keep the original field.
- Every `batch_alter_table` on `concerts`/`tags`/`concert_days`/`rounds` MUST pass `naming_convention=NAMING_CONVENTION` — these tables predate the convention and carry anonymous constraints on the live server that no metadata-built test DB reproduces.
- After autogenerate, replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and drop `import app.db.models`.
- Business logic lives in `src/app/db/service.py`; routes and bot are thin shells.
- New user-visible strings are `_()`-wrapped and filled BY HAND, non-fuzzy, into BOTH `src/app/translations/ja/LC_MESSAGES/messages.po` and the `zh` one; then delete the regenerable `messages.pot`. `tests/test_i18n_catalogues.py` must pass.
- **Editing an existing English msgid orphans both translations.** Do not touch existing copy.
- Sentence case in UI copy.
- Test fixtures: the suite uses a `db` sessionmaker (`async with db() as session:`); HTTP client fixtures are in `tests/test_crud.py`.
- **`round_label_en` is NOT padded** in `concerts.py`'s create/edit zips (it is assumed 1:1 with `round_label`, since every round row template emits both). `imports.py:337` DOES pad it. Any new parallel array must follow whichever rule its route already uses — a mismatch either raises on a strict zip or silently slides rows.

## Out of scope

Phase 3 (the round-label phrase library), phase 4 (enforcement / required variants), phase 5 (dropping the old free-text venue columns). Do not let them leak in. In particular: this phase makes the variants *possible*, not *required*.

---

### Task 1: Label variant columns

**Files:**
- Modify: `src/app/db/models.py:305` (`ConcertDay.label`), `:352-353` (`Round.label`/`label_en`)
- Test: `tests/test_i18n_ugc.py`

**Interfaces:**
- Produces: `ConcertDay.label_en`, `ConcertDay.label_zh` (`str | None`, `String(100)`); `Round.label_zh` (`str | None`, `String(200)`). Task 2 migrates them; Tasks 3-6 read them.

- [ ] **Step 1: Write the failing test**

```python
def test_label_variant_columns_are_nullable():
    for model, name, length in (
        (ConcertDay, "label_en", 100), (ConcertDay, "label_zh", 100),
        (Round, "label_zh", 200),
    ):
        col = model.__table__.columns.get(name)
        assert col is not None, f"{model.__name__}.{name} missing"
        assert col.nullable, f"{model.__name__}.{name} must be nullable"
        assert col.type.length == length


def test_loc_field_resolves_round_label():
    r = Round(label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行")
    assert loc_field(r, "label", "en") == "1st-round lottery"
    assert loc_field(r, "label", "zh") == "第一轮先行"
    assert loc_field(r, "label", "ja") == "1次先行抽選"


def test_loc_field_resolves_day_label_without_chaining():
    d = ConcertDay(label="2日目 夜公演", label_en="Day 2 evening")
    assert loc_field(d, "label", "en") == "Day 2 evening"
    # zh is unfilled; it must fall through to the ORIGINAL, never to the
    # English variant.
    assert loc_field(d, "label", "zh") == "2日目 夜公演"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_i18n_ugc.py::test_label_variant_columns_are_nullable -q`
Expected: FAIL — `ConcertDay.label_en missing`

- [ ] **Step 3: Add the columns**

`ConcertDay`, after `label`:

```python
    label: Mapped[str] = mapped_column(String(100))  # "Day 1", "Day 2 夜公演"
    # Viewer-locale variants of the leg label; ja IS the original column (see
    # i18n.loc_field). Nullable and never backfilled -- an unfilled variant
    # falls through to the original.
    label_en: Mapped[str | None] = mapped_column(String(100))
    label_zh: Mapped[str | None] = mapped_column(String(100))
```

`Round`, replacing the bare `label_en` line:

```python
    label: Mapped[str] = mapped_column(String(200))  # "最速先行 Round 1", "Day 2 配信"
    # Viewer-locale variants. label_en PREDATES the i18n layer -- it was an
    # English gloss rendered to every viewer at once. It is now a true locale
    # variant selected by loc_field; existing values are already English, so
    # they carry over unchanged.
    label_en: Mapped[str | None] = mapped_column(String(200))
    label_zh: Mapped[str | None] = mapped_column(String(200))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_i18n_ugc.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/db/models.py tests/test_i18n_ugc.py
git commit -m "feat: add locale variants for leg and round labels"
```

---

### Task 2: Migration

**Files:**
- Create: `alembic/versions/<generated>_label_locale_variants.py`
- Test: `tests/test_migration_label_variants.py`

**Interfaces:**
- Consumes: the columns from Task 1.

- [ ] **Step 1: Generate the revision**

```bash
uv run alembic revision -m "label locale variants"
```

Do NOT use `--autogenerate`. Set `down_revision = '789bbcc95bc3'`.

- [ ] **Step 2: Write the migration**

```python
"""label locale variants

Revision ID: <generated>
Revises: 789bbcc95bc3
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = '<generated>'
down_revision = '789bbcc95bc3'
branch_labels = None
depends_on = None


# `concert_days` and `rounds` predate the naming convention, so their
# constraints are anonymous on the live server. Batch mode copies them and
# refuses to name them itself unless handed the convention.
def upgrade() -> None:
    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('label_en', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('label_zh', sa.String(length=100), nullable=True))

    with op.batch_alter_table(
        'rounds', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('label_zh', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        'rounds', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('label_zh')

    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('label_zh')
        batch_op.drop_column('label_en')
```

There is no backfill: `Round.label_en` already holds English values and keeps them; the other three columns start NULL and fall through to the original.

- [ ] **Step 3: Write the legacy-shaped test**

Follow `tests/test_migration_venue_tag_backfill.py` exactly — including its `_run_upgrade` helper, which **monkeypatches `settings.database_url`**. `alembic/env.py` builds its URL from `settings.database_url` and IGNORES `cfg.set_main_option("sqlalchemy.url", ...)`, so a helper that only sets the config option runs the migration against the REAL repo database. Also stamp an `alembic_version` row and pin the upgrade to this revision rather than `"head"`.

The fixture must write `concert_days` and `rounds` with ANONYMOUS constraints (no `CONSTRAINT` keyword), and assert they are anonymous, so the test cannot silently become a tautology. Assert the three columns land and that `PRAGMA foreign_key_check` is clean after the rebuild.

- [ ] **Step 4: Run the migration test**

Run: `uv run pytest tests/test_migration_label_variants.py -q`
Expected: PASS

- [ ] **Step 5: Apply locally and run the full suite**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
uv run pytest -q --deselect tests/test_web.py::test_healthz
uv run ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/ tests/test_migration_label_variants.py
git commit -m "feat: migrate label locale variant columns"
```

---

### Task 3: Round labels render in the viewer's language

This is the semantic change to `label_en`. Today `_round_rows.html:52` renders:

```jinja
<span class="nm">{{ r.label }}<em>{% if r.label_en %}{{ r.label_en }} · {% endif %}{{ round_kind_label(r.kind) }}</em></span>
```

— the Japanese label AND the English gloss, to everyone. It becomes one locale-selected label plus the (already translated) kind.

**Files:**
- Modify: `src/app/web/templates/_round_rows.html:52`, `:100`; `_round_qualifier_chips.html:38`
- Test: `tests/test_concert_page.py`

**Interfaces:**
- Consumes: `Round.label_zh` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
async def test_round_label_renders_in_the_viewers_language(client, db):
    async with db() as session:
        concert = Concert(title="T", event_id="rl1")
        session.add(concert)
        await session.flush()
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
        ))
        session.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行",
            closes_at_utc=datetime(2026, 7, 1, 9, tzinfo=UTC),
        ))
        await session.commit()

    en = await client.get("/concerts/rl1", cookies={"lang": "en"})
    assert "1st-round lottery" in en.text
    assert "1次先行抽選" not in en.text, "the Japanese label must not leak to an EN viewer"

    zh = await client.get("/concerts/rl1", cookies={"lang": "zh"})
    assert "第一轮先行" in zh.text
    assert "1st-round lottery" not in zh.text, "no cross-locale chaining"

    ja = await client.get("/concerts/rl1", cookies={"lang": "ja"})
    assert "1次先行抽選" in ja.text
    assert "1st-round lottery" not in ja.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_concert_page.py::test_round_label_renders_in_the_viewers_language -q`
Expected: FAIL — the EN viewer sees both labels, so `"1次先行抽選" not in en.text` fails.

- [ ] **Step 3: Localize the round label**

`_round_rows.html:52`:

```jinja
        <span class="nm">{{ loc(r, "label") }}<em>{{ round_kind_label(r.kind) }}</em></span>
```

`_round_rows.html:100` — the "Next for you" standing block:

```jinja
{{ loc(next_row.round_, "label") }}
```

`_round_qualifier_chips.html:38`:

```jinja
{{ loc(other, "label") }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_concert_page.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web/templates/ tests/test_concert_page.py
git commit -m "feat: render round labels in the viewer's language"
```

---

### Task 4: Leg labels render in the viewer's language

**Files:**
- Modify: `src/app/web/templates/_round_rows.html:127`, `_round_leg_chips.html:32`, `home.html:227`, `discover.html:117`
- Test: `tests/test_concert_page.py`, `tests/test_home.py`

**Interfaces:**
- Consumes: `ConcertDay.label_en`/`label_zh` (Task 1).

**Note on `_leg_chips_script.html:79`** (`chip.textContent = leg.label`): that is the CLIENT-side chip builder for rows the editor is currently adding, reading values out of the form. Editor-side, not viewer-side — leave it alone. Say in your report that you checked it and why you left it.

- [ ] **Step 1: Write the failing test**

```python
async def test_leg_label_renders_in_the_viewers_language(client, db):
    async with db() as session:
        concert = Concert(title="T", event_id="ll1")
        session.add(concert)
        await session.flush()
        session.add(ConcertDay(
            concert_id=concert.id, label="2日目 夜公演",
            label_en="Day 2 evening", label_zh="第二天 夜场",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
        ))
        await session.commit()

    en = await client.get("/concerts/ll1", cookies={"lang": "en"})
    assert "Day 2 evening" in en.text
    assert "2日目 夜公演" not in en.text

    zh = await client.get("/concerts/ll1", cookies={"lang": "zh"})
    assert "第二天 夜场" in zh.text

    ja = await client.get("/concerts/ll1", cookies={"lang": "ja"})
    assert "2日目 夜公演" in ja.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_concert_page.py::test_leg_label_renders_in_the_viewers_language -q`
Expected: FAIL — `"2日目 夜公演" not in en.text`

- [ ] **Step 3: Localize the leg label**

`_round_rows.html:127` — the leg heading:

```jinja
      <h3 class="leg-heading{% if day.starts_at_utc < now %} past{% endif %}">{{ loc(day, "label") }}{% if day.cancelled %} <span class="badge cancelled">{{ _("Cancelled") }}</span>{% elif skipped %} <span class="badge cancelled">{{ _("Not going") }}</span>{% endif %}</h3>
```

`_round_leg_chips.html:32` — keep the existing fallback chain shape, localizing only the label:

```jinja
    >{{ loc(d, "label") or (loc(d.venue_tag, "name") if d.venue_tag else "") or (jst(d.starts_at_utc).strftime('%b %d') if d.starts_at_utc else _('Leg')) }}</button>
```

`home.html:227` and `discover.html:117`:

```jinja
{{ loc(d, "label") }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_concert_page.py tests/test_home.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web/templates/ tests/
git commit -m "feat: render leg labels in the viewer's language"
```

---

### Task 5: Localize labels where the service layer copies them

Six sites copy a label string into a dataclass before it reaches a template, so a `loc()` in the template cannot reach them. Each must resolve the variant where the dataclass is built.

**Files:**
- Modify: `src/app/db/service.py` — `my_deadline_rows` (`DeadlineRow.label`), `upcoming_deadlines`/`my_upcoming_deadlines` (`UpcomingDeadline`), `board_cards` (`Rung.label`), the setup tiles, and `notice_context`
- Test: `tests/test_board_queries.py`, `tests/test_messages.py`

**Interfaces:**
- Consumes: `Round.label_zh`, `ConcertDay.label_en`/`label_zh` (Task 1).

**Two locale sources, do not mix them up.** Web-request paths use `get_locale()` (the request ContextVar). Per-recipient paths — `notice_context` and anything the scheduler drains — must use the RECIPIENT's `user.language`, because the context is built once for many recipients outside any request. `notice_context` already does this correctly for `title` and `venue`; follow that.

**Also fix `tags_line` while you are here.** `notice_context` builds it as `" · ".join(t.name for ...)` — raw `Tag.name`, unlocalized, on the same line as a `loc_field`-resolved title. Tag names have had `name_en`/`name_zh` since the i18n build. Localize them with the same `locale` variable. Do the same for `first_deadline_label`, which is a raw `Round.label`.

- [ ] **Step 1: Write the failing test**

```python
async def test_dm_notice_localizes_tag_names_and_round_label(db):
    """notice_context resolves title and venue per recipient but leaves the
    tag line and the deadline label raw."""
    async with db() as session:
        tag = Tag(name="蓮ノ空", name_en="Hasunosora", name_zh="莲之空",
                  kind=TagKind.GROUP)
        session.add(tag)
        user = User(discord_id=1, username="u", language="en")
        session.add(user)
        concert = Concert(title="T", event_id="n1")
        session.add(concert)
        await session.flush()
        session.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        session.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label="1次先行抽選", label_en="1st-round lottery",
            closes_at_utc=datetime(2030, 8, 1, 9, tzinfo=UTC),
        ))
        await session.commit()

        ctx = await notice_context(session, concert.id, 1)

    assert ctx.tags_line == "Hasunosora", "tag names must resolve to the recipient's locale"
    assert ctx.first_deadline_label == "1st-round lottery"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_messages.py::test_dm_notice_localizes_tag_names_and_round_label -q`
Expected: FAIL — `assert '蓮ノ空' == 'Hasunosora'`

- [ ] **Step 3: Localize each site**

In `notice_context`, using the existing `locale` variable:

```python
    non_venue = [loc_field(t, "name", locale) for t in concert.tags if t.kind.value != "venue"]
    venues = [loc_field(t, "name", locale) for t in concert.tags if t.kind.value == "venue"]
```

and

```python
        first_deadline_label=loc_field(first[0], "label", locale) if first else None,
```

In the web-request paths, use `get_locale()` and `loc_field` wherever a `Round.label` or `ConcertDay.label` is copied into `DeadlineRow`, `UpcomingDeadline`, `Rung`, or a setup tile. Grep `\.label` in `service.py` and handle every assignment that copies one of those two columns — the report must list each site you found and changed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_messages.py tests/test_board_queries.py tests/test_concert_rows.py -q`
Expected: PASS

Several existing tests assert on raw label values (`tests/test_board_queries.py:240,320,333`, `tests/test_concert_rows.py:80,181,…`). Those run under the default `en` locale with no variants filled, so `loc_field` returns the original and they should keep passing unchanged. If one fails, check whether the fixture filled a variant before changing the test.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/
git commit -m "feat: localize labels and tag names where the service layer copies them"
```

---

### Task 6: Editor inputs for the new variants

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`apply_day_fields`, `build_day`, `apply_round_fields`, `build_round`, create + edit form params and zips), `src/app/web/routes/imports.py`
- Modify: `concert_new.html`, `concert_edit.html`, `import_preview.html`, `_leg_chips_script.html`
- Test: `tests/test_editor_legs.py`

**Interfaces:**
- Produces: form fields `day_label_en`, `day_label_zh`, `round_label_zh`.

**Padding, read carefully.** `round_label_en` is NOT padded in `concerts.py` — it rides the strict zip 1:1 with `round_label`, because every round row template emits both. `imports.py:337` DOES pad it. Give `round_label_zh` the SAME treatment as `round_label_en` in each route (unpadded in `concerts.py`, padded in `imports.py`), and give `day_label_en`/`day_label_zh` the same rule the neighbouring `day_label` uses in each route. Emitting the input in every row template is what makes the unpadded rule safe — if you add an input to one template and not another, the strict zip raises.

- [ ] **Step 1: Write the failing test**

```python
async def test_editor_round_trips_label_variants(editor_client, db):
    resp = await editor_client.post("/concerts", data={
        "title": "T", "event_id": "lv1",
        "day_label": ["2日目"], "day_label_en": ["Day 2"], "day_label_zh": ["第二天"],
        "day_starts_at": ["2026-08-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行"], "round_label_en": ["1st advance"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-07-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with db() as session:
        concert = (await session.execute(
            select(Concert).where(Concert.event_id == "lv1")
        )).scalar_one()
        await session.refresh(concert, ["days", "rounds"])
        day, round_ = concert.days[0], concert.rounds[0]

    assert (day.label, day.label_en, day.label_zh) == ("2日目", "Day 2", "第二天")
    assert (round_.label, round_.label_en, round_.label_zh) == (
        "1次先行", "1st advance", "第一轮先行",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_editor_legs.py::test_editor_round_trips_label_variants -q`
Expected: FAIL — the variants are not persisted (`(None, None)`).

- [ ] **Step 3: Thread the fields through**

Add `label_en: str = ""`, `label_zh: str = ""` to `apply_day_fields` and `build_day`, assigning `.strip() or None`. Add `label_zh: str = ""` to `apply_round_fields` and `build_round` beside the existing `label_en`.

Add the form params to the create route, the edit route and `import_commit`, and include them in each route's zip following the padding rule above.

- [ ] **Step 4: Add the inputs**

Beside each existing `day_label` input (`concert_new.html:46,148`; `concert_edit.html:101,239`), add:

```jinja
        <input name="day_label_en" maxlength="100" placeholder="{{ _('Day 1') }}">
        <input name="day_label_zh" maxlength="100" placeholder="{{ _('Day 1') }}">
```

with `value="{{ d.label_en or '' }}"` / `value="{{ d.label_zh or '' }}"` on the pre-filled edit rows only.

Beside each `round_label_en` input (`concert_new.html:177`; `concert_edit.html:62,269`; `import_preview.html:104,197`), add a `round_label_zh` input with the same shape.

Note `import_preview.html:103-104` does NOT pre-fill `round_label_en`'s `value=` even on an existing row, unlike `concert_edit.html`. Match whatever that template already does for its sibling — do not silently change import's behaviour in this task.

Update `_leg_chips_script.html`'s cloned-row template so a JS-added leg emits the new day inputs too. A cloned row missing them is a strict-zip 500 on save wherever the day arrays are padded 1:1.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_editor_legs.py tests/test_crud.py tests/test_venue_rollup.py -q`
Expected: PASS. Existing tests that post `round_label_en` without `round_label_zh` will now trip the strict zip — add the key to their payloads (that is the contract those payloads exist to satisfy).

- [ ] **Step 6: Update both catalogues**

```bash
uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run pybabel update -i messages.pot -d src/app/translations -l ja
uv run pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill every new and fuzzy msgstr by hand in both files, clear the fuzzy flags, then `rm messages.pot`. **Check pybabel's fuzzy matches — it has silently mismatched unrelated entries on this project before.**

Run: `uv run pytest tests/test_i18n_catalogues.py -q`

- [ ] **Step 7: Commit**

```bash
git add src/app/web/ src/app/translations/ tests/
git commit -m "feat: edit leg and round labels in all three languages"
```

---

### Task 7: The notes guard, and the YAML export

Two loose ends the spec names.

**Files:**
- Modify: `src/app/web/templates/concert_detail.html:29`, `src/app/domain/yaml_export.py`, `src/app/web/routes/concerts.py` (the YAML adaptation around `:1397`)
- Test: `tests/test_concert_page.py`, `tests/test_yaml_export.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_notes_render_when_only_a_variant_is_filled(client, db):
    """The guard tests the Japanese column, so notes filled ONLY in English
    render nothing."""
    async with db() as session:
        session.add(Concert(title="T", event_id="nt1", notes=None,
                            notes_en="Doors open early."))
        await session.commit()

    resp = await client.get("/concerts/nt1", cookies={"lang": "en"})
    assert "Doors open early." in resp.text


def test_yaml_export_carries_every_label_variant():
    day = YamlDay(label="2日目", label_en="Day 2", label_zh="第二天",
                  starts_at="2026-08-01T18:00")
    out = concert_to_yaml(title="T", event_id="y1", performances=[day], rounds=[])
    assert "Day 2" in out and "第二天" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_concert_page.py::test_notes_render_when_only_a_variant_is_filled -q`
Expected: FAIL — the notes block is not rendered at all.

- [ ] **Step 3: Fix the guard**

`concert_detail.html:29` — guard on the RESOLVED value, not the original column:

```jinja
{% set notes_text = loc(concert, "notes") %}
{% if notes_text %}<p class="dim">{{ notes_text }}</p>{% endif %}
```

- [ ] **Step 4: Carry the variants into the export**

`YamlDay` gains `label_en` and `label_zh`; `YamlRound` gains `label_zh` beside its existing `label_en`. Emit them in both list comprehensions (`yaml_export.py:94-104` for days, `:106-118` for rounds), and pass them through from the route's adaptation.

An export is data, not a viewer-facing render — emit the raw columns, never `loc_field`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q --deselect tests/test_web.py::test_healthz && uv run ruff check .`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/app/web/ src/app/domain/ tests/
git commit -m "feat: fix the notes guard and carry label variants into the YAML export"
```

---

## Done when

- `uv run pytest -q` and `uv run ruff check .` are green.
- A concert page shows its leg labels and round labels in the viewer's language, with no Japanese leaking to an EN or ZH viewer.
- `Round.label_en` is a locale variant, not a gloss shown to everyone.
- A DM notice's tag line and deadline label resolve to the recipient's language.
- Notes filled only in English render for an English viewer.
- The editor can type all three languages for both label kinds.

**Not in this phase:** the round-label phrase library (phase 3), required-variant enforcement (phase 4), dropping the phase-1 free-text venue columns (phase 5).
