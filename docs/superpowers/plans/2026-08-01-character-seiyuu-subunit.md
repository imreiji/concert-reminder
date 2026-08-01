# Character Tags, Seiyuu and Subunits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Idolm@ster events credited to a CHARACTER reach the people following that character's seiyuu, and let a subunit display beneath its parent group.

**Architecture:** Characters become a fifth tag kind carrying `voiced_by_tag_id`. Attaching a character also attaches its seiyuu, so `tracked_concert_ids` — which matches materialised `concert_tags` rows — needs no change at all. `parent_id` widens to allow GROUP→GROUP and CHARACTER→FRANCHISE. Two display rules, both the same rule: draw a relationship only when both ends are attached to this concert.

**Tech Stack:** Python 3.14, SQLAlchemy 2.0 async + Alembic (SQLite batch mode), FastAPI + Jinja2, babel gettext (en/ja/zh), pytest-asyncio auto mode, uv.

**Spec:** `docs/superpowers/specs/2026-08-01-character-seiyuu-subunit-design.md` — read it before Task 1. Where this plan and the spec disagree, the spec wins; report the conflict rather than guessing.

**Branch:** `character-seiyuu-tags` (exists, spec committed). Do not create another.

## Global Constraints

- Verification MUST run in the FOREGROUND with an explicit `timeout: 600000` on the Bash call and never `run_in_background`. The suite takes ~3 minutes; Bash defaults to 120s and an exceeded run is silently backgrounded where you will wait forever.
- Always `uv run --isolated` — an external process holds a lock on `.venv`.
- `uv run --isolated pytest -q` and `uv run --isolated ruff check .` must both pass before every commit.
- `src/app/domain/` may NOT import discord, fastapi, sqlalchemy or httpx.
- The DB stores aware UTC only. Python 3.14, modern typing (`str | None`).
- Tests use pytest-asyncio auto mode. DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener — cascades silently do not fire without it.
- After `alembic revision --autogenerate`: replace `app.db.models.UTCDateTime()` with `sa.DateTime()`, delete the `import app.db.models` line, use `batch_alter_table` for column adds, never `drop_constraint`, keep the file ASCII-only, and verify upgrade → downgrade -1 → upgrade round-trips.
- **The concert page and the Tags page are user-facing and translated.** Any new visible string needs `_()` and BOTH `messages.po` catalogues filled non-fuzzy — `tests/test_i18n_catalogues.py` enforces it, and fuzzy counts as untranslated. pybabel likes to pre-fill a new msgid from a similar one; verify the resolved string in Python, not by eye, because this console is GBK.
- Chips use `border-radius: 999px`; everything else is 3px. Sentence case. Only two callout shapes exist and this feature adds none.
- **Assert the property, not a proxy.** Every recent task on this repo had a review finding where a test's truth value did not depend on the code path it named. Mutation-verify: delete the code the test names, watch it fail, restore.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/types.py` | `TagKind.CHARACTER` |
| `src/app/db/models.py` | `Tag.voiced_by_tag_id` |
| `src/app/db/service.py` | attach chaining, prune rule, `performer_clusters` depth + pairing, the cycle guard's query |
| `src/app/web/routes/tags.py` | widened parent validation, `voiced_by` on create/edit |
| `src/app/web/templates/concert_detail.html` | split pill + indented rail |
| `src/app/web/templates/tags.html` | CHARACTER section, `voiced_by` picker |
| `src/app/web/static/style.css` | `.mchip` split pill, `.pcluster.sub` rail |
| `src/app/domain/tags_yaml.py` | `voiced_by` in the format, both halves |
| `src/app/domain/tags_diff.py` | `COMPARABLE_FIELDS` 11 → 12 |

---

## Task 1: The model and its migration

**Files:**
- Modify: `src/app/domain/types.py`, `src/app/db/models.py`
- Create: `alembic/versions/<generated>_character_tags.py`
- Test: `tests/test_character_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TagKind.CHARACTER` (value `"character"`); `Tag.voiced_by_tag_id: Mapped[int | None]`.

- [ ] **Step 1: Write the failing test**

```python
"""Character tags: a fifth kind, and who voices them."""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tag
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_character_is_a_tag_kind():
    assert TagKind.CHARACTER.value == "character"


async def test_a_character_records_who_voices_her(db):
    async with db() as s:
        seiyuu = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add(seiyuu)
        await s.flush()
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya-kisaragi",
                  voiced_by_tag_id=seiyuu.id))
        await s.commit()

    async with db() as s:
        chihaya = (await s.execute(
            select(Tag).where(Tag.slug == "chihaya-kisaragi")
        )).scalar_one()
        assert chihaya.voiced_by_tag_id == seiyuu.id


async def test_deleting_the_seiyuu_leaves_the_character(db):
    """SET NULL, never CASCADE: a character outlives her voice actor's tag,
    exactly as a leg outlives its venue tag."""
    async with db() as s:
        seiyuu = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add(seiyuu)
        await s.flush()
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya-kisaragi",
                  voiced_by_tag_id=seiyuu.id))
        await s.commit()
        await s.delete(seiyuu)
        await s.commit()

    async with db() as s:
        chihaya = (await s.execute(
            select(Tag).where(Tag.slug == "chihaya-kisaragi")
        )).scalar_one()
        assert chihaya is not None, "the character must survive"
        assert chihaya.voiced_by_tag_id is None


async def test_voiced_by_defaults_to_none(db):
    async with db() as s:
        s.add(Tag(name="天海春香", kind=TagKind.CHARACTER, slug="haruka-amami"))
        await s.commit()
        row = (await s.execute(select(Tag).where(Tag.slug == "haruka-amami"))).scalar_one()
        assert row.voiced_by_tag_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_character_model.py -q`
Expected: FAIL — `AttributeError: CHARACTER`

- [ ] **Step 3: Add the kind and the column**

In `src/app/domain/types.py`, inside `TagKind`:

```python
    CHARACTER = "character"   # 如月千早 — voiced by an ARTIST, see Tag.voiced_by_tag_id
```

Also widen `TagKind`'s docstring: it currently says "GROUP tags contain member (usually ARTIST) tags" — a group's members may now be ARTIST tags, CHARACTER tags, or a mix.

In `src/app/db/models.py`, on `Tag`, beside `parent_id`:

```python
    # CHARACTER-specific: the ARTIST tag who voices her. Its OWN column rather
    # than parent_id, deliberately -- parent_id means "the broader thing I
    # belong to" and renders the Tags hierarchy, and a seiyuu is not broader
    # than a character. Keeping parent_id free also lets a character say she
    # belongs to a franchise, which she could not otherwise do.
    # SET NULL, never CASCADE: deleting a seiyuu's tag must not take the
    # character down with it, the same reasoning as ConcertDay.venue_tag_id.
    # A recast is one re-pointed value; there is deliberately no history here.
    voiced_by_tag_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL")
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_character_model.py -q`
Expected: PASS

- [ ] **Step 5: Generate and fix the migration**

```bash
uv run --isolated alembic revision --autogenerate -m "character tags"
```

Then EDIT the generated file: no `app.db.models.UTCDateTime()` (there is no datetime here, so there should be none), no `import app.db.models`, `batch_alter_table` for the column add, and **no `drop_constraint`** — if autogenerate emitted one, delete it and report.

**Do not touch the `kind` column.** It is a bare `VARCHAR(9)` with no CHECK constraint (verified against the live schema), and `"character"` is nine characters like `"franchise"`, so a new enum value needs no schema change at all.

- [ ] **Step 6: Verify the round-trip**

```bash
uv run --isolated alembic upgrade head
uv run --isolated alembic downgrade -1
uv run --isolated alembic upgrade head
uv run --isolated pytest -q
```

- [ ] **Step 7: Commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/types.py src/app/db/models.py alembic/versions tests/test_character_model.py
git commit -m "feat: character tags and their seiyuu link"
```

---

## Task 2: Widen `parent_id`, and guard against cycles

**Files:**
- Modify: `src/app/db/service.py`, `src/app/web/routes/tags.py`
- Test: `tests/test_tag_parenting.py`

**Interfaces:**
- Consumes: `TagKind.CHARACTER` (Task 1).
- Produces: `async def would_create_tag_cycle(session, tag_id: int, parent_id: int) -> bool` in `db/service.py`.

`POST /tags` currently enforces *parent must be a FRANCHISE* and *only GROUP tags take a parent*. Both widen:

| child kind | permitted parent |
|---|---|
| GROUP | FRANCHISE, or GROUP (subunit) |
| CHARACTER | FRANCHISE |
| anything else | none |

- [ ] **Step 1: Write the failing test**

```python
"""parent_id after widening: subunits, characters, and no loops."""

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tag
from app.db.service import would_create_tag_cycle
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _chain(s, *names):
    """Create GROUP tags parented in a chain: names[0] is the root."""
    made = []
    for i, name in enumerate(names):
        tag = Tag(name=name, kind=TagKind.GROUP, slug=name,
                  parent_id=made[i - 1].id if i else None)
        s.add(tag)
        await s.flush()
        made.append(tag)
    return made


async def test_a_tag_may_not_be_its_own_parent(db):
    async with db() as s:
        (a,) = await _chain(s, "a")
        assert await would_create_tag_cycle(s, a.id, a.id) is True


async def test_a_tag_may_not_be_parented_to_its_own_descendant(db):
    """a > b > c. Making a's parent c would close the loop."""
    async with db() as s:
        a, b, c = await _chain(s, "a", "b", "c")
        assert await would_create_tag_cycle(s, a.id, c.id) is True


async def test_an_unrelated_parent_is_fine(db):
    async with db() as s:
        a, b = await _chain(s, "a", "b")
        other = Tag(name="other", kind=TagKind.GROUP, slug="other")
        s.add(other)
        await s.flush()
        assert await would_create_tag_cycle(s, other.id, b.id) is False


async def test_the_walk_terminates_on_pre_existing_bad_data(db):
    """If a loop somehow already exists in the table, the guard must return
    rather than spin forever -- a guard that hangs is worse than none."""
    async with db() as s:
        a, b = await _chain(s, "a", "b")
        a.parent_id = b.id          # a > b > a, written behind the guard's back
        await s.flush()
        other = Tag(name="other", kind=TagKind.GROUP, slug="other")
        s.add(other)
        await s.flush()
        assert await would_create_tag_cycle(s, other.id, a.id) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_parenting.py -q`
Expected: FAIL — `ImportError: cannot import name 'would_create_tag_cycle'`

- [ ] **Step 3: Implement the guard**

In `src/app/db/service.py`, in the tags section:

```python
async def would_create_tag_cycle(
    session: AsyncSession, tag_id: int, parent_id: int
) -> bool:
    """Would parenting `tag_id` to `parent_id` close a loop?

    GROUP -> GROUP made loops possible for the first time, and nothing in this
    codebase walks parent_id transitively -- so a cycle would not be noticed
    until something did, and then it would hang rather than fail. The guard
    belongs at the write boundary, which is the only place a loop can be
    created.

    The `seen` set is not belt-and-braces: it terminates the walk on data that
    is ALREADY looped (written before this guard existed, or by a direct DB
    edit), where following parents alone would spin forever.
    """
    if tag_id == parent_id:
        return True
    seen: set[int] = {tag_id}
    cursor: int | None = parent_id
    while cursor is not None:
        if cursor in seen:
            # Reaching tag_id means the proposed parent is BELOW us, so the
            # edge would close a loop. Reaching any other repeat means the
            # table already contains a loop that does not involve us -- not a
            # new cycle, but the reason the walk must stop rather than spin.
            return cursor == tag_id
        seen.add(cursor)
        cursor = await session.scalar(select(Tag.parent_id).where(Tag.id == cursor))
    return False
```

- [ ] **Step 4: Widen the route validation**

In `src/app/web/routes/tags.py`'s `create_tag`, replace the franchise-only check:

```python
    if parent_id:
        parent = await session.get(Tag, parent_id)
        if parent is None:
            raise HTTPException(status_code=422, detail="parent tag not found")
        # Widened 2026-08-01. Both shapes are the SAME meaning -- "the broader
        # thing I belong to" -- one rung deeper: a subunit belongs to its group
        # the way a group belongs to its franchise.
        allowed = {
            TagKind.GROUP: (TagKind.FRANCHISE, TagKind.GROUP),
            TagKind.CHARACTER: (TagKind.FRANCHISE,),
        }.get(kind, ())
        if parent.kind not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"a {kind.value} tag cannot have a {parent.kind.value} parent",
            )
```

The cycle guard cannot fire on creation (a brand-new tag has no descendants), so it is wired in wherever a parent is CHANGED. If no such route exists today, note that in your report and leave the guard unused-but-tested rather than inventing an edit surface — Task 7 adds the Tags-page control that needs it.

- [ ] **Step 5: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_tag_parenting.py -q
uv run --isolated pytest -q
```

- [ ] **Step 6: Commit**

```bash
uv run --isolated ruff check .
git add src/app/db/service.py src/app/web/routes/tags.py tests/test_tag_parenting.py
git commit -m "feat: subunit and character parents, with a cycle guard"
```

---

## Task 3: Attaching a character attaches its seiyuu

**Files:**
- Modify: `src/app/db/service.py` (`attach_tag`)
- Test: `tests/test_character_attach.py`

**Interfaces:**
- Consumes: `TagKind.CHARACTER`, `Tag.voiced_by_tag_id` (Task 1).
- Produces: `attach_tag`'s existing signature unchanged — `(session, concert_id, tag, expand=True) -> list[Tag]`. It now also returns any seiyuu it attached.

**This is the task the whole feature rests on.** Because `tracked_concert_ids` matches materialised `concert_tags` rows, attaching the seiyuu here is what makes following her match a character-credited concert — with no change to subscription code anywhere.

- [ ] **Step 1: Write the failing test**

```python
"""Attach-time chaining: character -> seiyuu, and group -> character -> seiyuu."""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Tag, TagMember
from app.db.service import attach_tag
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(s):
    """765PRO ALLSTARS containing two characters, each with a seiyuu."""
    concert = Concert(title="im@s live", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    nakamura = Tag(name="中村繪里子", kind=TagKind.ARTIST, slug="eriko-nakamura")
    s.add_all([concert, imai, nakamura])
    await s.flush()
    chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                  voiced_by_tag_id=imai.id)
    haruka = Tag(name="天海春香", kind=TagKind.CHARACTER, slug="haruka",
                 voiced_by_tag_id=nakamura.id)
    group = Tag(name="765PRO ALLSTARS", kind=TagKind.GROUP, slug="765pro")
    s.add_all([chihaya, haruka, group])
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=group.id, member_tag_id=chihaya.id),
        TagMember(group_tag_id=group.id, member_tag_id=haruka.id),
    ])
    await s.flush()
    return concert, group, chihaya, haruka, imai, nakamura


async def _attached(s, concert_id):
    return set((await s.execute(
        select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id)
    )).scalars())


async def test_attaching_a_character_attaches_her_seiyuu(db):
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        added = await attach_tag(s, concert.id, chihaya)
        assert imai.id in {t.id for t in added}, "the seiyuu must be RETURNED too"
        assert imai.id in await _attached(s, concert.id)


async def test_attaching_a_group_reaches_the_seiyuu_through_its_characters(db):
    """The chained step. Without it a group-credited show misses every seiyuu
    follower, which is the entire point of the feature."""
    async with db() as s:
        concert, group, chihaya, haruka, imai, nakamura = await _seed(s)
        await attach_tag(s, concert.id, group)
        got = await _attached(s, concert.id)
        assert {group.id, chihaya.id, haruka.id, imai.id, nakamura.id} <= got


async def test_attaching_a_seiyuu_never_attaches_her_characters(db):
    """Deliberately asymmetric -- she plays events with no im@s connection."""
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, imai)
        assert chihaya.id not in await _attached(s, concert.id)


async def test_the_seiyuu_is_attached_even_when_expansion_is_off(db):
    """expand=False exists so the creation form's explicit artist list is not
    overridden. Attaching the seiyuu overrides nothing -- it is a consequence
    of the character being present, and without it a concert created through
    that form would never match her followers."""
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, chihaya, expand=False)
        assert imai.id in await _attached(s, concert.id)


async def test_a_character_with_no_seiyuu_attaches_cleanly(db):
    async with db() as s:
        concert, *_ = await _seed(s)
        orphan = Tag(name="???", kind=TagKind.CHARACTER, slug="orphan")
        s.add(orphan)
        await s.flush()
        added = await attach_tag(s, concert.id, orphan)
        assert [t.id for t in added] == [orphan.id]


async def test_an_already_attached_seiyuu_is_not_added_twice(db):
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, imai)
        added = await attach_tag(s, concert.id, chihaya)
        assert imai.id not in {t.id for t in added}
        assert len(await _attached(s, concert.id)) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_character_attach.py -q`
Expected: FAIL — the seiyuu is not attached.

- [ ] **Step 3: Extend `attach_tag`**

Replace the body of `attach_tag` in `src/app/db/service.py`:

```python
    added: list[Tag] = []
    if not await _is_attached(session, concert_id, tag.id):
        session.add(ConcertTag(concert_id=concert_id, tag_id=tag.id))
        added.append(tag)
        if expand and tag.kind is TagKind.GROUP:
            for member in await group_members(session, tag.id):
                if not await _is_attached(session, concert_id, member.id):
                    session.add(ConcertTag(concert_id=concert_id, tag_id=member.id))
                    added.append(member)

    # THE CHAINED STEP. Every character now attached pulls in its seiyuu.
    # Without it a group-credited im@s show materialises characters only, and
    # tracked_concert_ids -- which matches materialised rows -- never matches
    # anyone following the performer. That is the whole feature.
    #
    # Bounded by construction, and NOT the nested-groups rule returning: a
    # seiyuu is an ARTIST, so group -> character -> seiyuu terminates in two
    # steps and cannot recurse.
    #
    # Deliberately NOT gated on `expand`. That flag exists so the creation
    # form's explicit artist list is not overridden; attaching the seiyuu
    # overrides nothing, and gating it would leave concerts made on that form
    # unmatched for her followers.
    seiyuu_ids = {
        t.voiced_by_tag_id for t in added
        if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id is not None
    }
    for seiyuu_id in sorted(seiyuu_ids):
        if not await _is_attached(session, concert_id, seiyuu_id):
            seiyuu = await session.get(Tag, seiyuu_id)
            if seiyuu is not None:
                session.add(ConcertTag(concert_id=concert_id, tag_id=seiyuu.id))
                added.append(seiyuu)

    await session.flush()
    return added
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_character_attach.py -q
uv run --isolated pytest -q
```

The full suite matters here: `attach_tag` is on the create, edit, import and duplicate paths.

- [ ] **Step 5: Mutation-verify the chained step**

Delete the `seiyuu_ids` block, confirm `test_attaching_a_group_reaches_the_seiyuu_through_its_characters` fails, restore it. Record what you saw.

- [ ] **Step 6: Commit**

```bash
uv run --isolated ruff check .
git add src/app/db/service.py tests/test_character_attach.py
git commit -m "feat: attaching a character attaches its seiyuu"
```

---

## Task 4: Pruning a character prunes its seiyuu

**Files:**
- Modify: `src/app/db/service.py` (`detach_tag`)
- Test: `tests/test_character_prune.py`

**Interfaces:**
- Consumes: Task 1 and Task 3.
- Produces: `detach_tag`'s existing signature unchanged — `(session, concert_id, tag_id) -> None`.

**The refinement is load-bearing:** a seiyuu can voice two characters on one bill, and detaching her because one was pruned would silently drop the other's performer.

- [ ] **Step 1: Write the failing test**

```python
"""Pruning a character takes its seiyuu -- unless someone else still needs her."""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Tag
from app.db.service import attach_tag, detach_tag
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _attached(s, concert_id):
    return set((await s.execute(
        select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id)
    )).scalars())


async def _two_roles(s):
    """One seiyuu voicing TWO characters -- the case the refinement exists for."""
    concert = Concert(title="im@s", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    s.add_all([concert, imai])
    await s.flush()
    a = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
            voiced_by_tag_id=imai.id)
    b = Tag(name="別の役", kind=TagKind.CHARACTER, slug="other-role",
            voiced_by_tag_id=imai.id)
    s.add_all([a, b])
    await s.flush()
    return concert, imai, a, b


async def test_pruning_a_character_detaches_her_seiyuu(db):
    async with db() as s:
        concert, imai, a, _b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await detach_tag(s, concert.id, a.id)
        assert await _attached(s, concert.id) == set()


async def test_the_seiyuu_stays_when_another_character_still_needs_her(db):
    """Two roles, one voice. Pruning one must not remove the other's performer."""
    async with db() as s:
        concert, imai, a, b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await attach_tag(s, concert.id, b)
        await detach_tag(s, concert.id, a.id)
        got = await _attached(s, concert.id)
        assert b.id in got
        assert imai.id in got, "the surviving character still needs her"


async def test_pruning_an_artist_touches_nothing_else(db):
    async with db() as s:
        concert, imai, a, _b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await detach_tag(s, concert.id, imai.id)
        assert await _attached(s, concert.id) == {a.id}


async def test_pruning_a_character_with_no_seiyuu_is_a_plain_detach(db):
    async with db() as s:
        concert, *_ = await _two_roles(s)
        orphan = Tag(name="???", kind=TagKind.CHARACTER, slug="orphan")
        s.add(orphan)
        await s.flush()
        await attach_tag(s, concert.id, orphan)
        await detach_tag(s, concert.id, orphan.id)
        assert await _attached(s, concert.id) == set()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_character_prune.py -q`
Expected: FAIL — the seiyuu stays attached.

- [ ] **Step 3: Extend `detach_tag`**

```python
async def detach_tag(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    """Remove a tag from a concert -- and, for a CHARACTER, her seiyuu with her.

    Owner rule (2026-08-01), with one refinement that is load-bearing: the
    seiyuu goes ONLY IF no other still-attached character shares her. A seiyuu
    can voice two characters on one bill, and detaching her because one was
    pruned would silently drop the other's performer.

    KNOWN EDGE, accepted rather than solved: concert_tags does not record WHY a
    tag was attached -- group expansion has had that blind spot since it
    shipped -- so a seiyuu who was ALSO there in her own right is removed when
    the character is pruned, and the editor re-adds her. Building provenance to
    fix that would touch every attach path for a rare case.
    """
    tag = await session.get(Tag, tag_id)
    await _detach_one(session, concert_id, tag_id)

    if tag is None or tag.kind is not TagKind.CHARACTER or tag.voiced_by_tag_id is None:
        await session.flush()
        return

    still_needed = await session.scalar(
        select(func.count())
        .select_from(ConcertTag)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(
            ConcertTag.concert_id == concert_id,
            Tag.kind == TagKind.CHARACTER,
            Tag.voiced_by_tag_id == tag.voiced_by_tag_id,
        )
    )
    if not still_needed:
        await _detach_one(session, concert_id, tag.voiced_by_tag_id)
    await session.flush()


async def _detach_one(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    """The single-row delete detach_tag used to be."""
    row = (await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_character_prune.py -q
uv run --isolated pytest -q
```

- [ ] **Step 5: Mutation-verify the refinement**

Remove the `still_needed` check so the seiyuu is always detached, confirm `test_the_seiyuu_stays_when_another_character_still_needs_her` fails, restore. Record what you saw.

- [ ] **Step 6: Commit**

```bash
uv run --isolated ruff check .
git add src/app/db/service.py tests/test_character_prune.py
git commit -m "feat: pruning a character prunes its seiyuu"
```

---

## Task 5: Display — clusters, the split pill, and the subunit rail

**Files:**
- Modify: `src/app/db/service.py` (`PerformerCluster`, `performer_clusters`)
- Modify: `src/app/web/templates/concert_detail.html`
- Modify: `src/app/web/static/style.css`
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_performer_display.py`

**Interfaces:**
- Consumes: Tasks 1, 3.
- Produces: `PerformerEntry` (frozen dataclass: `tag: Tag`, `seiyuu: Tag | None = None`); `PerformerCluster` gains `depth: int = 0` and renames `artists` to `performers: tuple[PerformerEntry, ...]`.

**Service and template land in ONE task** because renaming `artists` breaks the template — a commit with only one half is a broken page.

- [ ] **Step 1: Write the failing test**

```python
"""The Performing panel: pairing, nesting, and the standalone seiyuu."""

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, Tag, TagMember
from app.db.service import attach_tag, performer_clusters
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _reload(s, concert_id):
    from sqlalchemy import select
    return (await s.execute(
        select(Concert).where(Concert.id == concert_id)
        .options(selectinload(Concert.tags))
    )).scalar_one()


async def _imas(s):
    concert = Concert(title="im@s", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    s.add_all([concert, imai])
    await s.flush()
    chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                  voiced_by_tag_id=imai.id)
    parent = Tag(name="765PRO ALLSTARS", kind=TagKind.GROUP, slug="765pro")
    s.add_all([chihaya, parent])
    await s.flush()
    sub = Tag(name="竜宮小町", kind=TagKind.GROUP, slug="ryuguu", parent_id=parent.id)
    s.add(sub)
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=chihaya.id),
        TagMember(group_tag_id=sub.id, member_tag_id=chihaya.id),
    ])
    await s.flush()
    return concert, parent, sub, chihaya, imai


async def test_a_character_and_her_seiyuu_pair_into_one_entry(db):
    async with db() as s:
        concert, parent, _sub, chihaya, imai = await _imas(s)
        await attach_tag(s, concert.id, parent)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        entries = [e for c in clusters for e in c.performers]
        paired = [e for e in entries if e.seiyuu is not None]
        assert [(e.tag.id, e.seiyuu.id) for e in paired] == [(chihaya.id, imai.id)]
        assert imai.id not in [e.tag.id for e in entries], \
            "the seiyuu must not ALSO appear as her own entry"


async def test_a_seiyuu_attached_by_herself_is_listed_as_herself(db):
    """Owner rule: not under the group, just herself."""
    async with db() as s:
        concert, _p, _sub, _chihaya, imai = await _imas(s)
        await attach_tag(s, concert.id, imai)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert [c.group for c in clusters] == [None], "trailer only"
        assert [e.tag.id for e in clusters[0].performers] == [imai.id]
        assert clusters[0].performers[0].seiyuu is None


async def test_a_subunit_nests_under_its_parent_when_both_are_attached(db):
    async with db() as s:
        concert, parent, sub, _c, _i = await _imas(s)
        await attach_tag(s, concert.id, parent)
        await attach_tag(s, concert.id, sub)
        clusters = [c for c in await performer_clusters(s, await _reload(s, concert.id))
                    if c.group is not None]
        assert [(c.group.id, c.depth) for c in clusters] == [(parent.id, 0), (sub.id, 1)]


async def test_a_subunit_alone_renders_like_an_ordinary_group(db):
    """Owner rule: no parent attached, no nesting."""
    async with db() as s:
        concert, _parent, sub, _c, _i = await _imas(s)
        await attach_tag(s, concert.id, sub)
        clusters = [c for c in await performer_clusters(s, await _reload(s, concert.id))
                    if c.group is not None]
        assert [(c.group.id, c.depth) for c in clusters] == [(sub.id, 0)]


async def test_a_character_whose_seiyuu_is_not_attached_is_a_plain_entry(db):
    async with db() as s:
        concert, _p, _sub, chihaya, imai = await _imas(s)
        chihaya.voiced_by_tag_id = None      # nobody to pair with
        await s.flush()
        await attach_tag(s, concert.id, chihaya)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        entry = clusters[0].performers[0]
        assert entry.tag.id == chihaya.id and entry.seiyuu is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_performer_display.py -q`
Expected: FAIL — `AttributeError: 'PerformerCluster' object has no attribute 'performers'`

- [ ] **Step 3: Rework the service side**

Replace `PerformerCluster` and `performer_clusters` in `src/app/db/service.py`. **Keep the existing docstring's three reasons verbatim** — service-side because `Tag.members` is a lazy m2m and touching it during async template rendering raises `MissingGreenlet` (a 500 this project has shipped once); `group_members()` deliberately not reused because it is per-group; a performer in two attached groups appears under BOTH per the 2026-07-27 ruling — and add the new ones.

```python
@dataclass(frozen=True)
class PerformerEntry:
    """One chip. `seiyuu` is set ONLY when the tag is a CHARACTER and her voice
    actor is ALSO attached to this concert -- the both-ends rule. Otherwise it
    is None and the chip renders plain, which is what makes a lone character
    and a lone artist look identical, deliberately."""

    tag: Tag
    seiyuu: Tag | None = None


@dataclass(frozen=True)
class PerformerCluster:
    """One labelled row of the Performing panel. `group is None` is the
    trailing cluster of performers in no attached group. `depth` is 1 when this
    group's DIRECT parent is also attached to this concert -- a subunit with no
    parent present is an ordinary top-level cluster (owner rule)."""

    group: Tag | None
    performers: tuple[PerformerEntry, ...] = ()
    depth: int = 0
```

`performer_clusters` then:

```python
    groups = [t for t in concert.tags if t.kind is TagKind.GROUP]
    people = [t for t in concert.tags
              if t.kind in (TagKind.ARTIST, TagKind.CHARACTER)]
    attached_ids = {t.id for t in concert.tags}

    # Pair each character with her seiyuu, but only when BOTH ends are here.
    # The seiyuu is then dropped from the standalone list: she is rendered
    # inside the split pill. A seiyuu attached in her own right survives this
    # filter and is listed as herself (owner rule) -- and she reaches the
    # trailer for free, because a group's members are CHARACTER tags now, so
    # she is not in members_by_group at all.
    by_id = {t.id: t for t in concert.tags}
    paired_seiyuu: set[int] = {
        t.voiced_by_tag_id for t in people
        if t.kind is TagKind.CHARACTER
        and t.voiced_by_tag_id is not None
        and t.voiced_by_tag_id in attached_ids
    }
    entries = [
        PerformerEntry(t, by_id.get(t.voiced_by_tag_id)
                          if t.kind is TagKind.CHARACTER
                          and t.voiced_by_tag_id in attached_ids else None)
        for t in people if t.id not in paired_seiyuu
    ]

    if not groups:
        return [PerformerCluster(None, tuple(entries))] if entries else []
```

Then the existing single batched `tag_members` query, unchanged, building `members_by_group`. Build one cluster per group from `entries` whose `tag.id` is in that group's member set, and a trailer from entries in no attached group's member set — the same shape as today, with `PerformerEntry` in place of `Tag`.

Finally, ordering and depth. **No extra query**: `attached_ids` already tells you whether a group's parent is present.

```python
    # Parent-first ordering with depth. A group whose parent_id is not attached
    # is a root here, which is exactly the owner's rule that a subunit alone
    # renders like an ordinary group. Direct parent only.
    def _depth(g: Tag) -> int:
        return 1 if g.parent_id in attached_ids else 0

    roots = [c for c in built if c.group is not None and _depth(c.group) == 0]
    ordered: list[PerformerCluster] = []
    for root in roots:
        ordered.append(root)
        ordered.extend(
            c for c in built
            if c.group is not None and c.group.parent_id == root.group.id
        )
```

Append the trailer (`group is None`) last, and set each nested cluster's `depth` to 1 as you emit it.

- [ ] **Step 4: Update the template and CSS**

In `concert_detail.html`, the cluster loop becomes `cluster.performers` and each entry renders either a plain chip (`entry.seiyuu is None`) or the split pill. Keep the existing comment about why an empty `.chiprow` is omitted — it records a measured 5.6px of dead space and is still true.

Add a `depth` class so the rail can be styled, and the `Subunit` label — **which is user-facing and needs `_()`**.

In `style.css`, beside the existing `.chip` rules:

```css
/* The character/seiyuu split pill. ONE chip visibly made of two halves, each
   its own link -- the shape was chosen (owner, from four mockups) because the
   merge is CONDITIONAL: when only one end is attached the chip is plain, and
   the split makes that difference read as meaningful rather than as
   inconsistent styling. 999px like every other chip. */
.mchip { display: inline-flex; align-items: stretch; border-radius: 999px;
         overflow: hidden; background: var(--chip); font-size: .82rem; }
.mchip > * { padding: .1rem .55rem; color: var(--ink); text-decoration: none; }
.mchip > .cv { color: var(--dim); border-left: 1px solid rgba(27, 27, 32, .14); }

/* A subunit sits beneath its parent. Indent + rail, NOT a nested card: the
   two-shape callout grammar has no third shape and this is not a callout. */
.pcluster.sub { margin-left: 1rem; padding-left: .85rem; border-left: 2px solid var(--line); }
```

- [ ] **Step 5: Fill both catalogues**

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill the new msgid by hand in both `.po` files, **drop any `#, fuzzy` flag**, then delete `messages.pot` (gitignored). Verify by resolving the string in Python rather than reading the file — this console is GBK and will mangle it.

- [ ] **Step 6: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_performer_display.py -q
uv run --isolated pytest -q
```

`tests/test_i18n_catalogues.py` and the concert-page render tests are the ones most likely to catch a mistake here.

- [ ] **Step 7: Commit**

```bash
uv run --isolated ruff check .
git add -A
git commit -m "feat: split-pill chips and nested subunit clusters"
```

---

## Task 6: The catalogue round-trip carries `voiced_by`

**Files:**
- Modify: `src/app/domain/tags_yaml.py`, `src/app/domain/tags_diff.py`, `src/app/db/service.py`
- Test: `tests/test_tags_yaml.py`, `tests/test_tags_diff.py` (existing files)

**Interfaces:**
- Consumes: Task 1.
- Produces: `voiced_by` on `TagExport` and `ParsedTag`; `COMPARABLE_FIELDS` at 12.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags_diff.py`:

```python
def test_voiced_by_is_compared_like_any_other_field():
    plan = plan_tag_import(
        [_incoming(voiced_by="asami-imai")], [_current(voiced_by=None)]
    )
    assert plan.tags[0].fills == {"voiced_by": "asami-imai"}


def test_the_comparable_field_count_moved_to_twelve():
    """This pin exists so a field cannot join the FORMAT while the differ
    silently skips it -- which would make the reformat look successful and
    quietly drop every seiyuu link."""
    assert len(COMPARABLE_FIELDS) == 12
    assert "voiced_by" in COMPARABLE_FIELDS
```

Add to `tests/test_tags_yaml.py` (match the file's existing helper style):

```python
def test_a_character_round_trips_with_its_seiyuu():
    text = tags_to_yaml([TagExport(
        handle="chihaya", name="如月千早", kind="character", voiced_by="asami-imai",
    )])
    assert "voiced_by: asami-imai" in text
    (parsed,) = parse_tags(text).tags
    assert parsed.voiced_by == "asami-imai"
    assert parsed.kind is TagKind.CHARACTER


def test_voiced_by_is_omitted_when_unset():
    """The exporter omits empty fields -- a human reads these files, and
    `voiced_by: null` on every artist is noise."""
    text = tags_to_yaml([TagExport(handle="imai", name="今井麻美", kind="artist")])
    assert "voiced_by" not in text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run --isolated pytest tests/test_tags_diff.py tests/test_tags_yaml.py -q
```
Expected: FAIL — `TypeError: unexpected keyword argument 'voiced_by'`

- [ ] **Step 3: Thread the field through**

- `tags_yaml.py`: add `"voiced_by"` to `_TAG_KEYS`; add `voiced_by: str | None = None` to both `TagExport` and `ParsedTag`; emit it in `tags_to_yaml`'s omit-empty loop; parse it in `parse_tags` with `slug_core` normalisation, exactly as `parent` is handled — it is a HANDLE, not a name.
- `tags_diff.py`: add `"voiced_by"` to `COMPARABLE_FIELDS`.
- `db/service.py`: `current_tag_exports` must emit the seiyuu's **handle**, not its id — resolve it in the same batched pass that resolves `parent`. And `apply_tag_import` must resolve an incoming handle back to a tag id, in its second pass, where `parent` and members are already resolved for exactly this reason.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run --isolated pytest tests/test_tags_diff.py tests/test_tags_yaml.py -q
uv run --isolated pytest -q
```

`tests/test_catalogue_export.py`'s round-trip test is the one that proves the whole loop; make sure it still passes without being edited.

- [ ] **Step 5: Commit**

```bash
uv run --isolated ruff check .
git add -A
git commit -m "feat: voiced_by through the catalogue round-trip"
```

---

## Task 7: The Tags page — create, edit, and browse characters

**Files:**
- Modify: `src/app/web/routes/tags.py`, `src/app/web/templates/tags.html`
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_tags_character_ui.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 6.
- Produces: `voiced_by_tag_id` settable on create and edit; the cycle guard wired to whichever route can change a parent.

- [ ] **Step 1: Write the failing test**

Copy the `db` / `client` / `login_as` fixtures verbatim from `tests/test_admin_discoveries.py` (they are the project's standard shape), then:

```python
EDITOR_ID = 77


async def _artist(client, name="今井麻美"):
    from sqlalchemy import select
    from app.db.models import Tag
    client.post("/tags", data={"name": name, "kind": "artist"})
    async with client.db() as s:
        return (await s.execute(select(Tag).where(Tag.name == name))).scalar_one()


async def test_an_editor_creates_a_character_with_a_seiyuu(client):
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    r = client.post("/tags", data={
        "name": "如月千早", "kind": "character", "voiced_by_tag_id": imai.id,
    })
    assert r.status_code in (200, 303)
    async with client.db() as s:
        from sqlalchemy import select
        from app.db.models import Tag
        row = (await s.execute(select(Tag).where(Tag.name == "如月千早"))).scalar_one()
        assert row.voiced_by_tag_id == imai.id


async def test_a_recast_repoints_the_link(client):
    """The whole of recast handling: one value changes."""
    login_as(client, EDITOR_ID, "editor")
    old = await _artist(client, "今井麻美")
    new = await _artist(client, "別の声優")
    client.post("/tags", data={
        "name": "如月千早", "kind": "character", "voiced_by_tag_id": old.id,
    })
    async with client.db() as s:
        from sqlalchemy import select
        from app.db.models import Tag
        chihaya = (await s.execute(select(Tag).where(Tag.name == "如月千早"))).scalar_one()
    client.post(f"/tags/{chihaya.id}/edit", data={"voiced_by_tag_id": new.id})
    async with client.db() as s:
        from sqlalchemy import select
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).voiced_by_tag_id == new.id


async def test_the_page_shows_a_character_with_her_seiyuu(client):
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    client.post("/tags", data={
        "name": "如月千早", "kind": "character", "voiced_by_tag_id": imai.id,
    })
    body = client.get("/tags").text
    assert "如月千早" in body and "今井麻美" in body


async def test_a_character_may_not_be_parented_to_an_artist(client):
    """parent_id means 'the broader thing I belong to'. A seiyuu is not that,
    which is exactly why voiced_by_tag_id is a separate column."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    r = client.post("/tags", data={
        "name": "如月千早", "kind": "character", "parent_id": imai.id,
    })
    assert r.status_code == 422


async def test_a_group_may_be_parented_to_a_group(client):
    login_as(client, EDITOR_ID, "editor")
    client.post("/tags", data={"name": "765PRO ALLSTARS", "kind": "group"})
    async with client.db() as s:
        from sqlalchemy import select
        from app.db.models import Tag
        parent = (await s.execute(
            select(Tag).where(Tag.name == "765PRO ALLSTARS")
        )).scalar_one()
    r = client.post("/tags", data={
        "name": "竜宮小町", "kind": "group", "parent_id": parent.id,
    })
    assert r.status_code in (200, 303)
    async with client.db() as s:
        from sqlalchemy import select
        from app.db.models import Tag
        sub = (await s.execute(select(Tag).where(Tag.name == "竜宮小町"))).scalar_one()
        assert sub.parent_id == parent.id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tags_character_ui.py -q`

- [ ] **Step 3: Implement**

`create_tag` and `edit_tag` accept `voiced_by_tag_id: int = Form(0)`, validated to be an existing ARTIST tag (422 otherwise) and only meaningful on a CHARACTER. `edit_tag`'s docstring already says omitted fields leave stored values ALONE — keep that rule for this one too.

Wire `would_create_tag_cycle` into whichever route can change a parent; if `edit_tag` gains parent editing here, that is where it goes.

`tags.html` gains a CHARACTER section following the existing kind sections, each row naming its seiyuu, and the create/edit dialogs gain the picker (kind-scoped like the venue and franchise fields already are).

- [ ] **Step 4: Fill both catalogues**

Same procedure as Task 5, Step 5. Verify resolved strings in Python.

- [ ] **Step 5: Run and commit**

```bash
uv run --isolated pytest -q
uv run --isolated ruff check .
git add -A
git commit -m "feat: create and edit character tags on the Tags page"
```

---

## Task 8: Documentation

**Files:**
- Modify: `CLAUDE.md`, `WISHLIST.md`, `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to invariant 3 (group tag expansion) that expansion now chains one fixed step — group → character → seiyuu — and why that is not the nested-groups rule returning. Add `voiced_by_tag_id` beside the venue-tag notes with its SET-NULL reasoning and why it is not `parent_id`. Record the prune rule and its known provenance edge. Record the both-ends display rule. Note in the UI conventions that a subunit nests only when its parent is attached, and that a seiyuu attached in her own right is listed as herself.

- [ ] **Step 2: Update WISHLIST.md**

Move the entry to Shipped with today's date and do the full revision pass the project convention requires — re-rank every remaining entry and state which are unaffected. Note explicitly whether the `triage-leads` skill entry changes (it does: an im@s lead now needs character handling in the draft).

- [ ] **Step 3: Update the README test count**

```bash
uv run --isolated pytest -q 2>&1 | tail -3
```
Use the real number.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md WISHLIST.md README.md
git commit -m "docs: record character tags, seiyuu links and subunits"
```

---

## Task 9: Verify the whole chain against a real scenario

**Files:** none — a manual verification gate, no commit.

Every task above tests its own layer. This proves the layers connect, which is the claim the feature actually makes.

- [ ] **Step 1: Build the scenario in a scratch DB**

Create: a seiyuu, a character she voices, a group containing the character, a user following ONLY the seiyuu, and a concert with the group attached.

- [ ] **Step 2: Assert the promise**

`tracked_concert_ids` for that user must contain the concert. That is the entire feature in one assertion: a group-credited im@s show reaching someone who follows the performer, through a character she has never heard of.

- [ ] **Step 3: Report, do not commit**

Report what you saw. If the concert is NOT tracked, the chain is broken somewhere between Task 3 and the subscription layer — say where, rather than adjusting the test.

---

## Self-Review Notes

**Spec coverage.** `TagKind.CHARACTER` and `voiced_by_tag_id` (Task 1); `parent_id` widening and the cycle guard (Task 2); attach chaining, its asymmetry, and the `expand=False` carve-out (Task 3); the prune rule with its shared-seiyuu refinement (Task 4); both display rules, the standalone-seiyuu rule and the chosen shapes (Task 5); the round-trip with `COMPARABLE_FIELDS` 11 → 12 (Task 6); the editor surface (Task 7); docs (Task 8); an end-to-end gate (Task 9).

**Deliberately not built, carried from the spec:** recast history, provenance on `concert_tags`, nested membership, automatic subunit attachment, and any path to change an existing tag's kind — the reformat needs none of these, because seiyuu stay artists and characters are new tags.

**Type consistency:** `PerformerCluster.artists` becomes `PerformerCluster.performers: tuple[PerformerEntry, ...]`, renamed rather than extended because it now holds characters too; service and template change in the same task so no commit leaves the page broken. `voiced_by` is a HANDLE everywhere in the file format and an id everywhere in the ORM — the conversion happens in `current_tag_exports` and `apply_tag_import`, the same two places `parent` is converted.
