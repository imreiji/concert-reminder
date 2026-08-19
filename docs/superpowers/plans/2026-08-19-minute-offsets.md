# Minute-level reminder offsets — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reminder can fire a number of minutes before or after its anchor, not just whole hours — and every surface that describes a rule says so correctly.

**Architecture:** One new signed column (`offset_minutes`) on `ReminderRule` and `PresetItem`, carried through the pure planner and every write path; one new module `app/offsets.py` owning parse / format / describe for sub-day offsets; the preferences and concert-page editors swap their hours `<select>` for an HH:MM text box; the wizard keeps its curated select and gains sub-hour entries.

**Tech Stack:** Python 3.13, SQLAlchemy 2 async + Alembic (SQLite), FastAPI + Jinja2 + htmx, discord.py, Babel/gettext (en/ja/zh).

**Spec:** `docs/superpowers/specs/2026-08-19-minute-offsets-design.md` — read it before Task 1. The plan argues from the spec; where they disagree, the spec wins and you should say so rather than guessing.

## Global Constraints

- **The code in this plan is UNVERIFIED.** It was written by reading the repo, not by running anything. Treat every snippet as a sketch of intent: if it does not compile, does not match the surrounding style, or is simply wrong, fix it and say what you changed. Reviews of a previous plan of this shape found a real defect in 11 of 12 tasks.
- **Verification commands:** `uv run --isolated pytest -q <paths> -n0` while iterating (`--isolated` because the shared `.venv` is often locked by unrelated processes; `-n0` because worker startup costs ~5s on a targeted run). Run tests in the FOREGROUND with `timeout: 600000` — never background them. The full suite is `uv run --isolated pytest -q` and takes ~107s.
- **Lint:** `uv run --isolated ruff check .` must be clean before every commit. Line length 100.
- **Invariant 1 (timezones):** the DB stores aware UTC only; never construct a naive datetime in a test.
- **Invariant 2 (queue sync):** any write to a rule must be followed by `sync_rule`. Every site in this plan that creates a `ReminderRule` already does; do not drop it.
- **i18n:** editing existing English copy must keep the msgid byte-identical, or both catalogues silently lose that translation. Where this plan deliberately CHANGES a msgid, Task 8 re-translates it in both `.po` files. Never add a new user-visible English string without Task 8 covering it.
- **Sign convention:** direction is not stored. `before` = negative offsets, `after` = positive, and all three offset columns carry the same sign.
- **Canonical storage:** sub-day time is stored decomposed — `hours = total // 60`, `minutes = total % 60`. Never store 90 in `offset_minutes`.

---

### Task 1: The column, the migration, and the planner math

**Files:**
- Modify: `src/app/db/models.py:637` (ReminderRule), `src/app/db/models.py:679` (PresetItem)
- Create: `alembic/versions/<generated>_add_offset_minutes.py`
- Modify: `src/app/domain/reminders.py:14` (docstring), `:51` (RuleInfo), `:71` (offset_delta), `:104` (call site)
- Modify: `src/app/db/core.py:165-173` (`_rule_info`)
- Test: `tests/test_reminder_math.py`, `tests/test_migration_offset_minutes.py`

**Interfaces:**
- Produces: `ReminderRule.offset_minutes: int`, `PresetItem.offset_minutes: int`, `RuleInfo.offset_minutes: int = 0`, `offset_delta(offset_days: int, offset_hours: int, offset_minutes: int = 0) -> timedelta`.

- [ ] **Step 1: Write the failing planner tests**

Add to the existing domain reminder test file:

```python
async def test_a_minute_offset_moves_the_fire_time_by_exactly_that_much():
    """The mutation this must not survive: offset_delta ignoring its third argument."""
    rule = RuleInfo(
        id=1, anchor=Anchor.OPENS, offset_days=0, offset_hours=0,
        offset_minutes=-5, concert_id=1,
    )
    round_ = RoundInfo(
        id=7,
        opens_at_utc=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
        closes_at_utc=None,
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert [p.fire_at_utc for p in planned] == [datetime(2026, 9, 1, 2, 55, tzinfo=UTC)]


async def test_days_hours_and_minutes_compose():
    rule = RuleInfo(
        id=1, anchor=Anchor.CLOSES, offset_days=-1, offset_hours=-2,
        offset_minutes=-30, concert_id=1,
    )
    round_ = RoundInfo(
        id=7, opens_at_utc=None,
        closes_at_utc=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert planned[0].fire_at_utc == datetime(2026, 9, 9, 9, 30, tzinfo=UTC)


async def test_a_positive_minute_offset_fires_after_the_anchor():
    rule = RuleInfo(
        id=1, anchor=Anchor.RESULTS, offset_days=0, offset_hours=0,
        offset_minutes=15, concert_id=1,
    )
    round_ = RoundInfo(
        id=7, opens_at_utc=None, closes_at_utc=None,
        results_at_utc=datetime(2026, 9, 20, 6, 0, tzinfo=UTC),
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert planned[0].fire_at_utc == datetime(2026, 9, 20, 6, 15, tzinfo=UTC)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest -q tests/test_reminder_math.py -n0`
Expected: FAIL — `RuleInfo.__init__() got an unexpected keyword argument 'offset_minutes'`.

- [ ] **Step 3: Widen the pure math**

In `src/app/domain/reminders.py`:

```python
@dataclass(frozen=True)
class RuleInfo:
    id: int
    anchor: Anchor
    offset_days: int
    offset_hours: int = 0
    offset_minutes: int = 0
    round_id: int | None = None    # set -> rule targets one specific round
    concert_id: int | None = None  # set -> rule targets a whole concert


def offset_delta(offset_days: int, offset_hours: int, offset_minutes: int = 0) -> timedelta:
    return timedelta(days=offset_days, hours=offset_hours, minutes=offset_minutes)
```

and at the top of `plan_for_rule`:

```python
    delta = offset_delta(rule.offset_days, rule.offset_hours, rule.offset_minutes)
```

Extend the module docstring's semantics list so the third unit is documented next to the other two:

```
  * offset_days = 0  -> at the anchor moment (plus offset_hours/offset_minutes, if any)
  * All three offsets carry the SAME sign; direction is not stored separately.
```

- [ ] **Step 4: Run the planner tests — they pass**

Run: `uv run --isolated pytest -q tests/test_reminder_math.py -n0`
Expected: PASS.

- [ ] **Step 5: Add the columns**

`src/app/db/models.py`, in `ReminderRule` directly under `offset_hours`:

```python
    offset_minutes: Mapped[int] = mapped_column(default=0, server_default="0")
```

and the identical line in `PresetItem` under its own `offset_hours`.

- [ ] **Step 6: Carry it through the ORM adapter**

`src/app/db/core.py`, in `_rule_info`:

```python
        offset_minutes=r.offset_minutes,
```

- [ ] **Step 7: Generate and hand-edit the migration**

Run: `uv run --isolated alembic revision -m "add offset_minutes to rules and preset items"`
(Autogenerate is deliberately NOT used here: two `add_column`s are faster to write than to review, and autogenerate emits `app.db.models.UTCDateTime()` with no import for any datetime column it touches.)

Then write the body — `down_revision` is the current head, `fc4a98ad678a`:

```python
"""add offset_minutes to rules and preset items"""

import sqlalchemy as sa
from alembic import op

revision = "<the id alembic generated>"
down_revision = "fc4a98ad678a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only: no drop_constraint, so the legacy anonymous-constraint
    # hazard (CLAUDE.md, Migrations) does not apply and no naming_convention
    # needs threading through. Existing rows are already canonical -- whole
    # hours, zero minutes -- so there is nothing to backfill.
    for table in ("reminder_rules", "preset_items"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("offset_minutes", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    for table in ("reminder_rules", "preset_items"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("offset_minutes")
```

- [ ] **Step 8: Write the migration test**

Create `tests/test_migration_offset_minutes.py`, modelled on the existing `tests/test_migration_concert_audit.py` (read it first — copy its fixture shape exactly, including how it points `settings.database_url` at a `tmp_path` file and runs `alembic upgrade head`):

```python
def test_offset_minutes_exists_and_defaults_to_zero(tmp_path, monkeypatch):
    """A rule written by the OLD schema must read back as a zero-minute rule,
    not as NULL -- the planner adds it to a timedelta without checking."""
    db_path = tmp_path / "scratch.db"
    _upgrade_to_head(db_path, monkeypatch)  # the helper this file's sibling defines

    with sqlite3.connect(db_path) as conn:
        for table in ("reminder_rules", "preset_items"):
            cols = {row[1]: row for row in conn.execute(f"pragma table_info({table})")}
            assert "offset_minutes" in cols, f"{table} did not gain the column"
            assert cols["offset_minutes"][4] == "'0'", f"{table} default is not 0"
```

- [ ] **Step 9: Apply it locally and run both test files**

Run: `uv run --isolated alembic upgrade head`
Run: `uv run --isolated pytest -q tests/test_reminder_math.py tests/test_migration_offset_minutes.py -n0`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/app/db/models.py src/app/domain/reminders.py src/app/db/core.py alembic/versions tests/
git commit -m "feat(offsets): a signed offset_minutes column, and the planner math for it"
```

---

### Task 2: Every write path, and the dedupe tuple that fails silently

**Files:**
- Modify: `src/app/db/core.py:1149-1161` (auto-arm), `:3972-3986` (`apply_preset`), `:4150-4176` (`create_preset_from_rules`)
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `ReminderRule.offset_minutes`, `PresetItem.offset_minutes` (Task 1).
- Produces: `create_preset_from_rules(session, user_id, name, rules: list[tuple[int, int, int, str, Anchor]])` — rules are now `(days, hours, minutes, direction, anchor)`. Task 7 depends on this exact order.

- [ ] **Step 1: Write the failing dedupe test**

Add to `tests/test_presets.py`, which already has `client`, `login_as`,
`build_concert_with_deadlines`, `build_standard_preset` and `_all(db, model)`.
Note that `POST /presets` creates the preset WITH a first item (anchor closes,
3 days before), so the assertions below filter to the zero-day CLOSES rules
rather than counting every rule on the concert.

```python
async def test_a_sub_hour_item_is_not_swallowed_by_the_whole_hour_one(client):
    """The mutation this must not survive: leaving offset_minutes out of
    apply_preset's `have`/`key` tuples. Both items below anchor on CLOSES at
    zero days and zero hours, so a key without minutes makes them equal and the
    second rule is never created -- silently, with no error anywhere."""
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    client.post("/presets", data={"name": "fcfs"})
    # Seeded directly, NOT through the form: the `time` field does not exist
    # until Task 4, and the dedupe is a service-layer property anyway.
    async with client.db() as s:
        s.add_all([
            PresetItem(preset_id=1, anchor=Anchor.CLOSES,
                       offset_days=0, offset_hours=0, offset_minutes=0),
            PresetItem(preset_id=1, anchor=Anchor.CLOSES,
                       offset_days=0, offset_hours=0, offset_minutes=-30),
        ])
        await s.commit()

    assert client.post("/concerts/hasunosora-6th/presets/1/apply").status_code == 200

    rules = await _all(client.db, ReminderRule)
    at_close = sorted(
        r.offset_minutes for r in rules
        if r.anchor is Anchor.CLOSES and r.offset_days == 0
    )
    assert at_close == [-30, 0], "the 30-minute item collided with the moment one"


async def test_applying_a_preset_with_minutes_twice_creates_nothing_new(client):
    """The dedupe must keep dedupING once it has a third field to compare."""
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    client.post("/presets", data={"name": "fcfs"})
    async with client.db() as s:
        s.add(PresetItem(preset_id=1, anchor=Anchor.CLOSES,
                         offset_days=0, offset_hours=0, offset_minutes=-30))
        await s.commit()

    client.post("/concerts/hasunosora-6th/presets/1/apply")
    before = len(await _all(client.db, ReminderRule))
    client.post("/concerts/hasunosora-6th/presets/1/apply")

    assert len(await _all(client.db, ReminderRule)) == before
```

Import `Anchor` from `app.domain.types` if the file does not already.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --isolated pytest -q tests/test_presets.py -n0`
Expected: FAIL — `created == 1`, one rule row, because the two items share a key.

- [ ] **Step 3: Teach the dedupe the third field**

`src/app/db/core.py`, in `apply_preset`:

```python
    have = {(r.anchor, r.offset_days, r.offset_hours, r.offset_minutes) for r in existing.scalars()}

    created = 0
    for item in preset.items:
        key = (item.anchor, item.offset_days, item.offset_hours, item.offset_minutes)
        if key in have:
            continue
        rule = ReminderRule(
            user_id=user_id,
            concert_id=concert_id,
            anchor=item.anchor,
            offset_days=item.offset_days,
            offset_hours=item.offset_hours,
            offset_minutes=item.offset_minutes,
        )
```

- [ ] **Step 4: Carry it through the auto-arm path**

Same file, the OPENS auto-arm around line 1149:

```python
    offset_days, offset_hours, offset_minutes = 0, 0, 0
    preset = await get_default_preset(session, user_id)
    if preset is not None:
        await session.refresh(preset, ["items"])
        for item in preset.items:
            if item.anchor is Anchor.OPENS:
                offset_days, offset_hours, offset_minutes = (
                    item.offset_days, item.offset_hours, item.offset_minutes,
                )
                break

    rule = ReminderRule(
        user_id=user_id, round_id=next_round.id, anchor=Anchor.OPENS,
        offset_days=offset_days, offset_hours=offset_hours, offset_minutes=offset_minutes,
    )
```

- [ ] **Step 5: Widen the wizard's shared write path**

Same file, `create_preset_from_rules` — signature, docstring and loop:

```python
async def create_preset_from_rules(
    session: AsyncSession,
    user_id: int,
    name: str,
    rules: list[tuple[int, int, int, str, Anchor]],
) -> ReminderPreset:
    """Materialise a named preset and its items from (offset_days, offset_hours,
    offset_minutes, direction, anchor) rules -- the welcome wizard's preset step.

    [KEEP the rest of the existing docstring verbatim: the "no second preset
    write path" paragraph and the note that direction is encoded in the SIGN.
    Only the tuple shape changed.]
    """
    # [KEEP the existing preset construction and flush above this loop.]
    for offset_days, offset_hours, offset_minutes, direction, anchor in rules:
        sign = 1 if direction == "after" else -1
        session.add(PresetItem(
            preset_id=preset.id, anchor=anchor,
            offset_days=sign * offset_days, offset_hours=sign * offset_hours,
            offset_minutes=sign * offset_minutes,
        ))
```

Keep the rest of the docstring as written; only the tuple shape changed.

- [ ] **Step 6: Add an auto-arm test**

The OPENS auto-arm is exercised in `tests/test_lottery_outcomes.py` (find the
existing case with `grep -n "auto" tests/test_lottery_outcomes.py` and copy its
setup exactly rather than inventing one). Add:

```python
async def test_auto_arm_inherits_the_default_presets_sub_hour_offset(...):
    """A 5-minute OPENS item in the default preset must arm the next round at
    5 minutes before it opens, not at the anchor. The mutation this must not
    survive: the auto-arm reading offset_days/offset_hours and defaulting
    minutes to 0."""
    # seed exactly as the neighbouring auto-arm test does, but give the default
    # preset one OPENS item with offset_minutes=-5, then assert:
    assert (armed.offset_days, armed.offset_hours, armed.offset_minutes) == (0, 0, -5)
```

Write the setup out in full using that file's own fixtures; the comment above
is the specification of what to seed, not something to commit as-is.

- [ ] **Step 7: Run the file**

Run: `uv run --isolated pytest -q tests/test_presets.py -n0`
Expected: PASS.

- [ ] **Step 8: Find every remaining caller and fix the ones that break**

Run: `grep -rn "create_preset_from_rules\|offset_hours" src tests | grep -v "\.po"`
Every production call site must pass or carry minutes. `src/app/db/rehearsal.py` passes `offset_days=0, offset_hours=0` and needs NO change (the column defaults to 0). Tests that construct `PresetItem`/`ReminderRule` positionally may need updating.

- [ ] **Step 9: Run the db-layer suite**

Run: `uv run --isolated pytest -q tests/test_presets.py tests/test_service.py tests/test_crud.py -n0`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/app/db/core.py tests/
git commit -m "feat(offsets): carry offset_minutes through every rule write path

The dedupe tuple in apply_preset is the one that fails
silently: without minutes, '30 minutes before closes' collides with
'at closes' and the second rule is never created."
```

---

### Task 3: `app/offsets.py` — parse, format, describe

**Files:**
- Create: `src/app/offsets.py`
- Test: `tests/test_offsets.py`

**Interfaces:**
- Produces: `parse_hhmm(text: str) -> tuple[int, int]` (raises `ValueError`), `format_hhmm(hours: int, minutes: int) -> str`, `describe_offset(days: int, hours: int, minutes: int) -> str`. Tasks 4-7 all import these.

- [ ] **Step 1: Write the failing tests**

```python
"""What `app/offsets.py` owes its three callers.

`describe_offset` takes the SIGNED stored values and derives before/after
itself -- it deliberately does not accept a `direction` argument, because two
sources for one fact is a way for a caller to disagree with the database.
"""

import pytest

from app.i18n import set_locale
from app.offsets import describe_offset, format_hhmm, parse_hhmm


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0:30", (0, 30)),
        ("00:30", (0, 30)),
        ("3:00", (3, 0)),
        ("3", (3, 0)),        # bare number = hours, matching the select it replaced
        ("", (0, 0)),
        ("  1:05  ", (1, 5)),
    ],
)
def test_parse_hhmm_accepts(text, expected):
    assert parse_hhmm(text) == expected


@pytest.mark.parametrize("text", ["0:75", "24:00", "abc", "1:2:3", "-1:00", "1:-5"])
def test_parse_hhmm_rejects(text):
    """Rejects rather than clamps: a silently-rounded reminder is worse than a 422."""
    with pytest.raises(ValueError):
        parse_hhmm(text)


def test_format_hhmm_round_trips_what_the_box_shows():
    assert format_hhmm(0, 30) == "0:30"
    assert format_hhmm(3, 0) == "3:00"
    assert parse_hhmm(format_hhmm(1, 5)) == (1, 5)


@pytest.mark.parametrize(
    "days,hours,minutes,expected",
    [
        (0, 0, 0, "Same day"),
        (0, 0, -30, "30 minutes before"),
        (0, 0, -1, "1 minute before"),
        (0, -3, 0, "3 hours before"),      # today this renders as "Same day" -- the bug
        (0, -1, -30, "1 hour 30 minutes before"),
        (-3, 0, 0, "3 days before"),
        (-3, -6, 0, "3 days 6 hours before"),
        (0, 0, 15, "15 minutes after"),
        (1, 0, 0, "1 day after"),
    ],
)
def test_describe_offset(days, hours, minutes, expected):
    set_locale("en")
    assert describe_offset(days, hours, minutes) == expected
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run --isolated pytest -q tests/test_offsets.py -n0`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.offsets'`.

- [ ] **Step 3: Write the module**

```python
"""Sub-day reminder offsets: the text the user types, and the phrase we read back.

Lives above `db/` and beside `i18n.py` rather than inside `domain/`, because
`describe_offset` needs gettext and no `domain/` module imports `app.i18n`.
Parsing is pure and would be at home in `domain/`, but splitting five lines of
parsing from the phrase that renders them buys nothing and costs the reader the
round trip -- parse, store, format, describe -- being readable in one screen.

Both shells import this: the web layer registers `hhmm` and `describe_offset`
as Jinja globals, and `bot/cogs/reminders.py` calls `describe_offset` directly,
so the two never drift into two sets of msgids for one sentence.
"""

import re

from app.i18n import gettext as _
from app.i18n import ngettext

_HHMM_RE = re.compile(r"^(?P<hours>\d{1,2})(?::(?P<minutes>\d{1,2}))?$")


def parse_hhmm(text: str) -> tuple[int, int]:
    """Parse the editor's HH:MM box into (hours, minutes).

    Accepts "0:30", "00:30", "3" (bare = hours, which is what the hours select
    this box replaced used to post) and "" (zero). Raises ValueError on
    anything else -- including minutes > 59 and hours > 23, which are rejected
    rather than normalised: a reminder that silently moved is worse than a 422.
    """
    match = _HHMM_RE.match(text.strip())
    if text.strip() == "":
        return 0, 0
    if match is None:
        raise ValueError(f"not an h:mm value: {text!r}")
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes") or 0)
    if hours > 23:
        raise ValueError(f"hours must be 0-23, got {hours}")
    if minutes > 59:
        raise ValueError(f"minutes must be 0-59, got {minutes}")
    return hours, minutes


def format_hhmm(hours: int, minutes: int) -> str:
    """The value the box re-renders with, so a saved rule reads back as typed."""
    return f"{abs(hours)}:{abs(minutes):02d}"


def describe_offset(days: int, hours: int, minutes: int) -> str:
    """One translated phrase for a stored, SIGNED offset.

    "Same day" for a zero offset; otherwise the two largest non-zero units,
    e.g. "3 days 6 hours before", "1 hour 30 minutes before", "30 minutes
    before". Direction comes from the sign, never from an argument.
    """
    d, h, m = abs(days), abs(hours), abs(minutes)
    if (d, h, m) == (0, 0, 0):
        return _("Same day")

    parts: list[str] = []
    if d:
        parts.append(ngettext("{n} day", "{n} days", d).format(n=d))
    if h:
        parts.append(ngettext("{n} hour", "{n} hours", h).format(n=h))
    if m:
        parts.append(ngettext("{n} minute", "{n} minutes", m).format(n=m))

    quantity = " ".join(parts[:2])
    after = days > 0 or hours > 0 or minutes > 0
    pattern = _("{quantity} after") if after else _("{quantity} before")
    return pattern.format(quantity=quantity)
```

Note for the implementer: the `{quantity} before` / `{quantity} after` patterns exist so ja/zh can put the direction word where their grammar needs it (Task 8 fills them). Do not inline the English word order.

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest -q tests/test_offsets.py -n0`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/offsets.py tests/test_offsets.py
git commit -m "feat(offsets): one module for parsing, formatting and describing an offset"
```

---

### Task 4: The preferences editor's HH:MM box

**Files:**
- Modify: `src/app/web/templates/preferences.html:142-152` (the `sentence_fields` macro and its three call sites at `:171`, `:183`, `:201`)
- Modify: `src/app/web/routes/preferences.py:211-233` (create_preset), `:374-392` (add_item), `:394-417` (edit_item)
- Modify: `src/app/web/app.py` (register two Jinja globals near `:97`)
- Test: `tests/test_preferences_page.py`

**Interfaces:**
- Consumes: `parse_hhmm`, `format_hhmm` (Task 3).
- Produces: the three routes now take `time: str = Form("0:00")` instead of `hours: int`.

- [ ] **Step 1: Write the failing route tests**

Add to `tests/test_preferences_page.py`. Its `client` is a **synchronous**
`fastapi.testclient.TestClient` (no `await` on requests), `login_as(client,
USER_A, "reiji")` authenticates, and the database is reachable as `client.db`.

```python
async def test_a_preset_item_round_trips_a_sub_hour_offset(client):
    """Type 0:30, store (0, 0, -30), and read it back out of the box as 0:30."""
    login_as(client, USER_A, "reiji")
    assert client.post("/presets", data={
        "name": "fcfs", "anchor": "opens", "days": "0", "time": "0:30",
        "direction": "before",
    }).status_code == 303

    async with client.db() as s:
        item = (await s.execute(select(PresetItem))).scalar_one()
    assert (item.offset_days, item.offset_hours, item.offset_minutes) == (0, 0, -30)

    page = client.get("/preferences")
    assert 'value="0:30"' in page.text


def test_a_bad_time_value_is_refused_not_rounded(client):
    """0:75 is a typo, and a reminder that silently moved to 1:15 is worse
    than an error page."""
    login_as(client, USER_A, "reiji")
    r = client.post("/presets", data={
        "name": "typo", "anchor": "opens", "days": "0", "time": "0:75",
        "direction": "before",
    })
    assert r.status_code == 422


async def test_editing_an_item_puts_the_same_sign_on_all_three_columns(client):
    login_as(client, USER_A, "reiji")
    client.post("/presets", data={
        "name": "p", "anchor": "closes", "days": "3", "time": "0:00",
        "direction": "before",
    })
    r = client.post("/presets/1/items/1/edit", data={
        "anchor": "closes", "days": "1", "time": "1:15", "direction": "after",
    })
    assert r.status_code == 303

    async with client.db() as s:
        item = (await s.execute(select(PresetItem))).scalar_one()
    assert (item.offset_days, item.offset_hours, item.offset_minutes) == (1, 1, 15)
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest -q tests/test_preferences_page.py -n0`
Expected: FAIL — the routes ignore `time` and store `hours=0`.

- [ ] **Step 3: Change the three routes**

In each of `create_preset`, `add_item` and `edit_item`, replace the `hours` form field:

```python
    time: str = Form("0:00"),
```

and replace the `sign * hours` writes with a parse:

```python
    try:
        hours, minutes = parse_hhmm(time)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"bad time: {e}") from e
    sign = 1 if direction == "after" else -1
    session.add(PresetItem(
        preset_id=preset.id, anchor=anchor,
        offset_days=sign * days, offset_hours=sign * hours, offset_minutes=sign * minutes,
    ))
```

`edit_item` assigns the same three fields onto the existing row instead of constructing one. Import `parse_hhmm` from `app.offsets` at the top of the module.

- [ ] **Step 4: Register the Jinja globals**

`src/app/web/app.py`, beside the other globals:

```python
templates.env.globals["hhmm"] = format_hhmm
templates.env.globals["describe_offset"] = describe_offset
```

(`describe_offset` is registered here because Task 5's template needs it; registering both in one place keeps the import list short.)

- [ ] **Step 5: Swap the control in the macro**

`src/app/web/templates/preferences.html` — the macro takes minutes as well, and the hours `<select>` becomes a text box:

```jinja
    {% macro sentence_fields(days_val, hours_val, minutes_val, dir_val, anchor_val) -%}
      {%- set days_html %}<select name="days">{% for n in range(0, 61) %}<option value="{{ n }}" {% if n == days_val %}selected{% endif %}>{{ n }}</option>{% endfor %}</select>{% endset -%}
      {%- set time_html %}<input type="text" name="time" value="{{ hhmm(hours_val, minutes_val) }}" inputmode="numeric" pattern="([01]?[0-9]|2[0-3])(:[0-5][0-9])?" placeholder="0:30" title="{{ _('Hours and minutes, like 0:30 or 3:00') }}" size="5">{% endset -%}
```

and the pattern line — a NEW msgid, so Task 8 must translate it:

```jinja
      {{ sentence_slots(_("Remind me {days} day(s) {time} {direction} each {anchor}."), {"days": days_html, "time": time_html, "direction": direction_html, "anchor": anchor_html}) }}
```

Update the three call sites to pass minutes:

```jinja
          {{ sentence_fields(i.offset_days | abs, i.offset_hours | abs, i.offset_minutes | abs,
                             "after" if (i.offset_days > 0 or i.offset_hours > 0 or i.offset_minutes > 0) else "before",
                             i.anchor.value) }}
```

and the two "new rule" call sites become `sentence_fields(3, 0, 0, "before", "closes")`.

- [ ] **Step 6: Check the slot names are accepted**

`sentence_slots` raises `ValueError` on an unknown slot. Confirm the caller passes the four keys above and that nothing else hardcodes `"hours"` as a known slot: `grep -rn "hours" src/app/web/app.py src/app/domain/sentence.py`.

- [ ] **Step 7: Run the tests**

Run: `uv run --isolated pytest -q tests/test_preferences_page.py tests/test_web.py -n0`
Expected: PASS. If a render test asserts the old sentence text, update it — the copy change is intentional.

- [ ] **Step 8: Commit**

```bash
git add src/app/web/routes/preferences.py src/app/web/templates/preferences.html src/app/web/app.py tests/
git commit -m "feat(offsets): the preset editor's second box takes h:mm"
```

---

### Task 5: The concert page — entry, and the misreport it has today

**Files:**
- Modify: `src/app/web/templates/_rules.html:15` (the list line), `:43` (the add-form)
- Modify: `src/app/web/routes/reminders.py:28-47` (`add_rule`)
- Test: `tests/test_concert_page.py`

**Interfaces:**
- Consumes: `parse_hhmm` (Task 3), the `describe_offset` Jinja global (Task 4 step 4).
- Produces: `POST /concerts/{event_id}/rules` now takes `days: int = Form(0, ge=0, le=60)` and `time: str = Form("0:00")` in place of `days_before`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_concert_page.py`, whose `client` is again a synchronous
`TestClient`, authenticated with `login(client)` and seeded with
`seed_concert(client.db, ...)` — read two neighbouring tests before writing
these and match their setup exactly.

```python
async def test_the_rule_list_names_a_sub_day_offset_instead_of_saying_same_day(client):
    """This bug predates minutes: an hours-only rule renders as "Same day"
    today, because the template reads offset_days alone. The mutation this
    must not survive: _rules.html going back to an offset_days-only branch."""
    login(client)
    concert = await seed_concert(client.db)
    async with client.db() as s:
        s.add(ReminderRule(
            user_id=USER, concert_id=concert.id, anchor=Anchor.CLOSES,
            offset_days=0, offset_hours=-3, offset_minutes=0,
        ))
        await s.commit()

    page = client.get(f"/concerts/{concert.event_id}")
    assert "3 hours before" in page.text
    assert "Same day" not in page.text


async def test_adding_a_five_minute_rule_from_the_concert_page(client):
    login(client)
    concert = await seed_concert(client.db)

    r = client.post(f"/concerts/{concert.event_id}/rules", data={
        "anchor": "opens", "days": "0", "time": "0:05",
    })
    assert r.status_code == 200
    assert "5 minutes before" in r.text

    async with client.db() as s:
        rule = (await s.execute(select(ReminderRule))).scalar_one()
    assert (rule.offset_days, rule.offset_hours, rule.offset_minutes) == (0, 0, -5)
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest -q tests/test_concert_page.py -n0`
Expected: FAIL on both — "Same day" is still rendered, and the route rejects `time` as an unexpected field / ignores it.

- [ ] **Step 3: Change the route**

```python
    anchor: Anchor = Form(...),
    days: int = Form(0, ge=0, le=60),
    time: str = Form("0:00"),
):
    concert = await get_concert_by_event_id(session, event_id)
    await ensure_user(session, user.id, user.username)
    try:
        hours, minutes = parse_hhmm(time)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"bad time: {e}") from e
    rule = ReminderRule(
        user_id=user.id, concert_id=concert.id, anchor=anchor,
        offset_days=-days, offset_hours=-hours, offset_minutes=-minutes,
        channel=Channel.DM,
    )
```

- [ ] **Step 4: Change the template**

The list line becomes one call, and the "each {anchor}" clause stays as it is:

```jinja
        {{ describe_offset(r.offset_days, r.offset_hours, r.offset_minutes) }}
```

The add-form gains the same two controls as the preferences macro (days `<select>` 0-60 plus the h:mm box), replacing the bare number input. Keep the existing `{{ _("days before") }}` label OFF the new shape — the phrase is wrong now — and label the row with a single new msgid, e.g. `{{ _("before each") }}` placed to read as a sentence with the anchor select that follows it. Task 8 translates whatever you choose; write the final English here and note it in the commit.

- [ ] **Step 5: Run the tests**

Run: `uv run --isolated pytest -q tests/test_concert_page.py -n0`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/web/routes/reminders.py src/app/web/templates/_rules.html tests/
git commit -m "feat(offsets): sub-day rules on the concert page, and stop calling them Same day"
```

---

### Task 6: Discord — `/remindme` and `/myreminders`

**Files:**
- Modify: `src/app/bot/cogs/reminders.py:125-165` (`/remindme`), `:200-207` (`/myreminders` line building)
- Test: `tests/test_bot_reminders.py`

**Interfaces:**
- Consumes: `describe_offset` (Task 3).

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_reminders.py` already has the whole harness: a local `db`
fixture that monkeypatches `reminders_cog.SessionMaker`, a `FakeInteraction`,
and call helpers shaped like

```python
    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.mydeadlines.callback(cog, interaction, count)
```

Follow that shape exactly — no Discord gateway, no real interaction object.

```python
async def test_remindme_stores_a_sub_hour_offset(db):
    """/remindme 0 days, 30 minutes -> (0, 0, -30), decomposed on the way in."""
    interaction = FakeInteraction()
    concert_id = await _seed_one_concert(db)   # the helper this file already uses
    cog = reminders_cog.Reminders(bot=None)

    await reminders_cog.Reminders.remindme.callback(
        cog, interaction, concert=concert_id,
        anchor=app_commands.Choice(name="closes", value="closes"),
        days_before=0, minutes_before=30,
    )

    async with db() as s:
        rule = (await s.execute(select(ReminderRule))).scalar_one()
    assert (rule.offset_days, rule.offset_hours, rule.offset_minutes) == (0, 0, -30)
    assert "30 minutes before" in interaction.response.sent[-1]


async def test_myreminders_describes_an_hours_rule_as_hours(db):
    """The same misreport the concert page had: abs(offset_days) alone printed
    "same-day" for every sub-day rule."""
    interaction = FakeInteraction()
    async with db() as s:
        s.add(ReminderRule(
            user_id=interaction.user.id, concert_id=None, round_id=1,
            anchor=Anchor.CLOSES, offset_days=0, offset_hours=-3, offset_minutes=0,
        ))
        await s.commit()

    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.myreminders.callback(cog, interaction)

    sent = interaction.response.sent[-1]
    assert "3 hours before" in sent
    assert "same-day" not in sent
```

Check `FakeResponse` for how sent messages are recorded — the attribute may be
named something other than `.sent`; use whatever the file already asserts on.
The anchor argument's real type is whatever `/remindme`'s signature declares
(an `app_commands.Choice`); copy it from the command definition rather than
from this snippet.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest -q tests/test_bot_reminders.py -n0`
Expected: FAIL — unexpected keyword `minutes_before`; `same-day` still in the listing.

- [ ] **Step 3: Add the parameter**

In the `@app_commands.describe(...)` block add:

```python
        minutes_before="Extra minutes before (e.g. 30). Adds to days.",
```

widen the signature with `minutes_before: int = 0`, clamp it defensively (`0 <= minutes_before <= 1439`, answering the same ephemeral error the command already uses for a bad concert), and store it decomposed:

```python
            rule = ReminderRule(
                user_id=interaction.user.id,
                concert_id=target.id,
                anchor=Anchor(anchor.value),
                offset_days=-days_before,          # UX asks 'days before'; storage is signed
                offset_hours=-(minutes_before // 60),
                offset_minutes=-(minutes_before % 60),
            )
```

and the confirmation line becomes:

```python
        when = describe_offset(-days_before, -(minutes_before // 60), -(minutes_before % 60))
```

- [ ] **Step 4: Fix the listing**

```python
            timing = describe_offset(rule.offset_days, rule.offset_hours, rule.offset_minutes)
            lines.append(f"`#{rule.id}` **{scope}** — {timing} {rule.anchor.value}")
```

Delete the now-unused `d` / `direction` locals so ruff stays clean.

- [ ] **Step 5: Run the tests**

Run: `uv run --isolated pytest -q tests/test_bot_reminders.py -n0`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/bot/cogs/reminders.py tests/test_bot_reminders.py
git commit -m "feat(offsets): /remindme takes minutes, /myreminders reads them back"
```

---

### Task 7: The wizard's fine-tune options

**Files:**
- Modify: `src/app/web/routes/welcome.py:47-61` (`OFFSET_OPTIONS`), `:83-110` (`PRESET_TEMPLATES`), `:193-232` (`create_wizard_preset`)
- Modify: `src/app/web/templates/welcome.html:95` (the encoding comment only, if it names "days:hours")
- Test: `tests/test_welcome.py`

**Interfaces:**
- Consumes: `create_preset_from_rules(...)` with 5-tuples (Task 2).

- [ ] **Step 1: Write the failing tests**

`tests/test_welcome.py` has a synchronous `client` and `login_as(client,
FAN_ID, "fan")`. The wizard posts parallel arrays, so the data is a list of
tuples, not a dict.

```python
async def test_the_wizard_can_create_a_thirty_minute_rule(client):
    login_as(client, FAN_ID, "fan")

    r = client.post("/welcome/preset", data=[
        ("offset", "0:0:30"), ("direction", "before"), ("anchor", "opens"),
    ])
    assert r.status_code == 303

    async with client.db() as s:
        item = (await s.execute(select(PresetItem))).scalar_one()
    assert (item.offset_days, item.offset_hours, item.offset_minutes) == (0, 0, -30)


def test_a_tampered_offset_string_is_a_422_not_a_500(client):
    """The closed <select> can never send this; a hand-rolled POST can."""
    login_as(client, FAN_ID, "fan")

    r = client.post("/welcome/preset", data=[
        ("offset", "0:0:banana"), ("direction", "before"), ("anchor", "opens"),
    ])
    assert r.status_code == 422


def test_a_two_part_offset_is_also_refused(client):
    """The old "days:hours" encoding must not silently parse as something else
    once the format widened -- it means the page and the route disagree."""
    login_as(client, FAN_ID, "fan")

    r = client.post("/welcome/preset", data=[
        ("offset", "0:3"), ("direction", "before"), ("anchor", "opens"),
    ])
    assert r.status_code == 422
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest -q tests/test_welcome.py -n0`
Expected: FAIL — the parser reads only two parts, so `"0:0:30"` stores nothing sensible.

- [ ] **Step 3: Widen the option vocabulary**

```python
    {"value": "0:0:0", "label": N_("when"), "moment": True},
    {"value": "0:0:5", "label": N_("5 minutes"), "moment": False},
    {"value": "0:0:30", "label": N_("30 minutes"), "moment": False},
    {"value": "0:1:0", "label": N_("1 hour"), "moment": False},
    {"value": "0:3:0", "label": N_("3 hours"), "moment": False},
    {"value": "0:6:0", "label": N_("6 hours"), "moment": False},
    {"value": "1:0:0", "label": N_("1 day"), "moment": False},
    {"value": "3:0:0", "label": N_("3 days"), "moment": False},
    {"value": "5:0:0", "label": N_("5 days"), "moment": False},
    {"value": "7:0:0", "label": N_("1 week"), "moment": False},
```

`5 minutes` and `30 minutes` are new msgids; the rest keep theirs byte-identical, so their ja/zh translations survive. Only the `value` strings changed on those.

- [ ] **Step 4: Widen the templates and the parser**

`PRESET_TEMPLATES` entries become `[days, hours, minutes, direction, anchor]` — add a `0` in third position to every row, and update the comment above them.

`create_wizard_preset`:

```python
    rules: list[tuple[int, int, int, str, Anchor]] = []
    for off, dir_, anc in zip(offset, direction, anchor, strict=False):
        parts = off.split(":")
        if len(parts) != 3:
            raise HTTPException(status_code=422, detail=f"bad reminder row: {off!r}")
        try:
            rules.append((int(parts[0]), int(parts[1]), int(parts[2]), dir_, Anchor(anc)))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"bad reminder row: {e}") from e
```

Update the docstring paragraph that says each offset arrives as `"days:hours"`.

- [ ] **Step 5: Check the template's JS agrees**

`welcome.html`'s fine-tune script builds and reads these values. Run `grep -n "offset" src/app/web/templates/welcome.html` and make every place that splits or composes the value handle three parts. Render the step and confirm the list still populates with JS off.

- [ ] **Step 6: Run the tests**

Run: `uv run --isolated pytest -q tests/test_welcome.py -n0`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/app/web/routes/welcome.py src/app/web/templates/welcome.html tests/test_welcome.py
git commit -m "feat(offsets): the wizard's fine-tune list gets its sub-hour options back"
```

---

### Task 8: Both catalogues

**Files:**
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `src/app/translations/zh/LC_MESSAGES/messages.po` (confirm the paths with `ls src/app/translations/*/LC_MESSAGES`)
- Test: `tests/test_i18n_catalogues.py`

- [ ] **Step 1: Extract and update**

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

- [ ] **Step 2: Run the catalogue test to get the list of gaps**

Run: `uv run --isolated pytest -q tests/test_i18n_catalogues.py -n0`
Expected: FAIL, naming every untranslated or fuzzy msgid. That list is your worklist.

- [ ] **Step 3: Fill every new msgid in both files, by hand**

The new strings, and what they must read like:

- `"Remind me {days} day(s) {time} {direction} each {anchor}."` — the slot ORDER is the translator's choice and this is the whole point of the pattern. ja: `「{anchor}の{days}日{time}{direction}に通知。」`; zh: `「{anchor}{direction}{days}天{time}提醒我。」`. Keep every placeholder exactly once.
- `"Hours and minutes, like 0:30 or 3:00"` (the box's `title`).
- `"{n} minute"` / `"{n} minutes"`, `"{n} hour"` / `"{n} hours"`, `"{n} day"` / `"{n} days"` — ja and zh have ONE plural form; fill the singular msgstr and leave the plural slot as the catalogue's own convention for those locales (look at an existing plural entry in the file and copy its shape exactly).
- `"{quantity} before"` / `"{quantity} after"` — ja `「{quantity}前」`/`「{quantity}後」`, zh `「{quantity}前」`/`「{quantity}后」`.
- `"5 minutes"`, `"30 minutes"` (wizard options).
- Whatever single label Task 5 chose for the concert-page add-form.

- [ ] **Step 4: Delete the pot and re-run**

```bash
rm messages.pot
uv run --isolated pytest -q tests/test_i18n_catalogues.py tests/test_i18n_ugc.py -n0
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/translations
git commit -m "i18n(offsets): ja and zh for the new offset copy"
```

---

### Task 9: Documentation, WISHLIST, README, and the full gate

**Files:**
- Modify: `CLAUDE.md` (layout list), `docs/architecture.md`, `WISHLIST.md`, `README.md`

- [ ] **Step 1: `docs/architecture.md`**

Add an entry for `src/app/offsets.py` saying what it owns and why it is not in `domain/`, and add to the existing `db/core.py` entry the trap this build found: `apply_preset`'s dedupe tuple must name every offset column, or two items differing only in minutes collapse to one rule with no error.

- [ ] **Step 2: `CLAUDE.md`**

One line in the layout list for `app/offsets.py`, beside `i18n.py`.

- [ ] **Step 3: `WISHLIST.md`**

Move #1 to Shipped, dated 2026-08-19, recording: the owner's three rulings, the "Same day" misreport this closed on two surfaces, the dedupe-tuple trap, and that the wizard now matches the onboarding demo's long-standing `30m` entry. Then the full revision pass CLAUDE.md requires: renumber the entries below by removal, correct any live cross-reference, and state explicitly what moved on merit and what did not.

- [ ] **Step 4: `README.md`**

One line at the end of the "Shipped since Phase 12" list, in the same voice as its neighbours — what a user can now do (reminders down to the minute, typed as h:mm; the wizard's 5- and 30-minute options; rules that describe themselves correctly).

- [ ] **Step 5: The full gate**

Run: `uv run --isolated pytest -q` (foreground, `timeout: 600000`, ~107s)
Run: `uv run --isolated ruff check .`
Expected: both clean. Report the actual test count and duration; never a number you did not see.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/architecture.md WISHLIST.md README.md
git commit -m "docs(offsets): architecture entries, WISHLIST #1 closed, README line"
```
