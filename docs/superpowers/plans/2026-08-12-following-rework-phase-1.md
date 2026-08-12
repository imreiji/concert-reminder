# Following Rework — Phase 1: search and queries

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tag search find tags by the name it shows them under, make a filtered list look like a result list, and delete a 65-query N+1 that runs on two pages.

**Architecture:** Three independent changes to existing surfaces. Nothing is added, no page is restructured, no route changes shape. This is the phase the rest of the Following rework rests on: the spec removes per-group folds and makes search the only way to reach a name inside a 99-member group, which is not a safe thing to do while search is broken for 93% of the catalogue.

**Tech Stack:** Jinja2 templates, one Jinja global in `web/app.py`, vanilla JS in `base.html`, SQLAlchemy async.

**Spec:** `docs/superpowers/specs/2026-08-12-following-rework-design.md` — this plan is its §Suggested sequencing step 1. Phases 2-4 (`/tags` as the follow surface, `/following`, the Preferences reduction) get their own plans and MUST NOT be started here.

## Global Constraints

- Run everything from the repo root `E:\click clack clan\concert-reminder`.
- **Always `uv run --isolated`** — an external process holds a lock on `.venv`; never `uv sync`.
- Run test commands in the **FOREGROUND** with `timeout: 900000`. The suite takes 6-10 minutes.
- **Tests and lint must pass before any commit:** `uv run --isolated pytest -q` and `uv run --isolated ruff check .`
- Sentence case everywhere.
- **`data-name` is `filterChips()`'s hook and its only sanctioned job on these pages.** Do not reuse it for anything else; `data-tag-name` / `data-preset-name` exist precisely because they must not collide with it.
- **Never interpolate user-controlled text into inline `on*` handlers** (invariant 7). Tag names are user-controlled.
- **Adding a name to a module in `db/` means adding it to `db/service.py` too** — `tests/test_service_facade.py` fails otherwise, and it is testing the architecture seam, not tidiness.
- A feature module in `db/` must NEVER import the facade.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```

## File Structure

| File | Responsibility in this phase |
|---|---|
| `src/app/web/app.py` | gains one Jinja global, `search_key`, beside the existing `loc` |
| `src/app/web/templates/*.html` | nine `data-name` sites switch to it |
| `src/app/web/templates/base.html` | `filterChips` learns to hide containers that empty |
| `src/app/db/tags.py` | gains `members_by_group`, the batched replacement for the N+1 |
| `src/app/db/service.py` | re-exports it |
| `src/app/web/routes/tags.py`, `routes/preferences.py` | call the batched version |

---

### Task 1: `search_key` — find a tag by any of its three names

**Files:**
- Modify: `src/app/web/app.py` (near line 137, beside the `loc` global)
- Modify: `src/app/web/templates/tags.html:216`, `:219`, `:315`
- Modify: `src/app/web/templates/preferences.html:117`, `:125`
- Modify: `src/app/web/templates/welcome.html:31`, `:40`
- Modify: `src/app/web/templates/discover.html:202`
- Modify: `src/app/web/templates/_tag_picker_script.html:31`
- Test: `tests/test_tag_search_key.py` (new)

**Interfaces:**
- Produces: Jinja global `search_key(obj) -> str`. Later phases use it for split pills, where one pill carries both tags' keys.

**Why:** `data-name` carries `t.name` (the Japanese name) while the chip displays `loc(t, "name")` (the viewer's locale). **681 of 735 live tags have a `name_en` that differs from `name`** — an English viewer sees `Aina Aiba`, types "Aiba", and matches nothing because `data-name` is `相羽あいな`. This is live today on five surfaces.

**The precedent to copy:** `_round_phrase_dialog.html:31` already does exactly this for phrases —
`data-name="{{ (p.label ~ ' ' ~ p.label_en ~ ' ' ~ p.label_zh) | lower }}"`. This task generalises it into a helper rather than repeating the expression nine times.

**The trap:** `Tag.name_en` and `Tag.name_zh` are `str | None`, and **109 live tags have no `name_zh`**. Jinja renders `None` as the string `"None"`, so a naive concatenation makes those 109 tags findable by typing "none" and puts a junk token in every key. The helper must drop empties.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tag_search_key.py`:

```python
"""A tag must be findable by whatever name the viewer is shown.

`data-name` is filterChips()'s hook. It carried `Tag.name` (Japanese) while the
chip rendered `loc(t, "name")` (the viewer's locale), so on the live catalogue
681 of 735 tags could not be found by the name they displayed.
"""

from app.web.app import search_key


class _T:
    def __init__(self, name, name_en=None, name_zh=None):
        self.name = name
        self.name_en = name_en
        self.name_zh = name_zh


def test_search_key_joins_all_three_names_lowercased():
    key = search_key(_T("相羽あいな", "Aina Aiba", "相羽爱菜"))
    assert "相羽あいな" in key
    assert "aina aiba" in key, "an EN viewer types what the chip shows them"
    assert "相羽爱菜" in key


def test_search_key_drops_missing_names_rather_than_writing_none():
    """109 live tags have no name_zh. Jinja renders None as 'None', which would
    both add a junk token and make every one of them match a search for 'none'.

    Mutation this must fail against: joining the fields without filtering, e.g.
    f"{o.name} {o.name_en} {o.name_zh}".lower().
    """
    key = search_key(_T("蓮ノ空", "Hasunosora", None))
    assert "none" not in key
    assert key == "蓮ノ空 hasunosora"


def test_search_key_tolerates_an_object_without_the_optional_fields():
    """Not every object rendered through a chip is a Tag -- Discover's region
    links carry a bare `.name`. The helper must not raise on them."""
    class _Region:
        name = "Kanto"
    assert search_key(_Region()) == "kanto"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_search_key.py -q`
Expected: FAIL — `ImportError: cannot import name 'search_key' from 'app.web.app'`.

- [ ] **Step 3: Add the helper and register it**

In `src/app/web/app.py`, immediately above the line that registers `loc`
(`templates.env.globals["loc"] = ...`, around line 137), add:

```python
def search_key(obj) -> str:
    """Every name this object can be displayed under, lowercased and joined.

    `data-name` is filterChips()'s hook, and the chip beside it renders
    `loc(obj, "name")` -- the viewer's locale. Keying the hook on `name` alone
    meant an English viewer could not find a tag by the name they were looking
    at: 681 of 735 live tags have a name_en that differs from name.

    Empties are dropped rather than joined: name_en/name_zh are nullable (109
    live tags have no name_zh) and Jinja renders None as "None", which would
    make all of them match a search for "none".

    Takes any object with a `.name`, not just a Tag -- Discover's region links
    carry a bare name and must not raise here.
    """
    parts = (obj.name, getattr(obj, "name_en", None), getattr(obj, "name_zh", None))
    return " ".join(p for p in parts if p).lower()
```

Then register it beside `loc`:

```python
templates.env.globals["search_key"] = search_key
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --isolated pytest tests/test_tag_search_key.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Verify the test catches its named mutation**

Temporarily change the helper's last line to
`return f"{obj.name} {getattr(obj, 'name_en', None)} {getattr(obj, 'name_zh', None)}".lower()`
and re-run. `test_search_key_drops_missing_names_rather_than_writing_none` must
FAIL. Restore. If it passes, stop and report — the test is not doing its job.

- [ ] **Step 6: Switch the nine Tag sites to the helper**

Each of these currently reads `data-name="{{ t.name | lower }}"` (or `r.name`).
Replace the attribute value only; change nothing else on the line.

| File | Line | Was | Becomes |
|---|---|---|---|
| `tags.html` | 216 | `data-name="{{ t.name \| lower }}"` | `data-name="{{ search_key(t) }}"` |
| `tags.html` | 219 | same | `data-name="{{ search_key(t) }}"` |
| `tags.html` | 315 | same | `data-name="{{ search_key(t) }}"` |
| `preferences.html` | 117 | same | `data-name="{{ search_key(t) }}"` |
| `preferences.html` | 125 | same | `data-name="{{ search_key(t) }}"` |
| `welcome.html` | 31 | same | `data-name="{{ search_key(t) }}"` |
| `welcome.html` | 40 | same | `data-name="{{ search_key(t) }}"` |
| `discover.html` | 202 | same | `data-name="{{ search_key(t) }}"` |
| `_tag_picker_script.html` | 31 | same | `data-name="{{ search_key(t) }}"` |

**Leave `discover.html:220` alone.** That one iterates `region_links`, whose
items are not Tags — they carry `.name`, `.ids`, `.href`, `.count`, `.active`
and no localised names. `search_key` would work on it but adds nothing; changing
it is out of scope for this task.

- [ ] **Step 7: Write a render test proving it reaches the page**

Add to `tests/test_tag_search_key.py`. Read `tests/test_tags.py` first for the
`client` fixture, its `login_as` helper and `EDITOR_ID`, and match that style:

```python
async def test_tags_page_data_name_carries_the_english_name(client):
    """The unit test above proves the helper. This proves it is WIRED -- the
    mutation being a template left on `t.name | lower`, which no unit test can
    see."""
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        s.add(Tag(name="相羽あいな", name_en="Aina Aiba", name_zh="相羽爱菜",
                  kind=TagKind.ARTIST, slug="aina-aiba"))
        await s.commit()
    r = client.get("/tags")
    assert "aina aiba" in r.text, "findable by the name an EN viewer is shown"
```

Import what it needs (`Tag`, `TagKind`, and the fixture helpers) the same way
`tests/test_tags.py` does.

- [ ] **Step 8: Run the full suite and lint**

Run: `uv run --isolated pytest -q` (foreground, timeout 900000)
Then: `uv run --isolated ruff check .`
Expected: all pass. Note the baseline count before you start so you can state
the delta.

- [ ] **Step 9: Commit**

```bash
git add src/app/web/app.py src/app/web/templates tests/test_tag_search_key.py
git commit -m "$(cat <<'EOF'
fix(search): find a tag by the name it is displayed under

data-name carried Tag.name (Japanese) while the chip rendered
loc(t, "name") -- the viewer's locale. On the live catalogue 681 of 735
tags have a name_en that differs from name, so an English viewer saw
"Aina Aiba", typed "Aiba", and matched nothing.

One Jinja global beside loc, applied to the nine Tag sites across Tags,
Preferences, Discover, welcome and the concert editor's picker.

Empties are dropped rather than joined: 109 live tags have no name_zh and
Jinja renders None as "None", which would have made every one of them
match a search for "none".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

### Task 2: a filtered list should look like a list

**Files:**
- Modify: `src/app/web/templates/base.html:171-177` (`filterChips`)
- Test: `tests/test_theme_and_tokens.py` (it already owns the `base.html` JS sweeps)

**Interfaces:**
- Consumes: nothing from Task 1; the two are independent.
- Produces: `filterChips(input, scope)` keeps its signature. Later phases rely on it hiding emptied containers.

**Why:** `filterChips` sets `display` on `[data-name]` elements **and nothing else**, so a search leaves every heading, group row and section behind. Searching one name on `/tags` returns the whole page skeleton with a single chip in it. Phase 2 removes per-group folds and makes search the only way into a 99-member group, so this stops being cosmetic.

**Why it cannot be CSS:** `:empty` does not match these containers — template indentation puts whitespace text nodes inside them. This codebase already paid for that lesson; the reasoning is in `concert_detail.html` around line 149, on `.chiprow:empty`. It must be JS.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_theme_and_tokens.py`, beside the other `base.html` sweeps:

```python
def test_filter_chips_hides_containers_that_empty():
    """A filtered list must look like a list.

    filterChips only ever set display on [data-name] elements, so a search left
    every heading and row on the page with nothing inside them. Phase 2 of the
    Following rework deletes per-group folds and makes search the only way to
    reach a name inside a 99-member group, so an unusable result shape stops
    being cosmetic.

    Pinned as source text rather than behaviour because there is no JS runtime
    in this suite. Mutation this must fail against: deleting the container pass
    and leaving only the per-chip loop.
    """
    js = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "data-filter-container" in js, (
        "containers opt in by attribute; filterChips must not guess at selectors"
    )
    assert "filterChips" in js
    body = js.split("function filterChips", 1)[1].split("\n    }", 1)[0]
    assert "querySelectorAll" in body
    assert body.count("style.display") >= 2, (
        "one pass for chips, one for the containers holding them"
    )
```

`TEMPLATES` does **not** exist in that file yet — add it beside the existing
`STYLE` constant at line 19:

```python
TEMPLATES = Path(__file__).resolve().parents[1] / "src/app/web/templates"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_theme_and_tokens.py::test_filter_chips_hides_containers_that_empty -q`
Expected: FAIL on the `data-filter-container` assertion.

- [ ] **Step 3: Extend `filterChips`**

`src/app/web/templates/base.html`, lines 171-177, currently:

```js
    function filterChips(input, scope) {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll(scope + " [data-name]").forEach(el => {
        const target = el.closest(".chipform") || el;
        target.style.display = (!q || el.dataset.name.includes(q)) ? "" : "none";
      });
    }
```

Replace with:

```js
    // Two passes. The first is the original: show or hide each [data-name]
    // chip. The second hides any container that now holds no visible chip, so
    // a search returns a result list instead of the whole page skeleton with
    // one chip left in it.
    //
    // Containers opt IN via data-filter-container rather than being guessed at
    // by selector, so a template can add a level without editing this function.
    //
    // Deliberately not CSS: :empty cannot do this, because template
    // indentation leaves whitespace text nodes inside these elements. That is
    // the same trap documented on .chiprow:empty in concert_detail.html.
    function filterChips(input, scope) {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll(scope + " [data-name]").forEach(el => {
        const target = el.closest(".chipform") || el;
        target.style.display = (!q || el.dataset.name.includes(q)) ? "" : "none";
      });
      document.querySelectorAll(scope + " [data-filter-container]").forEach(box => {
        // offsetParent is null for a hidden element, which is what makes this
        // read the FIRST pass's result rather than re-testing the query.
        const alive = [...box.querySelectorAll("[data-name]")].some(el => {
          const target = el.closest(".chipform") || el;
          return target.style.display !== "none";
        });
        box.style.display = (!q || alive) ? "" : "none";
      });
    }
```

- [ ] **Step 4: Mark the containers on the Tags page**

In `src/app/web/templates/tags.html`, add `data-filter-container` to:

- each `<div class="tsec">` — there are **exactly 4** in the file; add it to
  every one
- each `<div class="grow2...">` produced by the `group_row` macro

For `group_row`, the macro's opening tag currently reads:

```html
<div class="grow2{% if depth %} sub{% endif %}"{% if depth %} style="--d: {{ depth }}" data-subunit-of="{{ g.parent_id }}"{% endif %}>
```

Becomes:

```html
<div class="grow2{% if depth %} sub{% endif %}" data-filter-container{% if depth %} style="--d: {{ depth }}" data-subunit-of="{{ g.parent_id }}"{% endif %}>
```

Do **not** mark `.tags-scope` itself — hiding the scope would hide the search
box's own container on a no-match search.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --isolated pytest tests/test_theme_and_tokens.py::test_filter_chips_hides_containers_that_empty -q`
Expected: PASS.

- [ ] **Step 6: Verify in a real browser**

The test pins source text; it cannot prove the JS works. Start a dev server and
check by hand:

```bash
export SCRATCH="$(mktemp -d)"
export DATABASE_URL="sqlite+aiosqlite:///$SCRATCH/harness.db"
export SESSION_SECRET="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
export DISCORD_TOKEN=""
export BASE_URL="http://127.0.0.1:8099"
export WEB_PORT=8099
uv run --isolated alembic upgrade head
uv run --isolated python -m app.main
```

Seed several tags across at least two sections and two groups, sign in, open
`/tags`, and confirm: typing a name that matches one chip leaves that chip's
group row and section visible and hides the others; clearing the box restores
everything. Report what you saw.

If you cannot get a browser, say so explicitly rather than claiming a check you
did not perform.

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 8: Commit**

```bash
git add src/app/web/templates/base.html src/app/web/templates/tags.html tests/test_theme_and_tokens.py
git commit -m "$(cat <<'EOF'
fix(search): a filtered list looks like a list

filterChips set display on [data-name] elements and nothing else, so
searching one name returned the whole page skeleton with a single chip
somewhere inside it.

A second pass hides any container holding no visible chip. Containers opt
in with data-filter-container rather than being guessed at by selector.

Not CSS: :empty cannot reach these, because template indentation leaves
whitespace text nodes inside them -- the same trap already documented on
.chiprow:empty in concert_detail.html.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

### Task 3: one query instead of sixty-five

**Files:**
- Modify: `src/app/db/tags.py` (beside `group_members`, around line 638)
- Modify: `src/app/db/service.py` (the import block near line 327 and `__all__` near line 530)
- Modify: `src/app/web/routes/tags.py:110`
- Modify: `src/app/web/routes/preferences.py:133`
- Test: `tests/test_tag_members_batch.py` (new)

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `members_by_group(session, group_tag_ids) -> dict[int, list[Tag]]` — one entry per requested id, empty list for a group with no members, members ordered by `Tag.name`. Phase 2 uses it for group rows.

**Why:** both `/tags` and `/preferences` build
`{g.id: await group_members(session, g.id) for g in groups}` — one query per
group. The live catalogue has **65 groups**, so that is 65 round trips per page
render, on two pages.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tag_members_batch.py`. Use the shared `db`/`session`
fixtures from `tests/conftest.py` — do not write a new one:

```python
"""One query for every group's members, not one query per group.

/tags and /preferences each built their member map with a dict comprehension
over group_members(), which is 65 round trips on the live catalogue.
"""

from app.db.models import Tag, TagKind, TagMember
from app.db.service import members_by_group


async def _group(session, name, slug):
    g = Tag(name=name, name_en=name, kind=TagKind.GROUP, slug=slug)
    session.add(g)
    await session.flush()
    return g


async def test_members_by_group_returns_each_groups_members(session):
    a = await _group(session, "Aqours", "aqours")
    b = await _group(session, "Liella", "liella")
    m1 = Tag(name="伊波杏樹", name_en="Anju Inami", kind=TagKind.ARTIST, slug="anju")
    m2 = Tag(name="逢田梨香子", name_en="Rikako Aida", kind=TagKind.ARTIST, slug="rikako")
    session.add_all([m1, m2])
    await session.flush()
    session.add_all([
        TagMember(group_tag_id=a.id, member_tag_id=m1.id),
        TagMember(group_tag_id=a.id, member_tag_id=m2.id),
        TagMember(group_tag_id=b.id, member_tag_id=m1.id),
    ])
    await session.flush()

    got = await members_by_group(session, [a.id, b.id])
    assert [t.name for t in got[a.id]] == ["伊波杏樹", "逢田梨香子"]
    assert [t.name for t in got[b.id]] == ["伊波杏樹"]


async def test_members_by_group_gives_an_empty_list_for_a_memberless_group(session):
    """Callers index this map per group. A group with no members must yield an
    empty list, not a KeyError and not a missing key.

    Mutation this must fail against: building the dict only from the rows the
    query returns, which silently drops every memberless group.
    """
    g = await _group(session, "Empty", "empty")
    got = await members_by_group(session, [g.id])
    assert got == {g.id: []}


async def test_members_by_group_handles_an_empty_id_list(session):
    """A catalogue with no groups must not emit `IN ()`."""
    assert await members_by_group(session, []) == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_members_batch.py -q`
Expected: FAIL — `ImportError: cannot import name 'members_by_group'`.

- [ ] **Step 3: Add the batched query**

In `src/app/db/tags.py`, directly beneath `group_members` (around line 645):

```python
async def members_by_group(
    session: AsyncSession, group_tag_ids: Sequence[int]
) -> dict[int, list[Tag]]:
    """Every listed group's members in ONE query, ordered by name.

    The per-group `group_members` above is still correct for a single group;
    this exists because /tags and /preferences each wanted the map for every
    group at once and built it with a dict comprehension -- 65 round trips on
    the live catalogue.

    Every requested id gets an entry: a group with no members yields an empty
    list, because callers index this map per group and a missing key is a
    different bug in each of them.
    """
    out: dict[int, list[Tag]] = {gid: [] for gid in group_tag_ids}
    if not out:
        return out
    res = await session.execute(
        select(TagMember.group_tag_id, Tag)
        .join(Tag, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id.in_(list(out)))
        .order_by(TagMember.group_tag_id, Tag.name)
    )
    for group_id, tag in res:
        out[group_id].append(tag)
    return out
```

`Sequence` is **already imported** at `src/app/db/tags.py:10`
(`from collections.abc import Collection, Sequence`) — no import change needed.

- [ ] **Step 4: Re-export it from the facade**

In `src/app/db/service.py`, add `members_by_group` to the `from .tags import (...)`
block (near line 327, where `group_members` already appears) and add
`"members_by_group"` to `__all__` (near line 530). Keep both lists in their
existing alphabetical position.

**This is not tidiness.** `tests/test_service_facade.py` fails if the facade
goes stale, because bot and web import from the facade and a missing name is an
ImportError on whichever code path happens to hit it first.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --isolated pytest tests/test_tag_members_batch.py tests/test_service_facade.py -q`
Expected: PASS.

- [ ] **Step 6: Switch both call sites**

`src/app/web/routes/tags.py:110` currently:

```python
    members = {t.id: await group_members(session, t.id) for t in groups}
```

Becomes:

```python
    members = await members_by_group(session, [t.id for t in groups])
```

`src/app/web/routes/preferences.py:133` currently:

```python
    members = {g.id: await group_members(session, g.id) for g in groups}
```

Becomes:

```python
    members = await members_by_group(session, [g.id for g in groups])
```

Update each file's import to bring in `members_by_group` from
`app.db.service`, and drop `group_members` from that import **only if nothing
else in the file still uses it** — grep before deleting.

- [ ] **Step 7: Verify both pages still render**

Run: `uv run --isolated pytest tests/test_tags.py tests/test_preferences_page.py -q`
Expected: PASS. These are the render tests that would catch a member map whose
shape changed.

- [ ] **Step 8: Verify the mutation**

Temporarily change the helper to build its dict only from returned rows
(`out = {}` then `out.setdefault(group_id, []).append(tag)`), and re-run
`tests/test_tag_members_batch.py`. The memberless-group test must FAIL. Restore.

- [ ] **Step 9: Run the full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 10: Commit**

```bash
git add src/app/db/tags.py src/app/db/service.py src/app/web/routes/tags.py src/app/web/routes/preferences.py tests/test_tag_members_batch.py
git commit -m "$(cat <<'EOF'
perf(tags): one query for every group's members, not one per group

/tags and /preferences each built their member map with a dict
comprehension over group_members() -- 65 round trips per render on the
live catalogue, on two pages.

members_by_group() returns an entry for every requested id, including an
empty list for a memberless group: callers index the map per group, and a
missing key is a different bug in each of them.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

## Self-review notes

**Spec coverage.** This plan implements the spec's §Search in full (all three
bullets) and the §Data-and-queries N+1 paragraph. It deliberately implements
**none** of §`/tags`, §`/following`, §Preferences or §Editor mode — those are
sequencing steps 2-4 and get their own plans. The spec's §Testing bullets that
belong to this phase are covered: the three-name search assertion is Task 1
Step 7, and the split-pill `data-name` rule is noted as a Task 1 interface for
phase 2 rather than implemented here, because split pills do not exist yet.

**Not in this phase, on purpose.** `search_key` is applied to
`preferences.html`, whose picker phase 4 deletes. That is not wasted work: the
picker is live and broken today, and phase 4 may be weeks away.

**Type consistency.** `search_key(obj) -> str` and
`members_by_group(session, group_tag_ids) -> dict[int, list[Tag]]` are used
under those exact names in Tasks 1, 3 and their tests.

**Task independence.** All three tasks touch disjoint files except
`tests/test_theme_and_tokens.py` (Task 2 only) and `tags.html` (Tasks 1 and 2,
different lines). Run them in order anyway — the plan's commits assume it.
