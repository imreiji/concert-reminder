# Goods Sale Rounds + Item-Requirement Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `Goods sale` round kind (cosmetic, like the other nine), plus a display-only FK from a round to the item-sale round it requires ("最速先行 needs the serial code from this CD sale"), threaded through the editor, the concert page, DM embeds, and the YAML draft round-trip.

**Architecture:** `RoundKind.GOODS_SALE` joins the enum as a tenth cosmetic member (label/emoji/import-heuristics only, zero behavior branches). `Round.required_item_round_id` is a nullable self-FK (`ON DELETE SET NULL`), validated at every write boundary via one shared resolver (target on the same submit, kind in `ITEM_SALE_KINDS`, never self). Same-submit references bind via a `round_key` hidden field mirroring the existing `day_key` mechanism. Display is derived from rounds already loaded — no new relationship, no lazy loads.

**Tech Stack:** Python 3.14, FastAPI + Jinja2, SQLAlchemy async + Alembic (SQLite), pytest-asyncio, pybabel catalogues (ja/zh).

**Spec:** `docs/superpowers/specs/2026-08-02-goods-sale-rounds-design.md`

## Global Constraints

- `uv run pytest -q` MUST pass and `uv run ruff check .` MUST be clean before every commit. Run tests in the FOREGROUND (background runs stall).
- The DB stores aware UTC only; forms enter JST. Never store/compare naive datetimes (invariant 1).
- New translatable strings: write `_("literal")` at render/lookup time, `N_()` in module-level dicts. After adding msgids run `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .` then `uv run pybabel update -i messages.pot -d src/app/translations -l ja` (and `-l zh`), hand-fill the new msgstrs in BOTH `.po` files (match the register/script already used in each file — read neighboring entries first), then delete `messages.pot`. `tests/test_i18n_catalogues.py` fails on any untranslated/fuzzy entry. No EN test may assert a translated string.
- Three locale sources: `get_locale()` inside a web request; `user.language` for scheduler-composed text; explicit param only where the caller must decide. Picking wrong is SILENT.
- After `alembic revision --autogenerate`: review the file; remove any `import app.db.models` line and replace `app.db.models.UTCDateTime()` with `sa.DateTime()` (not expected here — no datetime column); no `drop_constraint` anywhere in it.
- Templates: user-controlled text never reaches inline `on*` handlers; picker-script data uses `| tojson` on raw Python objects (invariant 7). Sentence case in UI copy.
- Editor round cards render ONLY through `_editor_round_card.html` — never hand-roll a card copy.
- The bot layer never imports fastapi; `db/` never imports `discovery`/`scheduler`; `domain/` does no I/O.
- Commit messages end with the Co-Authored-By/Claude-Session trailer already used on this branch.
- Branch: `goods-sale-rounds` (already created from origin/main; spec committed).

## File Structure

- `src/app/domain/types.py` — `RoundKind.GOODS_SALE`, `ITEM_SALE_KINDS`
- `src/app/domain/ingest.py` — goods keywords in `_KIND_KEYWORDS`
- `src/app/db/models.py` — `Round.required_item_round_id`
- `alembic/versions/<new>_round_required_item.py` — the column + FK + index
- `src/app/db/service.py` — `LABEL_BY_ROUND_KIND` entry; `RoundRow` requires/feeds fields + `concert_round_rows` derivation; `DueReminder` requires fields + `due_reminders` batch fetch; export builder writes `requires_label`
- `src/app/web/routes/concerts.py` — `RoundRequiresError`, `resolve_round_requires`, create/edit wiring, edit-form context options
- `src/app/web/routes/imports.py` — import_commit wiring, preview `round_key`/`requires_key` stamping
- `src/app/domain/draft.py` — `ParsedRound.requires_label` / `.round_key` / `.requires_key`
- `src/app/domain/yaml_import.py` — `requires` round key parsing
- `src/app/domain/yaml_export.py` — `YamlRound.requires_label` + emitted `requires` key
- `src/app/web/templates/_editor_round_card.html` — `round_key` hidden + requires `<select>`
- `src/app/web/templates/_requires_select_script.html` — NEW: client half of the select
- `src/app/web/templates/concert_new.html` / `concert_edit.html` / `import_preview.html` — macro args + script include
- `src/app/web/templates/_round_rows.html` — "Requires:" / "Needed for:" display lines
- `src/app/bot/messages.py` — `KIND_EMOJI["goods_sale"]`, embed "Requires" line
- `.claude/skills/add-concert/SKILL.md` + `src/app/web/skill_dist/add-concert/SKILL.md` — kind mapping + `requires:` docs
- `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po` — 7 new msgids
- Tests: `tests/test_round_requires.py` (NEW), plus extensions to `tests/test_yaml_export.py`, `tests/test_yaml_import.py`, `tests/test_bot_reminders.py` (or wherever `build_reminder_message` is tested — find with grep first)

---

### Task 1: Taxonomy — GOODS_SALE kind, ITEM_SALE_KINDS, label, emoji, ingest keywords

**Files:**
- Modify: `src/app/domain/types.py` (RoundKind, ~line 40)
- Modify: `src/app/db/service.py` (`LABEL_BY_ROUND_KIND`, ~line 1566)
- Modify: `src/app/bot/messages.py` (`KIND_EMOJI`, ~line 18)
- Modify: `src/app/domain/ingest.py` (`_KIND_KEYWORDS`, ~line 46)
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `src/app/translations/zh/LC_MESSAGES/messages.po`
- Test: `tests/test_round_requires.py` (new file, first tests)

**Interfaces:**
- Produces: `RoundKind.GOODS_SALE = "goods_sale"`; `ITEM_SALE_KINDS: frozenset[RoundKind]` in `app.domain.types` (imported by Tasks 3–7). Label msgid `"Goods sale"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_round_requires.py`. Copy the standard test-file header/fixture imports from `tests/test_crud.py` (async session fixture with the `PRAGMA foreign_keys=ON` listener — reuse the shared fixture from `conftest.py`, do not write a new engine).

```python
from app.domain.ingest import _guess_kind
from app.domain.types import ITEM_SALE_KINDS, RoundKind


def test_goods_sale_is_a_round_kind():
    assert RoundKind.GOODS_SALE.value == "goods_sale"


def test_item_sale_kinds_are_the_two_item_kinds():
    assert ITEM_SALE_KINDS == {RoundKind.ELIGIBILITY_ITEM_SALE, RoundKind.GOODS_SALE}


def test_guess_kind_maps_goods_keywords():
    assert _guess_kind("グッズ販売") is RoundKind.GOODS_SALE
    assert _guess_kind("Tour Goods Pre-order") is RoundKind.GOODS_SALE
    assert _guess_kind("会場物販") is RoundKind.GOODS_SALE
    # The serial-code sale stays what it was:
    assert _guess_kind("シリアル対象CD発売") is not RoundKind.GOODS_SALE


def test_label_and_emoji_cover_every_kind():
    from app.bot.messages import KIND_EMOJI
    from app.db.service import LABEL_BY_ROUND_KIND

    for kind in RoundKind:
        assert kind in LABEL_BY_ROUND_KIND
        assert kind.value in KIND_EMOJI
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (`GOODS_SALE` attribute error / `ITEM_SALE_KINDS` import error).

- [ ] **Step 3: Implement**

`src/app/domain/types.py` — after `TOUR_PACKAGE` and before `UPGRADE`:

```python
    # A merch/goods pre-order window (グッズ販売 / 物販). Cosmetic like the
    # nine above it -- only UPGRADE carries behavior -- but it is one of the
    # two kinds a round's required_item_round_id may point at (an item whose
    # purchase is what qualifies you for a lottery round; 抽選券付き goods
    # exist, which is why this kind joins ELIGIBILITY_ITEM_SALE there).
    GOODS_SALE = "goods_sale"
```

Below `RoundKind` (before `ConcertKind`), the shared table — ONE table for the same reason `ALLOWED_PARENT_KINDS` is one:

```python
# The kinds a round's `required_item_round_id` may target: the serial-code
# CD/BD sale and the goods sale (抽選券付き goods exist). ONE table, here in
# the pure vocabulary, because three write paths validate it (create, edit,
# import commit) and the requires <select>'s client script mirrors it -- two
# copies drifting is how a file stops being able to express a link at all.
ITEM_SALE_KINDS: frozenset["RoundKind"] = frozenset({
    RoundKind.ELIGIBILITY_ITEM_SALE, RoundKind.GOODS_SALE,
})
```

(Define it AFTER the class body; use the plain name `RoundKind` — no quotes needed there.)

`src/app/db/service.py` `LABEL_BY_ROUND_KIND` — add beside `TOUR_PACKAGE`:

```python
    RoundKind.GOODS_SALE: N_("Goods sale"),
```

`src/app/bot/messages.py` `KIND_EMOJI` — add beside `"tour_package"`:

```python
    "goods_sale": "🛍️",
```

`src/app/domain/ingest.py` `_KIND_KEYWORDS` — insert after the `("overseas", RoundKind.TOUR_PACKAGE)` entry and BEFORE `("result", ...)` (first match wins; a goods label mentioning 抽選 must land on goods, so goods keywords must precede the lottery ones):

```python
    ("goods", RoundKind.GOODS_SALE),
    ("グッズ", RoundKind.GOODS_SALE),
    ("物販", RoundKind.GOODS_SALE),
```

- [ ] **Step 4: Update both catalogues**

Run the pybabel extract/update commands from Global Constraints. Fill the ONE new msgid `"Goods sale"` in both `.po` files (suggested: ja `グッズ販売`; zh — read neighboring round-kind entries in the zh file first and match its script, e.g. `周边贩售` if simplified). Delete `messages.pot`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_i18n_catalogues.py tests/test_ingest.py -q` (if `tests/test_ingest.py` doesn't exist, grep tests/ for `_guess_kind`/`parse_ramen_event` and run that file). Then `uv run pytest -q` — a pre-existing test may pin the RoundKind member count or iterate kinds; if one fails, update it to include the new member (that pin existing is expected, not a regression).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: goods_sale round kind (label, emoji, ingest keywords, ITEM_SALE_KINDS)"
```

---

### Task 2: Model column + migration

**Files:**
- Modify: `src/app/db/models.py` (`Round`, ~line 413, after `applies_to`)
- Create: `alembic/versions/<autogen>_round_required_item.py`
- Test: `tests/test_round_requires.py`

**Interfaces:**
- Produces: `Round.required_item_round_id: int | None` (plain FK column, NO relationship — display derives from rounds already loaded, so a relationship would only be a lazy-load trap).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_round_requires.py` (model the fixture usage on the service-layer tests in `tests/test_crud.py` — async in-memory engine from conftest, FK pragma on):

```python
async def test_required_item_round_set_null_on_target_delete(session):
    # Build a concert with an item-sale round and a lottery round that
    # requires it, straight through the models (write-boundary validation is
    # a route concern, Tasks 3-5).
    from app.db.models import Concert, Round

    concert = Concert(title="t", event_id="t-1", created_by=1)
    session.add(concert)
    await session.flush()
    item = Round(concert_id=concert.id, kind=RoundKind.GOODS_SALE, label="グッズ")
    session.add(item)
    await session.flush()
    lottery = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        required_item_round_id=item.id,
    )
    session.add(lottery)
    await session.flush()

    await session.delete(item)
    await session.flush()
    await session.refresh(lottery)
    assert lottery.required_item_round_id is None
```

Adjust the `Concert(...)` constructor kwargs to whatever `tests/test_crud.py` actually uses to create a bare concert (there may be a helper — reuse it). If `created_by` needs a real user row, create one the way neighboring tests do.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (`required_item_round_id` is an invalid keyword argument).

- [ ] **Step 3: Add the column**

`src/app/db/models.py`, in `Round` after `applies_to`:

```python
    # The item-sale round (ELIGIBILITY_ITEM_SALE or GOODS_SALE -- see
    # ITEM_SALE_KINDS) whose item this round requires: "you may enter 最速先行
    # only with the serial code from this CD sale". Display-only for now; a
    # per-user "I bought it" capture would hang off this FK later. SET NULL,
    # never CASCADE, for ConcertDay.venue_tag_id's reason: deleting the item
    # round degrades this round to "no requirement", it must not delete it.
    # Deliberately NO relationship: every reader already has the concert's
    # rounds loaded, and a lazy load during async rendering is a
    # MissingGreenlet 500 this project has shipped before.
    required_item_round_id: Mapped[int | None] = mapped_column(
        ForeignKey("rounds.id", ondelete="SET NULL"), index=True
    )
```

- [ ] **Step 4: Generate and review the migration**

Run: `uv run alembic revision --autogenerate -m "round required item"`
Review the generated file: it should be a `batch_alter_table('rounds')` with `add_column`, `create_foreign_key` (named via the convention, `ondelete='SET NULL'`) and `create_index` — the same shape as `alembic/versions/bb9780f0ad82_character_tags.py`. In `downgrade()`, remove any `drop_constraint`/`drop_index` and keep only `drop_column` (batch mode recreates the table without both — see bb9780f0ad82's downgrade comment; copy that comment). No `UTCDateTime` should appear. Then: `uv run alembic upgrade head`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_round_requires.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: Round.required_item_round_id (nullable self-FK, SET NULL)"
```

---

### Task 3: The resolver + create_concert wiring

**Files:**
- Modify: `src/app/web/routes/concerts.py` (resolver near `parse_round_qualifiers` ~line 356; `create_concert` signature ~line 694 and round loop ~lines 809–853)
- Test: `tests/test_round_requires.py`

**Interfaces:**
- Consumes: `ITEM_SALE_KINDS` (Task 1), `Round.required_item_round_id` (Task 2).
- Produces: `RoundRequiresError(Exception)` and `resolve_round_requires(token: str, key_to_round_id: dict[str, int], kinds_by_id: dict[int, RoundKind], self_id: int) -> int | None` in `app.web.routes.concerts` — Tasks 4 and 5 import/reuse both. Form contract: parallel arrays `round_key` (one client key per round row; a saved round's key is its own id) and `round_requires` (one token per row: a round id or a `round_key`, or empty).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_round_requires.py`. Two levels: the resolver as a unit, and `create_concert` through the HTTP client. For the HTTP tests, copy the logged-in-editor client fixture and the `POST /concerts/new` form-data shape from the create test around `tests/test_crud.py:843` (day_key/day_label/... plus round_* parallel arrays), adding the two new arrays.

```python
import pytest
from fastapi import HTTPException

from app.web.routes.concerts import RoundRequiresError, resolve_round_requires


def test_resolver_resolves_key_and_id_tokens():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    keys = {"r0": 1}
    assert resolve_round_requires("r0", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("1", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("", keys, kinds, self_id=2) is None


def test_resolver_rejects_missing_wrong_kind_and_self():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("99", {}, kinds, self_id=2)   # not on this concert
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("2", {}, kinds, self_id=3)    # target not an item kind
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("1", {}, kinds, self_id=1)    # itself


async def test_create_concert_binds_requires_by_round_key(client_editor, session):
    # Two rounds in one submit: row 0 is the goods sale (key g1), row 1
    # requires it by key. Assert the saved lottery round points at the saved
    # goods round's real id.
    ...


async def test_create_concert_422_on_wrong_kind_target(client_editor):
    # round_requires names the OTHER lottery round -> 422, nothing persisted.
    ...
```

Fill the two `...` bodies concretely from the test_crud form shape: include `"round_key": ["g1", "x2"]` and `"round_requires": ["", "g1"]`, with `round_kind: ["goods_sale", "lottery_round"]`. After the 303, load the concert's rounds and assert `lottery.required_item_round_id == goods.id`. For the 422 test assert `resp.status_code == 422` and the concert does not exist.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (`RoundRequiresError` import error).

- [ ] **Step 3: Implement the resolver**

In `src/app/web/routes/concerts.py`, directly after `parse_round_qualifiers`:

```python
class RoundRequiresError(Exception):
    """A round_requires token naming a missing round, a non-item-sale round,
    or the round itself. The message finishes the sentence "Round X's
    required-item link ..." -- routes wrap it into a 422."""


def resolve_round_requires(
    token: str,
    key_to_round_id: dict[str, int],
    kinds_by_id: dict[int, RoundKind],
    self_id: int,
) -> int | None:
    """One round row's "Requires item from" selection: ONE token (a Round id
    for a saved round, a `round_key` for one created in this same submit),
    resolved to the id of a round surviving THIS submit.

    Same one-field-per-row encoding as round_legs/round_qualifiers, same
    post-flush resolution as key_to_day_id -- but STRICT where those are
    tolerant: an unresolvable selection raises rather than silently dropping,
    because this is a single deliberate <select> choice, not a chip set, and
    "your link didn't save" with no error is the silent failure the spec
    forbids. `kinds_by_id` holds every surviving round's SUBMITTED kind, so a
    target re-kinded off the item kinds in the same save is caught, and a
    cross-concert id fails the same membership test for free. Empty -> None.
    """
    token = token.strip()
    if not token:
        return None
    if token.isdigit() and int(token) in kinds_by_id:
        rid = int(token)
    elif token in key_to_round_id:
        rid = key_to_round_id[token]
    else:
        raise RoundRequiresError("names a round that is not on this concert")
    if rid == self_id:
        raise RoundRequiresError("a round cannot require itself")
    if kinds_by_id[rid] not in ITEM_SALE_KINDS:
        raise RoundRequiresError(
            "points at a round that is not an item or goods sale"
        )
    return rid
```

Import `ITEM_SALE_KINDS` beside the existing `RoundKind` import from `app.domain.types`.

- [ ] **Step 4: Wire create_concert**

In the `create_concert` signature after `round_qualifiers`:

```python
    round_key: list[str] = Form(default=[]),
    round_requires: list[str] = Form(default=[]),
```

Before the round loop, pad exactly as `round_legs` is padded (whole-array omission only):

```python
    if not round_key:
        round_key = [""] * len(round_label)
    if not round_requires:
        round_requires = [""] * len(round_label)
```

Extend the round loop's zip with `round_key, round_requires` (keep `strict=True`), rename `qual_jobs` to `round_jobs: list[tuple[Round, RoundKind, str, str, str]]` carrying `(round_, kind_, quals, key, req)`, and inside the loop append the two new values. After the existing `await session.flush()` that gives rounds ids, build the key map (first row claiming a key keeps it — same `setdefault` rule as `key_to_day_id`; a saved round has no key here since everything is new):

```python
    key_to_round_id: dict[str, int] = {}
    for round_, _k, _q, key, _r in round_jobs:
        if key.strip():
            key_to_round_id.setdefault(key.strip(), round_.id)
    kinds_by_id = {round_.id: kind_ for round_, kind_, _q, _k, _r in round_jobs}
    for round_, kind_, quals, _key, req in round_jobs:
        try:
            round_.required_item_round_id = resolve_round_requires(
                req, key_to_round_id, kinds_by_id, round_.id
            )
        except RoundRequiresError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Round {round_.label!r}: required-item link {exc}",
            ) from exc
        if kind_ is RoundKind.UPGRADE:
            ...  # existing qualifier loop body, unchanged, folded into this loop
```

(Keep the existing qualifier logic byte-identical — only the tuple shape and loop header change.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_crud.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: resolve_round_requires + create_concert required-item wiring"
```

---

### Task 4: edit_concert wiring (with the preserve-on-omit rule)

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`edit_concert` signature ~line 1332, round loop ~lines 1526–1623; `edit_concert_form` context)
- Modify: `src/app/web/templates/concert_edit.html` (~line 69, pass new macro args — the macro itself changes in Task 6; pass args only if Task 6 is done, otherwise add them in Task 6. To keep tasks independent: this task changes ONLY the route; the template wiring for edit lands in Task 6.)
- Test: `tests/test_round_requires.py`

**Interfaces:**
- Consumes: `resolve_round_requires`, `RoundRequiresError` (Task 3).
- Produces: `edit_concert` accepts `round_key`/`round_requires`; whole-array omission preserves each existing round's stored link (re-validated leniently: a preserved link whose target was deleted or re-kinded this submit drops to None silently — the submitter never sent it, so there is nothing to 422 about). Also produces `requires_options: list[tuple[str, str]]` in `edit_concert_form`'s template context (value=str(round id), label=ja label, item-kind rounds only) for Task 6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_round_requires.py`, modeled on test_crud's edit-page POST shape (`POST /concerts/{event_id}/edit` with `round_id` parallel array):

```python
async def test_edit_preserves_requires_when_field_omitted(client_editor, session):
    # Seed via create (Task 3 shape). Then POST an edit whose form omits
    # round_key/round_requires entirely (an old browser). Assert the link
    # survives.
    ...

async def test_edit_clears_requires_on_empty_value(client_editor, session):
    # POST an edit with round_requires=["", ""] -> link cleared.
    ...

async def test_edit_drops_preserved_link_when_target_deleted(client_editor, session):
    # Omit round_requires AND drop the item round's row from the submit
    # (delete it). The preserved link resolves against surviving rounds,
    # fails, and drops to None -- 200/303, never a 422 for a value the
    # submitter never sent.
    ...

async def test_edit_422_when_posted_target_rekinded(client_editor, session):
    # Explicitly post round_requires pointing at a round whose kind this same
    # submit changes to lottery_round -> 422.
    ...
```

Fill each `...` concretely: existing rounds post `round_id=[str(id)]` and their requires token is the target's id as a string.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: the new tests FAIL (fields not accepted / link wiped on every edit).

- [ ] **Step 3: Implement**

Signature: same two `Form(default=[])` fields as Task 3. Follow `round_qualifiers`' exact omission pattern (~line 1542):

```python
    requires_omitted = not round_requires
    if requires_omitted:
        round_requires = [""] * len(round_label)
    if not round_key:
        round_key = [""] * len(round_label)
```

Extend the round loop zip with the two arrays; carry `(round_, kind_, quals, key, req, preserved)` in `round_jobs` where:

```python
        preserved = requires_omitted and existing is not None
        if preserved:
            req = str(existing.required_item_round_id or "")
```

(Read `existing.required_item_round_id` BEFORE `apply_round_fields` mutates anything — it doesn't touch this column, but read it at the top of the row body for clarity.)

After the flush at ~line 1603, before/alongside the qualifier reconciliation:

```python
    key_to_round_id: dict[str, int] = {}
    for round_, _k, _q, key, _r, _p in round_jobs:
        if key.strip():
            key_to_round_id.setdefault(key.strip(), round_.id)
    kinds_by_id = {round_.id: kind_ for round_, kind_, _q, _k, _r, _p in round_jobs}
    for round_, kind_, quals, _key, req, preserved in round_jobs:
        try:
            round_.required_item_round_id = resolve_round_requires(
                req, key_to_round_id, kinds_by_id, round_.id
            )
        except RoundRequiresError as exc:
            if preserved:
                # The submitter never sent this value -- the target was
                # deleted or re-kinded by this same save. Preserving must not
                # preserve a dangling reference (parse_round_legs' rule), and
                # there is nobody to 422 at.
                round_.required_item_round_id = None
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Round {round_.label!r}: required-item link {exc}",
                ) from exc
```

In `edit_concert_form` (the GET), add to the template context:

```python
        "requires_options": [
            (str(r.id), r.label)
            for r in concert.rounds
            if r.kind in ITEM_SALE_KINDS
        ],
```

(`concert.rounds` is already eager-loaded there — verify by reading the function; if it isn't, load rounds the way `rounds_with_chips` already does.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_crud.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: edit_concert required-item wiring with preserve-on-omit"
```

---

### Task 5: import_commit wiring

**Files:**
- Modify: `src/app/web/routes/imports.py` (`import_commit` signature ~line 712, round loop ~lines 891–919)
- Test: `tests/test_round_requires.py`

**Interfaces:**
- Consumes: `resolve_round_requires`, `RoundRequiresError` (import them beside `build_round`'s existing import from `.concerts`).
- Produces: `import_commit` accepts `round_key`/`round_requires` with create_concert's exact semantics (all rounds are new; keys are the preview's `r0`/`r1`… scheme from Task 7, but any string works).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_round_requires.py`, modeled on an existing `import_commit` POST in `tests/test_imports.py` or `tests/test_crud.py` (find one with `client.post` to `/concerts/import` and copy its minimal form contract):

```python
async def test_import_commit_binds_requires_by_round_key(client_editor, session):
    # Same two-round shape as the create test: round_key=["r0","r1"],
    # round_requires=["","r0"], kinds goods_sale + lottery_round.
    # Assert the committed lottery round points at the goods round.
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (fields ignored, FK stays None).

- [ ] **Step 3: Implement**

Mirror Task 3 exactly: two `Form(default=[])` fields, whole-array-omission padding beside the existing `round_legs` padding (~line 895), zip extension (`strict=True` — note this route end-pads `round_label_en`/`_zh`/`round_notes`; the two NEW arrays follow `round_legs`' rule instead, omit-entirely-or-exact), collect `round_jobs`, and after the `await session.flush()` at ~line 921 run the same key-map + resolve loop as create_concert (422 on `RoundRequiresError` — an import commit is a create). `build_round` is unchanged; assignment happens post-flush on the instances, so keep references to the built rounds instead of `session.add(build_round(...))` inline — bind to a local first.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_imports.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: import_commit required-item wiring"
```

---

### Task 6: Editor UI — round card select + client script

**Files:**
- Modify: `src/app/web/templates/_editor_round_card.html`
- Create: `src/app/web/templates/_requires_select_script.html`
- Modify: `src/app/web/templates/concert_new.html` (template block ~line 135, script includes at the bottom — put the include beside `_qualifier_chips_script.html`'s)
- Modify: `src/app/web/templates/concert_edit.html` (call site ~line 69: pass `round_key=r.id`, `requires_value=r.required_item_round_id or ''`; template block ~line 208; script include)
- Modify: `src/app/web/templates/import_preview.html` (call site ~line 116: pass `round_key=r.round_key`, `requires_value=r.requires_key` — those fields exist after Task 7; pass `round_key=''`/`requires_value=''` for now and Task 7 flips them; template block ~line 226; script include)
- Modify: both `.po` catalogues
- Test: `tests/test_round_requires.py` (render tests)

**Interfaces:**
- Consumes: `requires_options` context (Task 4 provides it on the edit page; concert_new and import_preview pass none — the script builds options client-side there until Task 7 adds preview server options).
- Produces: macro args `round_key=""`, `requires_value=""`, `requires_options=none` on `round_card`; form fields `round_key` + `round_requires` emitted by every card. Msgids `"Requires item from"`, `"No item needed"`.

- [ ] **Step 1: Write the failing render tests**

Every page must have a logged-in GET render test already; extend rather than duplicate. Append to `tests/test_round_requires.py`:

```python
async def test_edit_page_renders_requires_select(client_editor, session):
    # Seed a concert with a goods round + lottery round linked (create POST
    # from Task 3). GET the edit page; assert 200 and that the HTML contains
    # name="round_requires" and the goods round's label inside an <option>
    # marked selected.
    ...

async def test_new_page_renders_requires_field(client_editor):
    # GET /concerts/new -> 200, contains name="round_requires" (empty select,
    # hidden box) and name="round_key".
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (no such markup).

- [ ] **Step 3: Extend the macro**

`_editor_round_card.html`: add `round_key=""`, `requires_value=""`, `requires_options=none` to the macro signature. Beside the `round_id` hidden input:

```jinja
  <input type="hidden" name="round_key" value="{{ round_key }}">
```

After the `{% if with_qualifiers %}...{% endif %}` block:

```jinja
  {#- The "requires item" select: one target round (an ELIGIBILITY_ITEM_SALE
      or GOODS_SALE round on this concert) or nothing. Value is the target's
      id (saved round) or round_key (same-submit round); resolve_round_requires
      is the server half. Hidden (never removed -- a hidden div still submits
      its fields) while no item-kind round exists; _requires_select_script.html
      keeps options and visibility current as rows change. -#}
  {% set _ropts = requires_options or [] %}
  <div class="redit-requires" data-requires-box{% if not _ropts and not requires_value %} hidden{% endif %}>
    <label class="dim tiny">{{ _("Requires item from") }}
      <select name="round_requires" data-none-label="{{ _('No item needed') }}">
        <option value="">{{ _("No item needed") }}</option>
        {% for val, lbl in _ropts %}{% if (val | string) != (round_id | string) %}
        <option value="{{ val }}"{% if (val | string) == (requires_value | string) %} selected{% endif %}>{{ lbl }}</option>
        {% endif %}{% endfor %}
      </select>
    </label>
  </div>
```

- [ ] **Step 4: Write the client script**

Create `src/app/web/templates/_requires_select_script.html`:

```html
{#- The requires select's client half: keep every card's option list equal to
    the CURRENT set of item-kind rounds (kind in eligibility_item_sale /
    goods_sale), minus the card itself, as rows are added, removed, re-kinded
    and re-labelled. Assigns a round_key to any row that lacks one (template
    clones), so a brand-new item round is referenceable in the same submit --
    the round twin of the leg chips' day_key. Delegated listeners only, never
    inline on* (round labels are user text -- invariant 7). Mirrors
    ITEM_SALE_KINDS (domain/types.py); change both together. -#}
<script>
  (function () {
    const rounds = document.getElementById("round-rows");
    if (!rounds) return;
    const ITEM_KINDS = ["eligibility_item_sale", "goods_sale"];
    let seq = 0;

    function keyOf(row) {
      const keyInput = row.querySelector('input[name="round_key"]');
      if (!keyInput) return null;
      if (!keyInput.value) {
        const id = row.querySelector('input[name="round_id"]')?.value;
        keyInput.value = id || "nk" + (++seq);
      }
      return keyInput.value;
    }

    function rebuild() {
      const all = Array.from(rounds.querySelectorAll(".redit"));
      const items = all
        .map((row) => ({
          key: keyOf(row),
          kind: row.querySelector('select[name="round_kind"]')?.value,
          label: row.querySelector('input[name="round_label"]')?.value.trim(),
        }))
        .filter((it) => it.key && ITEM_KINDS.includes(it.kind));
      for (const row of all) {
        const box = row.querySelector("[data-requires-box]");
        const select = row.querySelector('select[name="round_requires"]');
        if (!box || !select) continue;
        const own = keyOf(row);
        const current = select.value;
        const options = items.filter((it) => it.key !== own);
        box.hidden = options.length === 0 && !current;
        select.textContent = "";
        const none = document.createElement("option");
        none.value = "";
        none.textContent = select.dataset.noneLabel || "";
        select.appendChild(none);
        for (const it of options) {
          const opt = document.createElement("option");
          opt.value = it.key;
          opt.textContent = it.label || it.key;
          select.appendChild(opt);
        }
        select.value = options.some((it) => it.key === current) ? current : "";
      }
    }

    rounds.addEventListener("change", (e) => {
      if (e.target.closest('select[name="round_kind"]')) rebuild();
    });
    rounds.addEventListener("focusout", (e) => {
      if (e.target.closest('input[name="round_label"]')) rebuild();
    });
    new MutationObserver(rebuild).observe(rounds, { childList: true });
    rebuild();
  })();
</script>
```

One subtlety the first `rebuild()` must survive: on the edit page the server-rendered selected option's value is a round ID, `keyOf` for saved rows resolves to that same id, so the selection is preserved through the rebuild. Verify by hand-tracing before moving on.

- [ ] **Step 5: Wire the three surfaces**

- `concert_edit.html` call site: add `round_key=r.id, requires_value=r.required_item_round_id or '', requires_options=requires_options` to the `round_card(...)` call; template block at ~208 stays `{{ round_card() }}`. Include `{% include "_requires_select_script.html" %}` beside the existing `_qualifier_chips_script.html` include (grep for it).
- `concert_new.html`: template block unchanged (`{{ round_card() }}` — args default); add the script include.
- `import_preview.html`: add the script include; call-site args come in Task 7.

- [ ] **Step 6: Catalogues**

pybabel extract/update; fill `"Requires item from"` (ja suggestion: `応募に必要な商品`; zh: match file register, e.g. `报名所需商品`) and `"No item needed"` (ja: `商品の購入は不要`; zh: `无需购买商品`) in both files; delete the pot.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_i18n_catalogues.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: requires-item select on the editor round cards"
```

---

### Task 7: Draft vocabulary round-trip (export, parse, preview)

**Files:**
- Modify: `src/app/domain/draft.py` (`ParsedRound`)
- Modify: `src/app/domain/yaml_import.py` (`_ROUND_KEYS`, round loop)
- Modify: `src/app/domain/yaml_export.py` (`YamlRound`, `concert_to_yaml` rounds block)
- Modify: `src/app/db/service.py` (export builder, ~line 4286)
- Modify: `src/app/web/routes/imports.py` (`_draft_preview_response`, ~line 373)
- Modify: `src/app/web/templates/import_preview.html` (call site ~line 116)
- Test: `tests/test_yaml_import.py`, `tests/test_yaml_export.py`, `tests/test_round_requires.py`

**Interfaces:**
- Consumes: import_commit's `round_key`/`round_requires` contract (Task 5), macro args (Task 6).
- Produces: draft round key `requires:` (another round in the same draft, by ja label). `ParsedRound.requires_label: str | None = None` (parser-filled), `ParsedRound.round_key: str = ""` and `ParsedRound.requires_key: str = ""` (route-resolved, like `leg_keys`). `YamlRound.requires_label: str | None = None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_yaml_import.py` (match the file's existing test style):

```python
def test_round_requires_parses():
    parsed = parse_draft(
        "title: t\n"
        "rounds:\n"
        "  - label: グッズ販売\n"
        "    kind: goods_sale\n"
        "  - label: 最速先行\n"
        "    kind: lottery_round\n"
        "    requires: グッズ販売\n"
    )
    assert parsed.rounds[1].requires_label == "グッズ販売"
    assert not [w for w in parsed.warnings if "unknown key" in w]
```

In `tests/test_yaml_export.py`: extend an existing round-trip test (or add one) asserting a `YamlRound(requires_label="グッズ販売", ...)` emits a `requires: グッズ販売` line and that `requires_label=None` emits `requires: null` or omits it — match how `label_en: None` currently behaves (it emits null; keep that consistent, do NOT special-case).

In `tests/test_round_requires.py`:

```python
async def test_draft_preview_resolves_requires_to_keys(client_editor):
    # POST the two-round draft above to /concerts/import/draft; assert the
    # preview HTML carries round_key values r0/r1 and the second round's
    # round_requires select has r0 selected.
    ...

async def test_draft_preview_warns_on_unmatched_requires(client_editor):
    # requires: naming a label no round has -> a warning in the strip, link
    # dropped, page still 200.
    ...

async def test_export_round_trip_keeps_requires(client_editor, session):
    # Create a linked concert (Task 3 shape), fetch its YAML export (find the
    # export route in test_yaml_export.py's usage), re-parse with parse_draft,
    # assert requires_label round-trips.
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yaml_import.py tests/test_yaml_export.py tests/test_round_requires.py -q`
Expected: new tests FAIL (`unknown key 'requires'` warning; missing fields).

- [ ] **Step 3: Implement domain halves (both at once — the tags_yaml rule)**

`draft.py` `ParsedRound` — after `applies_to_labels`:

```python
    # Another round IN THIS DRAFT whose item this round requires, by ja label
    # (the same way applies_to names legs). Parser-filled; the two keys below
    # are route-resolved like leg_keys.
    requires_label: str | None = None
    round_key: str = ""                                 # route-resolved
    requires_key: str = ""                              # route-resolved
```

`yaml_import.py`: add `"requires"` to `_ROUND_KEYS`; in the round loop add:

```python
            requires_label=_text(raw.get("requires"), f"{where} requires", warnings),
```

`yaml_export.py`: `YamlRound` gains `requires_label: str | None = None`; the rounds block in `concert_to_yaml` gains `"requires": r.requires_label,` after `"applies_to"`.

`db/service.py` export builder (~4286): before the `yaml_rounds` comprehension add `round_labels_by_id = {r.id: r.label for r in concert.rounds}`, and inside `YamlRound(...)` add:

```python
            requires_label=(
                round_labels_by_id.get(r.required_item_round_id)
                if r.required_item_round_id else None
            ),
```

- [ ] **Step 4: Implement preview stamping**

In `_draft_preview_response` (imports.py), after the leg-key block (~line 388):

```python
    # requires -> the preview's round_key scheme ("r0", "r1", ...), the same
    # label-claiming rule as legs: first round with a duplicate label keeps it.
    round_label_to_key: dict[str, str] = {}
    for i, r in enumerate(parsed.rounds):
        r.round_key = f"r{i}"
        round_label_to_key.setdefault(r.label.strip(), f"r{i}")
    for r in parsed.rounds:
        if not r.requires_label:
            continue
        key = round_label_to_key.get(r.requires_label.strip())
        if key is None or key == r.round_key:
            parsed.warnings.append(
                f"round {r.label!r}: no other round labelled "
                f"{r.requires_label!r} -- that item link was dropped, "
                "pick it by hand"
            )
        else:
            r.requires_key = key
```

`import_preview.html` call site: add to the loop's `round_card(...)` call:

```jinja
        round_key=r.round_key, requires_value=r.requires_key,
        requires_options=requires_options,
```

and pass from `_draft_preview_response`'s context (also add `"requires_options"` to the OTHER `import_preview.html` render in this file — the ramen-URL path at ~line 300–320 — as `[]`, so the template never sees an undefined; grep for every `import_preview.html` TemplateResponse):

```python
            "requires_options": [
                (r.round_key, r.label)
                for r in parsed.rounds
                if r.kind in ITEM_SALE_KINDS
            ],
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_yaml_import.py tests/test_yaml_export.py tests/test_round_requires.py tests/test_imports.py tests/test_draft_import.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: requires link rides the draft vocabulary and import preview"
```

---

### Task 8: Concert page display

**Files:**
- Modify: `src/app/db/service.py` (`RoundRow` ~line 3018; `concert_round_rows` ~line 3200)
- Modify: `src/app/web/templates/_round_rows.html` (`round_row` macro, `.dts` span ~line 101)
- Modify: both `.po` catalogues
- Test: `tests/test_round_requires.py`

**Interfaces:**
- Consumes: `Round.required_item_round_id`; `label_by_id` already built in `concert_round_rows` (~line 3208).
- Produces: `RoundRow.requires_label: str | None = None`, `RoundRow.requires_closes_at_utc: datetime | None = None` (set only while the item sale is still open), `RoundRow.needed_for_labels: tuple[str, ...] = ()`. Msgids `"Requires: %(name)s"`, `"sale ends"`, `"Needed for: %(names)s"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_round_requires.py` (service-level, calling `concert_round_rows` the way existing tests in `tests/` do — grep for `concert_round_rows(` in tests and copy the setup):

```python
async def test_round_rows_carry_requires_and_needed_for(session):
    # Concert: goods round (closes in the future) + lottery round requiring
    # it. Assert on the lottery row: requires_label == goods label,
    # requires_closes_at_utc == goods.closes_at_utc. On the goods row:
    # needed_for_labels == (lottery label,).
    ...

async def test_requires_close_time_hidden_once_sale_over(session):
    # Same shape, goods round closed in the past: requires_label still set,
    # requires_closes_at_utc is None.
    ...

async def test_concert_page_renders_requires_line(client_editor, session):
    # GET /concerts/{event_id}: 200, body contains "Requires:" and the goods
    # label (EN locale -> byte-identical English, fine to assert).
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q`
Expected: FAIL (no such fields).

- [ ] **Step 3: Implement the service half**

`RoundRow` — after `qualifier_labels`:

```python
    # The item-sale round this round requires (display only): its
    # viewer-locale label, and its close time WHILE that sale is still open
    # (the actionable half -- "you still need to buy this, sale ends 6/15";
    # a closed sale's time is history and is dropped here, not in the
    # template, because round timing is not presentation).
    requires_label: str | None = None
    requires_closes_at_utc: datetime | None = None
    # The reverse line on an item-sale round: the rounds that require it.
    needed_for_labels: tuple[str, ...] = ()
```

In `concert_round_rows`, after `label_by_id` (~line 3208):

```python
    rounds_by_id = {r.id: r for r in rounds}
    # round id -> labels of the rounds that require its item, insertion order.
    needed_for: dict[int, list[str]] = {}
    for r in rounds:
        if r.required_item_round_id in rounds_by_id:
            needed_for.setdefault(r.required_item_round_id, []).append(
                label_by_id[r.id]
            )
```

Then find the `RoundRow(...)` construction (read the rest of the function first — there may be more than one construction site; every one gets the same three kwargs, so extract a small helper if that reads better) and pass:

```python
            requires_label=(
                label_by_id[target.id]
                if (target := rounds_by_id.get(r.required_item_round_id)) else None
            ),
            requires_closes_at_utc=(
                target.closes_at_utc
                if target and target.closes_at_utc and target.closes_at_utc > now
                else None
            ),
            needed_for_labels=tuple(needed_for.get(r.id, ())),
```

(Mind the walrus scoping — compute `target = rounds_by_id.get(r.required_item_round_id) if r.required_item_round_id else None` as a plain statement above the constructor instead if the site is a long kwargs call.)

- [ ] **Step 4: Implement the template half**

`_round_rows.html`, inside `round_row`'s `.dts` span, directly after the `{% if r.notes %}` line:

```jinja
    {% if row.requires_label %}<span class="more">🛍️ {% trans name=row.requires_label %}Requires: {{ name }}{% endtrans %}{% if row.requires_closes_at_utc %}{% set ql = dual_lines(row.requires_closes_at_utc, tz) %} — {{ _("sale ends") }} {{ ql[0] }} · {{ ql[1] }}{% endif %}</span>{% endif %}
    {% if row.needed_for_labels %}<span class="more">{% trans names=row.needed_for_labels | join(", ") %}Needed for: {{ names }}{% endtrans %}</span>{% endif %}
```

(Labels are user text reaching the page as escaped text content — invariant 7 satisfied by construction; never move them into an attribute or handler.)

- [ ] **Step 5: Catalogues**

pybabel extract/update; fill `"Requires: %(name)s"` (ja: `要購入: %(name)s`), `"sale ends"` (ja: `販売終了`), `"Needed for: %(names)s"` (ja: `対象ラウンド: %(names)s`) — zh equivalents in the file's register — both files; delete the pot.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_i18n_catalogues.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: requires/needed-for lines on the concert page"
```

---

### Task 9: DM embed line

**Files:**
- Modify: `src/app/db/service.py` (`DueReminder` ~line 1337; `due_reminders` ~lines 1403–1499)
- Modify: `src/app/bot/messages.py` (`build_reminder_message`, ~line 188)
- Modify: both `.po` catalogues
- Test: `tests/test_round_requires.py` (service half), plus the file that already tests `build_reminder_message` (grep `build_reminder_message` in tests/ and extend it there)

**Interfaces:**
- Consumes: `Round.required_item_round_id`.
- Produces: `DueReminder.requires_label: str | None = None`, `DueReminder.requires_closes_at_utc: datetime | None = None`. Python msgid `"Requires: {name}"` (`.format` style — distinct from the template's `%(name)s` msgid; both live in the catalogues) and reuse of `"sale ends"`.

- [ ] **Step 1: Write the failing tests**

Service half (append to `tests/test_round_requires.py`, modeled on existing `due_reminders` tests — grep tests/ for `due_reminders(`):

```python
async def test_due_reminder_carries_requires(session):
    # Linked rounds + a rule whose queue row is due on the lottery round.
    # due_reminders() -> the row has requires_label set to the goods label
    # in the RECIPIENT's language and requires_closes_at_utc set while the
    # sale is open, None once past.
    ...
```

Message half (in the existing `build_reminder_message` test file):

```python
def test_reminder_embed_carries_requires_line():
    # Build a DueReminder(..., requires_label="グッズ販売",
    # requires_closes_at_utc=<future aware UTC>) and assert
    # "Requires:" and "グッズ販売" appear in embed.description.
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_requires.py -q` (and the messages test file)
Expected: FAIL.

- [ ] **Step 3: Implement the service half**

`DueReminder` — after `url`:

```python
    # The item-sale round this round requires, when it names one: label in
    # the RECIPIENT's language, close time only while that sale is still
    # open (same rule as RoundRow -- a closed sale's time is history).
    requires_label: str | None = None
    requires_closes_at_utc: datetime | None = None
```

In `due_reminders`, after the `rounds` dict is loaded (~line 1406): fetch the required rounds the batch's rounds point at but the batch didn't load — one extra bounded SELECT, keeping the fixed-round-trip property:

```python
    required_ids = {
        r.required_item_round_id
        for r in rounds.values()
        if r.required_item_round_id is not None
    } - set(rounds)
    required_rounds: dict[int, Round] = dict(rounds)
    if required_ids:
        required_rounds.update({
            r.id: r for r in (await session.execute(
                select(Round).where(Round.id.in_(required_ids))
            )).scalars()
        })
```

In the `DueReminder(...)` construction, before the constructor compute:

```python
        req = (
            required_rounds.get(round_.required_item_round_id)
            if round_ and round_.required_item_round_id is not None
            else None
        )
```

and pass:

```python
                requires_label=(
                    loc_field(req, "label", user.language) if req else None
                ),
                requires_closes_at_utc=(
                    req.closes_at_utc
                    if req and req.closes_at_utc and req.closes_at_utc > now
                    else None
                ),
```

- [ ] **Step 4: Implement the message half**

`build_reminder_message`, after `embed.description` is set (both branches — append once, after the if/else):

```python
    if item.requires_label:
        line = "🛍️ " + _("Requires: {name}").format(name=item.requires_label)
        if item.requires_closes_at_utc is not None:
            line += " — {} {}".format(
                _("sale ends"),
                fmt_dual(item.requires_closes_at_utc, item.user_timezone, get_locale()),
            )
        embed.description = f"{embed.description}\n{line}"
```

- [ ] **Step 5: Catalogues**

pybabel extract/update; fill `"Requires: {name}"` with the same translations Task 8 gave `"Requires: %(name)s"` (placeholder syntax differs — keep `{name}` literal in the msgstr). `"sale ends"` already exists from Task 8. Delete the pot.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_round_requires.py tests/test_i18n_catalogues.py -q`, the messages test file, then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: requires line on reminder DM embeds"
```

---

### Task 10: Skill docs, wishlist, final sweep

**Files:**
- Modify: `.claude/skills/add-concert/SKILL.md` AND `src/app/web/skill_dist/add-concert/SKILL.md` (they must stay in sync — check for a sync-pinning test with `grep -rn "skill_dist" tests/`)
- Modify: `WISHLIST.md`
- Test: whatever pins the skill's example draft to the parser (grep tests/ for `add-concert` / `SKILL.md`)

- [ ] **Step 1: Update both SKILL.md copies**

In the round-kind classification list (~line 61 of the dist copy), add:

```
  - グッズ販売 / 物販 (a merch/goods pre-order or sale window) -> `goods_sale`
```

(the existing serial-code line already says the CD sale itself is `eligibility_item_sale` — leave it). Document the new optional round key where the round fields are described:

```
  - `requires:` (optional) — the ja `label` of another round IN THIS DRAFT
    (an `eligibility_item_sale` or `goods_sale` round) whose item is needed
    to enter this round. Example: a 最速先行 whose serial code comes from the
    CD sale names that CD-sale round's label here.
```

If the skill's example draft has a natural place for it (a lottery round beside an item-sale round), add `requires:` there; run the example-pinning test and fix whichever side disagrees.

- [ ] **Step 2: WISHLIST + demo-parity note**

Per CLAUDE.md's wishlist protocol: add a Shipped entry (dated 2026-08-02) describing what shipped (the kind + the display-only link, owner's option 1, and that per-user "I bought it" capture is the recorded later layer), and run the revision pass over Proposed — expected outcome: no re-ranks on merit (this build touches rounds' model/editor/display, which no Proposed entry goes near), but the **minor demo-parity cosmetics entry GROWS again**: the requires select row and the concert page's Requires/Needed-for lines are new components with no demo frame, same resolution as the split pill (fold into that entry's single pass). Say so in the entry and in the pass paragraph.

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q` (full suite, foreground) and `uv run ruff check .`
Expected: all green, ruff clean. If anything fails, fix before committing.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: add-concert requires vocabulary + wishlist pass for goods-sale rounds"
```

---

## Self-Review Notes (already applied)

- Spec §1–§8 each map to a task: taxonomy→1, link/model→2, validation→3–5, editor→6, round-trip→7, display→8, DM→9, tests woven throughout, docs→10.
- The spec's "position in the submitted arrays" binding is implemented as a `round_key` field (the day_key mechanism the spec itself cites) — keys survive row re-ordering/removal where raw positions would not; recorded here so the deviation is a decision.
- The spec's route-side 422 is strict for POSTED values only; a PRESERVED link (whole-array omission) that no longer resolves drops silently, because 422ing a value the submitter never sent is undebuggable from a browser. Matches `parse_round_legs`' preserve-must-not-dangle rule.
- Two "Requires" msgids (jinja `%(name)s` vs python `{name}`) is deliberate — both catalogues carry both.
