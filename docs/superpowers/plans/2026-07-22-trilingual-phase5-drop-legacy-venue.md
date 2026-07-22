# Trilingual Concert Pages — Phase 5: Drop the Legacy Venue Columns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the six free-text venue columns the two-deploy migration kept alive, now that every venue lives on a leg's VENUE tag.

**Architecture:** Phase 1 moved venues onto leg tags and kept `Concert.venue`/`venue_en`/`venue_zh` and `ConcertDay.city`/`venue`/`venue_address` as read-only recovery data, in case a leg's free-text venue didn't match a tag during the backfill. The owner has since run the unmatched-legs query against production: **zero unmatched legs.** The recovery data has done its job, so the columns can go. Every remaining read is a fallback behind a "no venue tag" branch that collapses to nothing; every write is to a column about to be dropped.

**Tech Stack:** Python 3.14, SQLAlchemy async, Alembic (SQLite batch mode), FastAPI, Jinja2, pytest.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any commit.
- **The full suite takes ~200s; the Bash tool's default timeout is 120s.** Pass an explicit `timeout` of 600000ms to every test run, or it gets killed mid-run and leaves partial output that reads like a pass.
- `tests/test_web.py::test_healthz` is a genuine wall-clock flake (passes in isolation); ANY other failure means something broke.
- Current alembic head is `14bc590fdb44`. The drop migration in Task 3 uses it as `down_revision`.
- **The migration DROPS columns from `concerts` and `concert_days` — both predate the NAMING_CONVENTION.** Every `batch_alter_table` MUST pass `naming_convention=NAMING_CONVENTION`, and the migration MUST be tested against a hand-written legacy-shaped DDL fixture with anonymous constraints. A drop migration that passes on a metadata-built DB can still die on the server with `ValueError: No such constraint` — this has shipped once.
- **Migration tests MUST monkeypatch `settings.database_url`.** `alembic/env.py` builds its URL from settings and IGNORES `cfg.set_main_option`, so a test that only sets the config option runs against the real repo database. Copy the helper in `tests/test_migration_venue_tag_backfill.py`.
- After autogenerate (do NOT autogenerate this one — hand-write it), never reference `app.db.models.UTCDateTime()` — no datetime columns here anyway.
- Business logic in `src/app/db/service.py`; routes thin.
- Sentence case in UI copy. Removing an input that had a translated label is fine; do NOT edit an existing English msgid, and do NOT delete an msgid still used elsewhere. If a label becomes wholly unused, leaving its catalogue entry is harmless (an unused msgid is not an error); only touch `.po` files if `tests/test_i18n_catalogues.py` demands it.
- Before trusting any bulk/scripted edit, read `git diff --stat` and sanity-check line counts.

## Task order is load-bearing

Reads first, then writes+forms, then the model+migration. A column with no readers and no writers is harmless until its mapped attribute is removed — so each task stays green. **Do not drop the columns (Task 3) before the reads and writes are gone**, or SQLAlchemy still emits `SELECT concerts.venue` against a schema without it and every test 500s.

## Out of scope

Nothing after this — phase 5 is the last phase. Do not touch the venue TAG plumbing (`ConcertDay.venue_tag_id`, `sync_concert_venue_tags`, the `venue_tags` accepted-but-discarded route params). Those are the current model and stay.

---

### Task 1: Remove the fallback reads

Every read of the six columns is a fallback behind a "no venue tag" branch. With the columns gone there is nothing to fall back to, so each collapses to its tag-only branch. This also retires a known bug: `discover.html`'s guard tests raw `c.venue` while its body renders `loc(c, "venue")`, so a concert with only `venue_en`/`venue_zh` never renders — dropping the columns deletes the buggy half.

**Files:**
- `src/app/web/templates/discover.html:55-56`, `home.html:28`, `_board.html:69`, `setup.html:40-41`, `_deadline_rows.html:44`, `_round_rows.html:142`
- `src/app/db/service.py` — `my_deadline_rows` (`DeadlineRow.venue`, ~1541), `_setup_tile_venue` (~1603), `notice_context` (`NoticeContext.venue`, ~3441), and `TRACKED_CONCERT_FIELDS` (~2643, drop `venue_en`/`venue_zh`)
- `src/app/web/routes/discover.py` — `concert_search_text` (~119), drop the `c.venue`/`venue_en`/`venue_zh` append
- `src/app/web/routes/concerts.py` — `export_concert_yaml` (~1450), drop the `else d.city`/`else d.venue`/`else d.venue_address` fallbacks (keep the `d.venue_tag.x` primary)
- Tests: `tests/test_crud.py`, `tests/test_venue_rollup.py`, `tests/test_tags.py`, `tests/test_home.py`, `tests/test_i18n_ugc.py`

**The reads still reference the columns via the model attribute, which still exists after this task — so `getattr` works and nothing errors. This task changes DISPLAY behaviour, not the schema.**

**Contract changes — call these out, do not paper over:** several tests assert the fallback WORKS and must be DELETED, not patched, because the behaviour is intentionally gone:
- `test_concert_page_falls_back_to_free_text_venue` (in `test_crud.py` and/or `test_venue_rollup.py`)
- `test_yaml_export_falls_back_to_free_text_venue`
- `test_index_search_falls_back_to_free_text_venue_when_no_venue_tag` (`test_tags.py`)
- the `notice_context` venue-fallback assertion, and the `_setup_tile_venue` fallback, if any test pins them

Tests that seed `venue=` only to drive rendered HTML and assert on the tag path (`test_home.py`, most of `test_concert_page.py`) keep passing — but a concert seeded with `venue=` and NO tag will now render no venue. Update those to attach a VENUE tag if the assertion needs a venue to appear; delete the assertion if it was testing the dropped fallback.

- [ ] **Step 1:** Grep every read listed above; for each template, collapse the `X or loc(c, "venue")` / `X or c.venue` to just `X`, and drop the `{% elif ... c.venue %}` / `{% elif day.venue or day.city %}` fallback branches entirely.
- [ ] **Step 2:** In `service.py`, each venue ternary (`... else loc_field(concert, "venue", ...)`) collapses to its non-fallback branch (`None` when no tag). Remove `venue_en`/`venue_zh` from `TRACKED_CONCERT_FIELDS`.
- [ ] **Step 3:** In `concert_search_text`, delete the `if not any(... VENUE ...): parts += [c.venue, c.venue_en, c.venue_zh]` block.
- [ ] **Step 4:** In `export_concert_yaml`, each `d.venue_tag.x if d.venue_tag else d.x` becomes `d.venue_tag.x if d.venue_tag else None`.
- [ ] **Step 5:** Delete the fallback-path tests named above. Update seed-only tests that now render no venue.
- [ ] **Step 6:** `uv run pytest -q` (600000ms timeout) and `uv run ruff check .`. Commit: `refactor: drop the free-text venue fallbacks now every venue is a tag`.

---

### Task 2: Remove the writes and the form inputs

**Files:**
- `src/app/web/routes/concerts.py` — `apply_day_fields` (drop the three preserve-on-empty lines and the `city`/`venue`/`venue_address` params), `build_day` (drop those params), `create_concert`/`edit_concert` (drop `venue_en`/`venue_zh` and `day_city`/`day_venue`/`day_venue_address` `Form(...)` params and the `concert.venue_en =`/`venue_zh =` assignments), `create_concert_row` (drop `venue=None`), `duplicate_concert` (drop `venue=None`)
- `src/app/web/routes/imports.py` — `import_commit` (drop `day_city`/`day_venue`/`day_venue_address` params and their `build_day` args)
- `src/app/web/templates/concert_new.html:133,141`, `concert_edit.html:192,200` — remove the `venue_en`/`venue_zh` inputs (and the now-empty "Translations" venue rows around them)
- `src/app/web/templates/import_preview.html:124-125,282` — remove the free-text `day_venue` input (the venue TAG picker plus the "no match — create it" hint added earlier already cover this; the scraped name survives as the hint, not a written column)
- Tests: `tests/test_i18n_ugc.py` (`test_edit_form_persists_translation_variants` POSTs `venue_en`/`venue_zh` and asserts they persist + hit the audit log — this is the strongest must-change test), `tests/test_editor_legs.py` (the preserve-on-empty test `test_an_edit_save_does_not_destroy_a_legs_free_text_venue` — DELETE it, the behaviour it protects is being removed), `tests/test_crud.py`, `tests/test_venue_rollup.py`, and the many payloads posting `day_venue`/`day_city`/`venue_en`

**The model attribute still exists after this task, so removing the writes just leaves the columns unwritten. Green.**

**Contract changes to call out:**
- `test_an_edit_save_does_not_destroy_a_legs_free_text_venue` and its `test_crud.py` twin — DELETE. Preserve-on-empty existed to protect data we are now dropping.
- `test_edit_form_persists_translation_variants` — remove the `venue_en`/`venue_zh` half (keep the `title`/`notes` half); those inputs no longer exist.
- `test_snapshot_concert_includes_new_fields` (`test_i18n_ugc.py`) — remove the `venue_en`/`venue_zh` key assertions.
- Any payload posting `day_venue`/`day_city`/`day_venue_address`/`venue_en`/`venue_zh` — drop those keys. The routes no longer declare the params, so an unknown form field is simply ignored (not an error), but drop them for honesty.

- [ ] **Step 1:** Delete the three preserve-on-empty lines in `apply_day_fields` and the `city`/`venue`/`venue_address` params from it and `build_day`; update every `build_day`/`apply_day_fields` call to stop passing them.
- [ ] **Step 2:** Drop `venue_en`/`venue_zh`/`day_city`/`day_venue`/`day_venue_address` `Form(...)` params from `create_concert`, `edit_concert`, `import_commit`; drop the `concert.venue_en =`/`venue_zh =` assignments; drop `venue=None` from `create_concert_row` and `duplicate_concert`.
- [ ] **Step 3:** Remove the venue inputs from `concert_new.html`, `concert_edit.html`, and the free-text `day_venue` from `import_preview.html` (both the parsed row and the `<template>`).
- [ ] **Step 4:** Delete/trim the tests named above; drop the dropped keys from payloads.
- [ ] **Step 5:** `uv run pytest -q` (600000ms) and `uv run ruff check .`. Commit: `refactor: stop writing the free-text venue columns and remove their inputs`.

---

### Task 3: Drop the columns

Now nothing reads or writes them. Remove the model attributes and drop them from the DB in one migration.

**Files:**
- `src/app/db/models.py` — remove `Concert.venue`, `venue_en`, `venue_zh`; `ConcertDay.city`, `venue`, `venue_address`
- Create: `alembic/versions/<generated>_drop_legacy_venue_columns.py`
- Test: `tests/test_migration_drop_legacy_venue.py` (create)

**Interfaces:** the column types being reversed (from the migrations that created them): `Concert.venue`/`venue_en`/`venue_zh` all `String(200)`; `ConcertDay.city` `String(100)`, `venue` `String(200)`, `venue_address` `String(300)` — all nullable. The `downgrade()` recreates them (empty — the data is gone, which is the point).

- [ ] **Step 1: Remove the model attributes.**

Delete the six `mapped_column` lines. Run `uv run pytest tests/test_i18n_ugc.py -q` — any lingering reference now fails at import or attribute access, which is how you find a missed consumer from Tasks 1-2. Fix any that surface (there should be none).

- [ ] **Step 2: Generate and hand-write the migration.**

```bash
uv run alembic revision -m "drop legacy venue columns"
```

Set `down_revision = '14bc590fdb44'`. Do NOT autogenerate.

```python
"""drop legacy venue columns

Revision ID: <generated>
Revises: 14bc590fdb44
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION

revision = '<generated>'
down_revision = '14bc590fdb44'
branch_labels = None
depends_on = None


# concerts and concert_days predate the naming convention, so their
# constraints are anonymous on the live server. Batch mode copies them and
# refuses to name them itself unless handed the convention -- without this a
# drop can die on the server with "No such constraint" (has shipped once).
def upgrade() -> None:
    with op.batch_alter_table('concerts', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_column('venue_zh')
        b.drop_column('venue_en')
        b.drop_column('venue')
    with op.batch_alter_table('concert_days', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_column('venue_address')
        b.drop_column('venue')
        b.drop_column('city')


def downgrade() -> None:
    # Recreates the columns empty -- the free-text data is gone by design.
    with op.batch_alter_table('concert_days', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.add_column(sa.Column('city', sa.String(length=100), nullable=True))
        b.add_column(sa.Column('venue', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_address', sa.String(length=300), nullable=True))
    with op.batch_alter_table('concerts', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.add_column(sa.Column('venue', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_en', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_zh', sa.String(length=200), nullable=True))
```

- [ ] **Step 3: Legacy-shaped DDL test.**

Create `tests/test_migration_drop_legacy_venue.py`. Copy the `_run_upgrade` helper from `tests/test_migration_venue_tag_backfill.py` INCLUDING its `settings.database_url` monkeypatch, `alembic_version` stamp, and explicit-revision pin.

Hand-write a legacy-shaped `concerts` and `concert_days` with **anonymous** constraints (no `CONSTRAINT` keyword) and the six columns present. Assert `"CONSTRAINT" not in sql` for both, so the fixture cannot silently become named. After upgrade, assert the six columns are gone (`PRAGMA table_info`) and `PRAGMA foreign_key_check` is clean. Add a downgrade-then-upgrade round-trip.

The `concert_days` legacy DDL must include `venue_tag_id` (added by an earlier migration) so the batch rebuild reflects a realistic post-backfill shape — see `tests/test_migration_label_variants.py`'s fixture for the current column set.

- [ ] **Step 4: Apply and round-trip.**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
uv run pytest -q   # 600000ms timeout
uv run ruff check .
```

- [ ] **Step 5: Commit.**

```bash
git add src/app/db/models.py alembic/versions/ tests/test_migration_drop_legacy_venue.py
git commit -m "feat: drop the legacy free-text venue columns"
```

---

## Done when

- `uv run pytest -q` and `uv run ruff check .` are green.
- The six columns are gone from the model and the DB.
- A concert with no leg venue tag shows no venue anywhere — no free-text fallback remains.
- `discover.html`'s venue-guard bug is gone with the columns it depended on.
- The migration drops cleanly against a legacy-shaped fixture with anonymous constraints.

## Deploy — READ THIS, the order is reversed for this migration

The usual ritual is `git pull && uv sync && alembic upgrade head && restart`. **For this deploy, restart BEFORE migrating:**

```
cd ~/app && git pull && uv sync && sudo systemctl restart concert-reminder && uv run alembic upgrade head
```

Why: this migration DROPS columns. In the usual order, the window between `alembic upgrade head` and `restart` runs the OLD code — whose SQLAlchemy models still map `Concert.venue` etc. — against a schema that no longer has them, so every `SELECT` 500s until the restart. Restarting first puts the new code (which never references the columns) live against the old schema (the extra columns are harmless), and the drop then happens with nothing reading them. Zero error window.

This is irreversible in production: `downgrade` recreates the columns but not the data. That is intentional and safe — the owner confirmed zero unmatched legs, so no free-text venue holds anything the venue tags don't.
