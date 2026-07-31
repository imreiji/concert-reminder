# Tag Import Conflicts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the tags importer from a restore tool into a sync tool: fills happen silently, disagreements are shown and resolved by a person, and nothing is ever overwritten unseen.

**Architecture:** A pure differ (`domain/tags_diff.py`) compares the parsed file against the current catalogue and returns a plan. `db/service.py` gains the writer that applies that plan plus a set of choices. `POST /admin/import/tags` renders the plan; `POST /admin/import/tags/apply` re-parses, re-plans and commits — so the browser only ever sends `mine`/`theirs` and can never inject a value.

**Tech Stack:** Python 3.14, SQLAlchemy 2.0 async, FastAPI + Jinja2, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-31-tag-import-conflicts-design.md`

**Branch:** `tag-import-conflicts` (already exists, off `main`, carrying the spec commit).

## Global Constraints

- `uv run --isolated pytest -q` MUST pass before any commit; `uv run --isolated ruff check .` MUST be clean. Line length 100. Use `--isolated`: an external `serve.py` can lock `.venv`.
- **`/admin/*` pages are English-only and NOT wrapped in `_()`**, like `admin_deliveries.html`. Only the Preferences LINK to them is translated, and this plan adds no new link — so **no `.po` work at all**. If you reach for `pybabel`, stop: you have added a translatable string to an admin surface.
- `domain/` takes no ORM, DB or FastAPI imports.
- Business logic lives in `db/service.py`; routes stay thin.
- Route `HTTPException` details are not translated.
- **Nothing here may touch a concert.** `tag_members` is taxonomy; invariant 3 says membership edits never rewrite existing concerts, and `attach_tag` (which carries expansion) must not be called.
- **A missing choice means KEEP MINE.** Every default in this feature is the one that changes nothing.

---

### Task 1: `domain/tags_diff.py` — the pure differ

**Files:**
- Create: `src/app/domain/tags_diff.py`
- Test: `tests/test_tags_diff.py` (create)

**Interfaces:**
- Consumes: `ParsedTag` and `TagExport` from `app.domain.tags_yaml`; `TagKind` from `app.domain.types`.
- Produces, used by Tasks 2 and 3:
  - `COMPARABLE_FIELDS: tuple[str, ...]` — the eleven field names, in display order
  - `@dataclass(frozen=True) class FieldConflict` — `field: str`, `current: str`, `incoming: str`
  - `@dataclass class TagPlan` — `handle: str`, `is_new: bool`, `kind_mismatch: bool`, `incoming: ParsedTag`, `fills: dict[str, str]`, `conflicts: list[FieldConflict]`, `member_additions: list[str]`, `member_removals: list[str]`; plus a property `needs_choice: bool`
  - `@dataclass class ImportPlan` — `tags: list[TagPlan]`, `warnings: list[str]`; plus properties `created`, `changed`, `conflicted` (each `list[TagPlan]`)
  - `def plan_tag_import(incoming: Sequence[ParsedTag], current: Sequence[TagExport]) -> ImportPlan`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tags_diff.py
"""The pure differ: what an import WOULD do, before anything is written.

Its own module rather than joining tags_yaml.py: that one is about the FORMAT
(serialize/parse), this is about COMPARISON, and a module with three jobs is how
a file starts growing unwieldy.
"""

from app.domain.tags_diff import COMPARABLE_FIELDS, plan_tag_import
from app.domain.tags_yaml import ParsedTag, TagExport
from app.domain.types import TagKind


def _incoming(handle="kozue", **kw):
    base = dict(handle=handle, name="乙宗梢", kind=TagKind.ARTIST)
    return ParsedTag(**{**base, **kw})


def _current(handle="kozue", **kw):
    base = dict(handle=handle, name="乙宗梢", kind="artist")
    return TagExport(**{**base, **kw})


def test_a_handle_not_in_the_catalogue_is_new():
    plan = plan_tag_import([_incoming()], [])
    (tag,) = plan.tags
    assert tag.is_new
    assert tag.conflicts == [] and tag.fills == {}
    assert [t.handle for t in plan.created] == ["kozue"]


def test_a_blank_field_is_a_FILL_not_a_conflict():
    """Writing into emptiness cannot lose anything, so it needs no decision."""
    plan = plan_tag_import(
        [_incoming(eventernote_url="https://www.eventernote.com/actors/1")],
        [_current(eventernote_url=None)],
    )
    (tag,) = plan.tags
    assert tag.fills == {"eventernote_url": "https://www.eventernote.com/actors/1"}
    assert tag.conflicts == []
    assert not tag.is_new


def test_an_empty_string_counts_as_blank():
    """Every writer normalises "" -> None today, but the differ must not depend
    on that holding forever."""
    plan = plan_tag_import([_incoming(city="横浜")], [_current(city="")])
    assert plan.tags[0].fills == {"city": "横浜"}
    assert plan.tags[0].conflicts == []


def test_a_blank_in_the_FILE_changes_nothing():
    plan = plan_tag_import([_incoming(city=None)], [_current(city="横浜")])
    (tag,) = plan.tags
    assert tag.fills == {} and tag.conflicts == []
    assert not tag.needs_choice


def test_identical_values_change_nothing():
    plan = plan_tag_import([_incoming(city="横浜")], [_current(city="横浜")])
    assert plan.tags[0].fills == {} and plan.tags[0].conflicts == []


def test_two_different_values_are_a_CONFLICT():
    plan = plan_tag_import([_incoming(name_en="Kozue")], [_current(name_en="Kozue Otomune")])
    (tag,) = plan.tags
    assert tag.fills == {}
    (conflict,) = tag.conflicts
    assert (conflict.field, conflict.current, conflict.incoming) == (
        "name_en", "Kozue Otomune", "Kozue",
    )
    assert tag.needs_choice
    assert [t.handle for t in plan.conflicted] == ["kozue"]


def test_every_comparable_field_is_actually_compared():
    """A field silently missing from COMPARABLE_FIELDS would never conflict and
    never fill -- it would just be ignored, which is the quiet kind of wrong."""
    incoming = _incoming(
        name="A", name_en="B", name_zh="C", parent="p", region="R", city="D",
        city_en="E", city_zh="F", address="G", location_url="http://h",
        eventernote_url="http://i",
    )
    current = _current(
        name="z", name_en="z", name_zh="z", parent="z", region="z", city="z",
        city_en="z", city_zh="z", address="z", location_url="z",
        eventernote_url="z",
    )
    conflicts = {c.field for c in plan_tag_import([incoming], [current]).tags[0].conflicts}
    assert conflicts == set(COMPARABLE_FIELDS)
    assert len(COMPARABLE_FIELDS) == 11


def test_kind_disagreeing_skips_the_tag_entirely():
    """A venue arriving as an artist could orphan a leg's venue_tag_id. Not
    choosable: warn loudly and touch nothing, fills included."""
    plan = plan_tag_import(
        [_incoming(kind=TagKind.VENUE, city="横浜")],
        [_current(kind="artist", city=None)],
    )
    (tag,) = plan.tags
    assert tag.kind_mismatch
    assert tag.fills == {} and tag.conflicts == []
    assert not tag.needs_choice
    assert any("kind" in w and "kozue" in w for w in plan.warnings)


def test_member_additions_and_removals_are_separate_directions():
    plan = plan_tag_import(
        [_incoming(handle="g", kind=TagKind.GROUP, members=["a", "b"])],
        [_current(handle="g", kind="group", members=("b", "c"))],
    )
    (tag,) = plan.tags
    assert tag.member_additions == ["a"]
    assert tag.member_removals == ["c"]
    assert tag.needs_choice


def test_a_new_tag_has_no_member_diff():
    """Its members are simply created; there is nothing to compare against."""
    plan = plan_tag_import(
        [_incoming(handle="g", kind=TagKind.GROUP, members=["a"])], []
    )
    (tag,) = plan.tags
    assert tag.is_new
    assert tag.member_additions == [] and tag.member_removals == []


def test_parent_is_an_ordinary_field_not_a_set():
    plan = plan_tag_import([_incoming(parent="love-live")], [_current(parent="gakumas")])
    (conflict,) = plan_tag_import(
        [_incoming(parent="love-live")], [_current(parent="gakumas")]
    ).tags[0].conflicts
    assert conflict.field == "parent"
    assert plan.tags[0].member_additions == []


def test_a_catalogue_tag_absent_from_the_file_is_untouched_and_unmentioned():
    """Nothing is ever deleted. A tag the file does not mention does not appear
    in the plan at all."""
    plan = plan_tag_import([_incoming("kozue")], [_current("kozue"), _current("kaho")])
    assert [t.handle for t in plan.tags] == ["kozue"]


def test_incoming_warnings_are_carried_through():
    plan = plan_tag_import([_incoming()], [], warnings=["parser said something"])
    assert "parser said something" in plan.warnings
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tags_diff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.tags_diff'`

- [ ] **Step 3: Write the module**

```python
# src/app/domain/tags_diff.py
"""What an import WOULD do, computed before anything is written.

Pure: no I/O, no ORM. Takes the parsed file and a snapshot of the current
catalogue, returns a plan the route can render and the service can apply.

Its own module rather than joining `tags_yaml.py`: that one is about the FORMAT
(serialize and parse), this is about COMPARISON. A module with three jobs is how
a file starts growing unwieldy.

The rule, per field: a blank on the DB side is a FILL (writing into emptiness
cannot lose anything, so it needs no decision); a blank in the file changes
nothing; equal values change nothing; and two different values are a CONFLICT
somebody has to resolve.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.tags_yaml import ParsedTag, TagExport

# The eleven fields that participate. `kind` is compared but never offered (see
# below), and `handle` is the match key so it cannot differ. A field missing
# from this tuple would silently never fill and never conflict -- it would just
# be ignored -- which is why a test pins the count and the set.
COMPARABLE_FIELDS = (
    "name",
    "name_en",
    "name_zh",
    "parent",
    "region",
    "city",
    "city_en",
    "city_zh",
    "address",
    "location_url",
    "eventernote_url",
)


def _blank(value: str | None) -> bool:
    """NULL or whitespace-only. Every writer normalises "" -> None today, but a
    differ that depends on that holding forever is a differ waiting to break."""
    return value is None or not str(value).strip()


@dataclass(frozen=True)
class FieldConflict:
    field: str
    current: str
    incoming: str


@dataclass
class TagPlan:
    handle: str
    incoming: ParsedTag
    is_new: bool = False
    kind_mismatch: bool = False
    fills: dict[str, str] = field(default_factory=dict)
    conflicts: list[FieldConflict] = field(default_factory=list)
    member_additions: list[str] = field(default_factory=list)
    member_removals: list[str] = field(default_factory=list)

    @property
    def needs_choice(self) -> bool:
        """Does a person have to decide something about this tag?"""
        return bool(self.conflicts or self.member_additions or self.member_removals)


@dataclass
class ImportPlan:
    tags: list[TagPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def created(self) -> list[TagPlan]:
        return [t for t in self.tags if t.is_new]

    @property
    def changed(self) -> list[TagPlan]:
        """Existing tags with something to apply automatically (fills only)."""
        return [t for t in self.tags if not t.is_new and t.fills and not t.kind_mismatch]

    @property
    def conflicted(self) -> list[TagPlan]:
        return [t for t in self.tags if t.needs_choice]


def plan_tag_import(
    incoming: Sequence[ParsedTag],
    current: Sequence[TagExport],
    warnings: Sequence[str] = (),
) -> ImportPlan:
    """Compare the file against the catalogue. Writes nothing, decides nothing.

    A catalogue tag the file does not mention is UNTOUCHED and unmentioned --
    nothing is ever deleted by an import.
    """
    plan = ImportPlan(warnings=list(warnings))
    by_handle = {t.handle: t for t in current}

    for tag in incoming:
        existing = by_handle.get(tag.handle)
        if existing is None:
            plan.tags.append(TagPlan(handle=tag.handle, incoming=tag, is_new=True))
            continue

        # kind is compared but NOT choosable: a venue arriving as an artist could
        # orphan a leg whose venue_tag_id points at it. Warn loudly, touch
        # nothing -- fills included.
        if existing.kind != tag.kind.value:
            plan.tags.append(
                TagPlan(handle=tag.handle, incoming=tag, kind_mismatch=True)
            )
            plan.warnings.append(
                f"{tag.handle}: the file calls this a {tag.kind.value} but the "
                f"catalogue has a {existing.kind} -- skipped entirely, nothing "
                f"about this tag was touched"
            )
            continue

        entry = TagPlan(handle=tag.handle, incoming=tag)
        for name in COMPARABLE_FIELDS:
            mine = getattr(existing, name)
            theirs = getattr(tag, name)
            if _blank(theirs):
                continue
            if _blank(mine):
                entry.fills[name] = theirs
            elif str(mine).strip() != str(theirs).strip():
                entry.conflicts.append(
                    FieldConflict(field=name, current=str(mine), incoming=str(theirs))
                )

        mine_members = set(existing.members)
        their_members = set(tag.members)
        entry.member_additions = sorted(their_members - mine_members)
        entry.member_removals = sorted(mine_members - their_members)
        plan.tags.append(entry)

    return plan
```

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_tags_diff.py -q`
Expected: PASS, all thirteen.

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src/app/domain/tags_diff.py tests/test_tags_diff.py
git commit -m "feat: a pure differ for the tags import"
```

---

### Task 2: applying a plan, with choices

**Files:**
- Modify: `src/app/db/service.py` — add `current_tag_exports`, `apply_tag_import`; REPLACE `import_tags`
- Modify: `tests/test_tag_import.py` — its `_import` helper now goes through plan+apply
- Test: `tests/test_tag_import.py` (extend)

**Interfaces:**
- Consumes: `plan_tag_import`, `ImportPlan`, `TagPlan` (Task 1); `create_tag_row`, `TagImportReport` (existing).
- Produces, used by Task 3:
  - `async def current_tag_exports(session) -> list[TagExport]`
  - `@dataclass class ImportChoices` — `fields: dict[tuple[str, str], str]` (`(handle, field) -> "mine"|"theirs"`), `members: dict[tuple[str, str], str]` (`(handle, member_handle) -> "add"|"remove"`)
  - `async def apply_tag_import(session, plan: ImportPlan, choices: ImportChoices, created_by: int | None = None) -> TagImportReport`
  - `TagImportReport` gains `filled: list[str]` and `resolved: list[str]` beside the existing `created`, `skipped`, `warnings`.

- [ ] **Step 1: Write the failing tests**

Replace `test_tag_import.py`'s `_import` helper and add the new cases. The
helper now runs the real two-step path, because that is what the route does:

```python
from app.db.service import (
    ImportChoices,
    apply_tag_import,
    assign_tag_slug,
    current_tag_exports,
    ensure_user,
)
from app.domain.tags_diff import plan_tag_import
from app.domain.tags_yaml import parse_tags


async def _import(db, text=FILE, choices=None):
    """Plan then apply, exactly as the route does."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        parsed = parse_tags(text)
        plan = plan_tag_import(parsed.tags, await current_tag_exports(s), parsed.warnings)
        report = await apply_tag_import(
            s, plan, choices or ImportChoices(), created_by=ADMIN
        )
        await s.commit()
        return report
```

New cases (append to the file):

```python
async def test_a_blank_field_is_filled_without_being_asked(db):
    """The case this feature exists for: 79 artists with no eventernote_url."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        tag = Tag(name="乙宗梢", kind=TagKind.ARTIST, slug="kozue")
        s.add(tag)
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", kind: artist, eventernote_url: "https://e.example/1"}
""")
    assert report.filled == ["kozue"]
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.eventernote_url == "https://e.example/1"


async def test_a_conflict_left_unanswered_keeps_mine(db):
    """The safe default: a truncated or half-submitted form overwrites nothing."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="乙宗梢", name_en="Kozue Otomune", kind=TagKind.ARTIST, slug="kozue"))
        await s.commit()

    await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", name_en: Kozue, kind: artist}
""")
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue Otomune", "unanswered must not overwrite"


async def test_a_conflict_answered_theirs_takes_the_file(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="乙宗梢", name_en="Kozue Otomune", kind=TagKind.ARTIST, slug="kozue"))
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", name_en: Kozue, kind: artist}
""", choices=ImportChoices(fields={("kozue", "name_en"): "theirs"}))
    assert report.resolved == ["kozue"]
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue"


async def test_a_member_is_removed_ONLY_when_explicitly_chosen(db):
    """The only destructive operation here, so it never happens by default."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        group = Tag(name="G", kind=TagKind.GROUP, slug="g")
        member = Tag(name="M", kind=TagKind.ARTIST, slug="m")
        s.add_all([group, member])
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=member.id))
        await s.commit()

    file = 'tags:\n  - {handle: g, name: G, kind: group}\n  - {handle: m, name: M, kind: artist}\n'
    await _import(db, file)
    async with db() as s:
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1, (
            "no choice given -> the member stays"
        )

    await _import(db, file, choices=ImportChoices(members={("g", "m"): "remove"}))
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_a_member_addition_applies_when_chosen(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add_all([
            Tag(name="G", kind=TagKind.GROUP, slug="g"),
            Tag(name="M", kind=TagKind.ARTIST, slug="m"),
        ])
        await s.commit()

    await _import(
        db,
        'tags:\n  - {handle: g, name: G, kind: group, members: [m]}\n'
        '  - {handle: m, name: M, kind: artist}\n',
        choices=ImportChoices(members={("g", "m"): "add"}),
    )
    async with db() as s:
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1


async def test_a_kind_mismatch_writes_nothing_at_all(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="Hall", kind=TagKind.VENUE, slug="hall"))
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: hall, name: Hall, kind: artist, city: "横浜"}
""")
    assert report.created == [] and report.filled == []
    assert any("kind" in w for w in report.warnings)
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "hall"))).scalar_one()
        assert row.kind is TagKind.VENUE and row.city is None


async def test_a_choice_naming_a_handle_not_in_the_file_is_ignored(db):
    """A forged form must not reach past what was pasted."""
    report = await _import(db, choices=ImportChoices(
        fields={("not-in-file", "name"): "theirs"},
        members={("also-not", "x"): "remove"},
    ))
    assert sorted(report.created) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_tag_import.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_tag_import'`

- [ ] **Step 3: Extract `current_tag_exports`**

Lift the `TagExport` construction out of `catalogue_export_files` into its own
function and have that caller use it — one builder, so the export and the differ
can never disagree about what the catalogue looks like:

```python
async def current_tag_exports(session: AsyncSession) -> list[TagExport]:
    """The whole catalogue as TagExport rows, kind then handle.

    One builder shared by the zip export and the import differ. Two would drift,
    and a differ comparing against a slightly different snapshot than the export
    wrote is the sort of bug that only shows up months later in a restore.
    """
    tags = list((await session.execute(
        select(Tag).options(selectinload(Tag.members)).order_by(Tag.kind, Tag.slug)
    )).scalars())
    by_id = {t.id: t for t in tags}
    return [
        TagExport(
            handle=t.slug, name=t.name, kind=t.kind.value,
            name_en=t.name_en, name_zh=t.name_zh,
            parent=by_id[t.parent_id].slug if t.parent_id in by_id else None,
            members=tuple(sorted(m.slug for m in t.members)),
            region=t.region, city=t.city, city_en=t.city_en, city_zh=t.city_zh,
            address=t.address, location_url=t.location_url,
            eventernote_url=t.eventernote_url,
        )
        for t in tags
    ]
```

In `catalogue_export_files`, replace the inline block with
`exports = await current_tag_exports(session)`.

- [ ] **Step 4: Replace `import_tags` with `apply_tag_import`**

Delete `import_tags` entirely — two functions that both write tags is exactly
what `create_tag_row` was extracted to avoid — and add:

```python
@dataclass
class ImportChoices:
    """What the operator decided, keyed by (handle, field) and (handle, member).

    Values are the literal strings "mine"/"theirs" and "add"/"remove" and nothing
    else. The browser never sends a VALUE -- the data comes from re-parsing the
    pasted file -- so a forged form cannot inject anything.
    """

    fields: dict[tuple[str, str], str] = field(default_factory=dict)
    members: dict[tuple[str, str], str] = field(default_factory=dict)


async def apply_tag_import(
    session: AsyncSession,
    plan: ImportPlan,
    choices: ImportChoices,
    created_by: int | None = None,
) -> TagImportReport:
    """Write what the plan says, resolved by the operator's choices.

    Every default is the one that changes nothing: an unanswered conflict keeps
    the catalogue's value, and a member removal happens only when explicitly
    chosen. A truncated form therefore cannot overwrite or delete anything.

    Does not commit -- the caller owns the transaction, so a rejected file leaves
    nothing behind.
    """
    report = TagImportReport(warnings=list(plan.warnings))
    by_slug = {
        slug: tag_id for tag_id, slug in await session.execute(select(Tag.id, Tag.slug))
    }

    for entry in plan.tags:
        if entry.kind_mismatch:
            report.skipped.append(entry.handle)
            continue
        tag = entry.incoming
        if entry.is_new:
            row = await create_tag_row(
                session,
                name=tag.name, kind=tag.kind, slug=tag.handle,
                name_en=tag.name_en, name_zh=tag.name_zh,
                region=tag.region, city=tag.city, city_en=tag.city_en,
                city_zh=tag.city_zh, address=tag.address,
                location_url=tag.location_url, eventernote_url=tag.eventernote_url,
                created_by=created_by,
            )
            await session.flush()
            by_slug[tag.handle] = row.id
            report.created.append(entry.handle)
            continue

        row = await session.get(Tag, by_slug[entry.handle])
        touched = False
        for name, value in entry.fills.items():
            if name == "parent":
                continue  # resolved below, it is a handle not a value
            setattr(row, name, value)
            touched = True
        resolved = False
        for conflict in entry.conflicts:
            if choices.fields.get((entry.handle, conflict.field)) != "theirs":
                continue  # KEEP MINE is the default, including "no answer"
            if conflict.field == "parent":
                continue  # resolved below
            setattr(row, conflict.field, conflict.incoming)
            resolved = True
        if touched:
            report.filled.append(entry.handle)
        if resolved:
            report.resolved.append(entry.handle)

    # Parent and membership need every tag to exist, so they run in a second
    # pass -- the same reason the original importer had two.
    for entry in plan.tags:
        if entry.kind_mismatch or entry.handle not in by_slug:
            continue
        row = await session.get(Tag, by_slug[entry.handle])
        wanted_parent = entry.incoming.parent
        take_parent = entry.is_new or "parent" in entry.fills or any(
            c.field == "parent" and choices.fields.get((entry.handle, "parent")) == "theirs"
            for c in entry.conflicts
        )
        if take_parent and wanted_parent:
            parent_id = by_slug.get(wanted_parent)
            if parent_id is None:
                report.warnings.append(
                    f"{entry.handle}: parent {wanted_parent!r} is in neither the file "
                    f"nor the catalogue -- left without a parent"
                )
            else:
                parent_kind = (await session.get(Tag, parent_id)).kind
                if parent_kind is not TagKind.FRANCHISE:
                    report.warnings.append(
                        f"{entry.handle}: parent {wanted_parent!r} is not a franchise "
                        f"-- left without a parent"
                    )
                else:
                    row.parent_id = parent_id

        additions = entry.incoming.members if entry.is_new else entry.member_additions
        for member in additions:
            if not entry.is_new and choices.members.get((entry.handle, member)) != "add":
                continue
            member_id = by_slug.get(member)
            if member_id is None:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is in neither the file nor the "
                    f"catalogue -- that membership dropped"
                )
                continue
            if (await session.get(Tag, member_id)).kind is TagKind.GROUP:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is a group, and groups do not "
                    f"nest -- dropped"
                )
                continue
            session.add(TagMember(group_tag_id=by_slug[entry.handle], member_tag_id=member_id))
        for member in entry.member_removals:
            if choices.members.get((entry.handle, member)) != "remove":
                continue  # NEVER by default -- the one destructive operation here
            member_id = by_slug.get(member)
            if member_id is not None:
                await session.execute(
                    delete(TagMember).where(
                        TagMember.group_tag_id == by_slug[entry.handle],
                        TagMember.member_tag_id == member_id,
                    )
                )
    return report
```

Add `filled: list[str] = field(default_factory=list)` and
`resolved: list[str] = field(default_factory=list)` to `TagImportReport`. `delete`
is already imported in `service.py`.

- [ ] **Step 5: Update the existing importer tests**

`test_tag_import.py`'s older cases still describe correct behaviour, with two
exceptions that must be REWRITTEN, not deleted, because they pinned the
skip-whole rule this feature replaces:

- `test_importing_twice_changes_nothing` — still true; the second run has no
  fills and no conflicts. Keep, and add an assertion that `report.filled == []`.
- `test_an_existing_tag_is_not_updated` — now WRONG in its blanket form. Rename
  to `test_an_existing_tag_is_not_updated_without_a_choice` and assert the
  populated field is untouched while a BLANK one is filled. The rule is no
  longer "never touch an existing tag", it is "never overwrite unasked".
- `test_membership_of_an_existing_tag_is_left_alone` — same: rename to
  `..._without_a_choice` and assert the membership is unchanged when no choice
  is given.

- [ ] **Step 6: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_tag_import.py tests/test_tags_diff.py tests/test_catalogue_export.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: apply a tag-import plan, resolved by the operator's choices"
```

---

### Task 3: the two-step route and the plan page

**Files:**
- Modify: `src/app/web/routes/admin.py`
- Modify: `src/app/web/templates/admin_import_tags.html`
- Modify: `tests/test_admin_import_tags.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces: `POST /admin/import/tags` renders the plan; `POST /admin/import/tags/apply` commits.

- [ ] **Step 1: Write the failing tests**

Two existing tests in `test_admin_import_tags.py` describe the OLD one-step
behaviour and are deliberate reversals — rewrite them, do not delete:

- `test_a_good_file_creates_the_tags_and_reports` becomes
  `test_a_good_file_is_previewed_then_applied` (POST to `/tags` shows the plan
  and writes nothing; POST to `/tags/apply` writes).
- `test_a_second_import_reports_skips_and_writes_nothing` becomes
  `test_a_second_import_has_nothing_to_do`.

Then add:

```python
async def test_the_preview_writes_nothing(client):
    """The whole point of a two-step flow: looking is not doing."""
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "love-live" in body
    async with client.db() as s:
        assert (await s.execute(select(Tag))).scalars().all() == []


async def test_a_conflict_is_shown_with_both_values(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    body = client.post("/admin/import/tags", data={
        "text": 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n',
    }).text
    assert "Kozue Otomune" in body or "kozue" in body
    assert "Renamed" in body
    assert 'name="conflict__kozue__name_en"' in body


async def test_apply_honours_a_theirs_choice(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    newer = 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n'
    client.post("/admin/import/tags/apply", data={
        "text": newer, "conflict__kozue__name_en": "theirs",
    })
    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Renamed"


async def test_apply_with_no_choices_changes_nothing_it_was_not_asked_to(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    newer = 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n'
    client.post("/admin/import/tags/apply", data={"text": newer})
    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue Otomune"


def test_a_forged_choice_value_is_refused(client):
    """Only the literal strings are accepted; the VALUE always comes from the
    re-parsed file, so there is nothing to inject."""
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/import/tags/apply", data={
        "text": FILE, "conflict__kozue__name_en": "<script>alert(1)</script>",
    })
    assert r.status_code == 200
    assert "alert(1)" not in r.text


def test_an_editor_cannot_reach_apply(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.post("/admin/import/tags/apply", data={"text": FILE}).status_code == 403
```

`FILE` in that module needs `name_en: Kozue Otomune` on the `kozue` entry for
these to have something to conflict with — update the constant.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_admin_import_tags.py -q`
Expected: FAIL — `/admin/import/tags/apply` is a 404 and the preview still writes.

- [ ] **Step 3: Parse the choices**

Add to `admin.py`, above the routes:

```python
def _read_choices(form) -> ImportChoices:
    """Pull the operator's decisions out of the posted form.

    Field names are `conflict__<handle>__<field>` and
    `member__<handle>__<member>`; handles are [a-z0-9_-] by construction so the
    names need no escaping. Values are checked against the literal sets and
    anything else is DISCARDED -- the browser never supplies data, only a
    decision, so an unexpected value can only ever mean "keep mine".
    """
    choices = ImportChoices()
    for key, value in form.multi_items():
        parts = key.split("__")
        if len(parts) != 3:
            continue
        kind, handle, name = parts
        if kind == "conflict" and value in ("mine", "theirs"):
            choices.fields[(handle, name)] = value
        elif kind == "member" and value in ("add", "remove"):
            choices.members[(handle, name)] = value
    return choices
```

- [ ] **Step 4: Rewrite the two routes**

`POST /admin/import/tags` renders the plan; the new `/apply` commits:

```python
@router.post("/admin/import/tags", response_class=HTMLResponse)
async def import_tags_preview(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    text: str = Form(""),
):
    """Show what the import WOULD do. Writes nothing -- looking is not doing."""
    if len(text) > MAX_DRAFT_CHARS:
        return _import_page(
            request, user, text,
            error="that file is too large -- pastes are capped at 200k characters",
        )
    try:
        parsed = parse_tags(text)
    except TagsFileError as exc:
        return _import_page(request, user, text, error=str(exc))
    plan = plan_tag_import(parsed.tags, await current_tag_exports(session), parsed.warnings)
    return _import_page(request, user, text, plan=plan)


@router.post("/admin/import/tags/apply", response_class=HTMLResponse)
async def import_tags_apply(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    text: str = Form(""),
):
    """Commit, resolved by the choices in the form.

    RE-PARSES and RE-PLANS from the pasted file rather than trusting anything
    the browser sends about content. The form carries decisions only, so a
    forged post cannot inject a value that was not in the file -- and a conflict
    that vanished since the preview (because the catalogue changed) simply is
    not applied.
    """
    if len(text) > MAX_DRAFT_CHARS:
        return _import_page(
            request, user, text,
            error="that file is too large -- pastes are capped at 200k characters",
        )
    try:
        parsed = parse_tags(text)
    except TagsFileError as exc:
        return _import_page(request, user, text, error=str(exc))
    plan = plan_tag_import(parsed.tags, await current_tag_exports(session), parsed.warnings)
    choices = _read_choices(await request.form())
    await ensure_user(session, user.id, user.username)
    report = await apply_tag_import(session, plan, choices, created_by=user.id)
    await session.commit()
    return _import_page(request, user, text, report=report)
```

with a shared page helper beside them:

```python
def _import_page(request, user, text, *, plan=None, report=None, error=None):
    return templates.TemplateResponse(
        request,
        "admin_import_tags.html",
        {"user": user, "text": text, "plan": plan, "report": report, "error": error},
    )
```

Update `import_tags_form` (the GET) to call `_import_page(request, user, "")`.
Imports to add: `ImportChoices`, `apply_tag_import`, `current_tag_exports` from
`app.db.service`; `plan_tag_import` from `app.domain.tags_diff`.

- [ ] **Step 5: The plan view**

In `admin_import_tags.html`, between the error banner and the paste form, add
the plan block. English-only, no `_()`:

```html
{% if plan %}
<form method="post" action="/admin/import/tags/apply" class="stack wide">
  <input type="hidden" name="text" value="{{ text }}">

  {% if plan.created %}
  <div class="edgecard ok">
    <p><b>{{ plan.created | length }} new tag(s)</b> will be created.</p>
    <p class="dim">{% for t in plan.created %}<code>{{ t.handle }}</code>{% if not loop.last %}, {% endif %}{% endfor %}</p>
  </div>
  {% endif %}

  {% if plan.changed %}
  <div class="edgecard ok">
    <p><b>{{ plan.changed | length }} tag(s)</b> have empty fields the file can fill.
      Filling cannot lose anything, so these are not asked about.</p>
    <ul class="dim">
      {% for t in plan.changed %}
      <li><code>{{ t.handle }}</code> — {% for f, v in t.fills.items() %}{{ f }}: {{ v }}{% if not loop.last %}; {% endif %}{% endfor %}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if plan.conflicted %}
  <div class="banner warn">
    <span>⚠</span>
    <span><b>{{ plan.conflicted | length }} tag(s) disagree with the file.</b>
      Nothing is overwritten unless you say so — leaving a row alone keeps what is
      in the catalogue.</span>
  </div>
  <p><button type="button" id="all-mine">Keep all mine</button>
     <button type="button" id="all-theirs">Take all theirs</button></p>

  {% for t in plan.conflicted %}
  <div class="edgecard dg">
    <p><b><code>{{ t.handle }}</code></b></p>
    {% for c in t.conflicts %}
    <div class="subrow two">
      <span class="nm3">{{ c.field }}</span>
      <span class="sw">
        <label><input type="radio" name="conflict__{{ t.handle }}__{{ c.field }}" value="mine" checked> keep <code>{{ c.current }}</code></label>
        <label><input type="radio" name="conflict__{{ t.handle }}__{{ c.field }}" value="theirs"> take <code>{{ c.incoming }}</code></label>
      </span>
    </div>
    {% endfor %}
    {% for m in t.member_additions %}
    <div class="subrow two">
      <span class="nm3">member</span>
      <span class="sw"><label><input type="checkbox" name="member__{{ t.handle }}__{{ m }}" value="add" checked> add <code>{{ m }}</code></label></span>
    </div>
    {% endfor %}
    {% for m in t.member_removals %}
    <div class="subrow two">
      <span class="nm3">member</span>
      <span class="sw"><label><input type="checkbox" name="member__{{ t.handle }}__{{ m }}" value="remove"> remove <code>{{ m }}</code> — this is the only destructive choice here</label></span>
    </div>
    {% endfor %}
  </div>
  {% endfor %}
  {% endif %}

  <button type="submit">Apply</button>
</form>

<script>
  // Bulk controls: a long list is otherwise a lot of clicking for what is
  // usually one decision repeated. Radios only -- member checkboxes keep their
  // own defaults, because "take all theirs" must never mean "delete members".
  (function () {
    function setAll(value) {
      document.querySelectorAll('input[type=radio][value="' + value + '"]')
        .forEach(function (r) { r.checked = true; });
    }
    document.getElementById("all-mine")?.addEventListener("click", function () { setAll("mine"); });
    document.getElementById("all-theirs")?.addEventListener("click", function () { setAll("theirs"); });
  })();
</script>
{% endif %}
```

Extend the existing report block to show `filled` and `resolved` beside
`created` and `skipped`.

- [ ] **Step 6: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_admin_import_tags.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: preview a tag import, then apply the choices"
```

---

### Task 4: docs and close-out

**Files:**
- Modify: `CLAUDE.md`, `WISHLIST.md`, `README.md`
- Modify: `docs/superpowers/specs/2026-07-31-tag-import-conflicts-design.md`

- [ ] **Step 1: CLAUDE.md**

In the tag-identity paragraph, replace "The import SKIPS an existing handle
entirely, never updating it" with the new rule: the import PLANS first, fills
blanks automatically, asks about disagreements, and never removes a member
unless explicitly told. Note that `kind` disagreeing skips the tag entirely, and
that the apply step re-parses so the browser only sends decisions. Add
`domain/tags_diff.py` to the domain bullet.

- [ ] **Step 2: WISHLIST.md**

Add a Shipped entry dated today describing the feature and WHY it exists (the 79
empty `eventernote_url` values that the restore-only importer could not carry),
then do the revision pass the rule requires: re-read each Proposed entry, and in
particular note whether Eventernote actor-page discovery (#1) got cheaper now
that field values can be carried across.

- [ ] **Step 3: README.md**

Extend the catalogue round-trip bullet rather than adding a second one: the
importer now previews, fills blanks, and asks about disagreements.

- [ ] **Step 4: Spec close-out**

Set `Status:` to `implemented (2026-07-31)` and record deviations at the foot.
If there were none, say so explicitly — an empty deviations section is
information.

- [ ] **Step 5: Final gates and commit**

```bash
uv run --isolated ruff check . && uv run --isolated pytest -q
git add docs CLAUDE.md WISHLIST.md README.md
git commit -m "docs: close out tag-import conflict resolution"
```

---

## Self-review notes

**Spec coverage.** Four-case model → Task 1. `handle` as key and `kind`
warn-and-skip → Task 1. Eleven comparable fields (pinned by count AND set) →
Task 1. Member set-diff, additions pre-ticked, removals never → Tasks 1–3.
`parent` as ordinary field → Tasks 1–2. Stateless hidden-field round trip,
re-parse on apply, missing choice = keep mine → Tasks 2–3. Bulk buttons → Task
3. Replacing the shipped one-step route → Task 3. Nothing deleted, ever → Task
1's `test_a_catalogue_tag_absent_from_the_file_is_untouched_and_unmentioned`.
Out-of-scope items appear nowhere.

**Two rewrites, not deletions.** `test_an_existing_tag_is_not_updated` and
`test_membership_of_an_existing_tag_is_left_alone` pinned the skip-whole rule
this feature replaces, and two route tests pinned the one-step flow. All four
are renamed to say what is now true, because a deleted test leaves no trace that
the behaviour was once deliberate.

**Known risk.** `apply_tag_import` is the longest function in the plan and it
carries every default that must fail safe. The three that matter are each
pinned by their own test: an unanswered conflict keeps mine, a removal happens
only when chosen, and a kind mismatch writes nothing at all. If that function
needs to grow further, splitting the second pass (parent and membership) into
its own helper is the natural seam.
