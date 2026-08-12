# Following Rework — Phase 2: `/tags` becomes the follow surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/tags` the place you follow tags — every chip a real follow control, characters and their seiyuu as split pills with independently followable halves, and three pieces of dead weight removed.

**Architecture:** One page, one context builder. The db layer gains a seiyuu map and drops subunit members from their parent's row; the template turns chips into forms, renders split pills, and loses the Characters section and the table view; editors get a mode switch so their edit-on-click survives.

**Tech Stack:** Jinja2 templates, plain CSS (`.mchip` already exists), vanilla JS, SQLAlchemy async, pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-following-rework-design.md` — this is its §Suggested sequencing **step 2**. Phase 1 (search + the N+1) is merged as PR #151.

**NOT in this phase:** `/following` and its config dialog, the Preferences reduction, and the standing preset default. Those are steps 3-4.

> **The link to `/following` is deliberately omitted.** The spec says `/tags` links to it, but that page does not exist until phase 3, which adds the link. Do not add a dead link here.

## Global Constraints

- Run everything from the repo root `E:\click clack clan\concert-reminder`.
- **Always `uv run --isolated`** — an external process holds a lock on `.venv`; never `uv sync`.
- **Run test commands in the FOREGROUND with `timeout: 900000`.** The suite takes 5-10 minutes.
- **Baseline: 2802 passing**, `uv run --isolated ruff check .` clean. Both before any commit.
- **Sentence case everywhere.**
- **Radiuses: 3px default, 999px chips, 4px overlay cards, 50% circles, bottom sheets `14px 14px 0 0`.** Never 6px or 8px — there is a sweep test.
- **Two callout shapes and no third**: `.edgecard` (raise ground, coloured left edge — ongoing state) and `.banner` (wash ground, full border — needs attention).
- **Never interpolate user-controlled text into an inline `on*` handler** (invariant 7). Tag names are user-controlled; the browser HTML-decodes the attribute before parsing it as JS, so Jinja's escaping does not protect you. Use `data-` attributes read via `dataset`.
- **Editing existing English copy must keep the msgid byte-identical, or update BOTH `.po` catalogues** (`src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`). `tests/test_i18n_catalogues.py` fails on anything untranslated. **New user-visible strings need both catalogues filled by hand.**
- **`data-name` is `filterChips()`'s hook** and its only sanctioned job on this page. Phase 1 made it carry `search_key(t)` — all three of a tag's names.
- **A `db/` feature module must NEVER import `db/service.py`** (a cycle). Adding a name to `db/tags.py` means adding it to the facade too — `tests/test_service_facade.py` enforces it.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```

## The three traps in this phase

1. **`Tag.voiced_by` is not a loaded relationship.** Resolving a character's seiyuu by touching that attribute during async template rendering is a `MissingGreenlet` **500**. It must be resolved from the already-loaded tag list — `routes/tags.py:145` already does exactly this for the section being deleted, and the comment there says why. Task 1 moves that resolution into the context builder; nothing may reintroduce a lazy load.
2. **`.tagtable` is shared by six other templates** — `admin_broadcast`, `admin_deliveries`, `admin_discoveries`, `admin_fetch_domains`, `admin_quiet_ladders`, `rehearsal`. Task 5 removes the table **markup** from `/tags`. **The CSS rule must stay.** Deleting it silently flattens six admin pages.
3. **A chip is currently an editor's edit button.** Making it a follow control takes that away, which is why Task 6 exists. Tasks 2-5 leave editors temporarily unable to open a tag dialog by clicking a chip; that is expected and is closed by Task 6, which must land in the same branch.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/tags.py` | `tag_directory_context` gains `seiyuu_of`, and drops subunit members from their parent's row |
| `src/app/web/routes/tags.py` | stops building `characters`; passes the new map through |
| `src/app/web/templates/tags.html` | chips become forms; split pills; Characters section and table markup deleted; editor mode |
| `src/app/web/static/style.css` | split-pill and edit-mode rules for this page |
| `docs/architecture.md`, `WISHLIST.md` | recorded in Task 7 |

---

### Task 1: the context — seiyuu map, and subunit members leave their parent's row

**Files:**
- Modify: `src/app/db/tags.py` — `tag_directory_context` (starts line 914; the group walk is at 1019-1044)
- Modify: `src/app/web/routes/tags.py:145-149` (drop the `characters` key)
- Test: `tests/test_tag_directory_subunits.py` (new)

**Interfaces:**
- Produces: `tag_directory_context(...)` gains `seiyuu_of: dict[int, Tag | None]`, keyed by CHARACTER tag id. `franchise_families` and `no_franchise_groups` keep their `[(Tag, [Tag, ...], int)]` shape, but a parent group's member list no longer contains members that belong to one of its subunits.
- Consumes: nothing new.

**Why the de-dup:** owner ruling, `/tags` only. The 2026-08-01 spec ruled "repetition kept" because a *concert bill* must be a truthful lineup and its content depends on which other tags are attached. A catalogue has no "attached" — nothing varies — so the objection does not transfer. **The concert page is untouched.** Measured on the live catalogue: member chips in group rows **485 → 343**, and **6 parent rows become empty** (SideM 49→0, Shiny Colors 28→0, four others).

**Depth note:** the live catalogue's deepest group nesting is 1, so direct and transitive de-dup give the same 343. Implement it **transitively anyway** — `GROUP → GROUP → GROUP` is legal, and the cost is identical.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tag_directory_subunits.py`. Use `tests/conftest.py`'s shared `session` fixture — do not write a new one:

```python
"""On /tags, a subunit's members render under the subunit and nowhere else.

Owner ruling 2026-08-12, THIS PAGE ONLY. The 2026-08-01 spec kept the
repetition on the concert page because a bill must be a truthful lineup and
what it shows depends on which other tags are attached. A catalogue has no
"attached", so that reasoning does not transfer.
"""

from app.db.models import Tag, TagKind, TagMember
from app.db.service import tag_directory_context


async def _tag(session, name, kind, slug, **kw):
    t = Tag(name=name, name_en=name, kind=kind, slug=slug, **kw)
    session.add(t)
    await session.flush()
    return t


async def test_a_subunit_member_leaves_its_parents_row(session):
    parent = await _tag(session, "765PRO ALLSTARS", TagKind.GROUP, "765pro")
    sub = await _tag(session, "竜宮小町", TagKind.GROUP, "ryuguu", parent_id=parent.id)
    shared = await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko")
    only_parent = await _tag(session, "天海春香", TagKind.CHARACTER, "haruka")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=shared.id),
        TagMember(group_tag_id=parent.id, member_tag_id=only_parent.id),
        TagMember(group_tag_id=sub.id, member_tag_id=shared.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: [m.name for m in members] for g, members, _d in ctx["no_franchise_groups"]}
    assert rows["竜宮小町"] == ["秋月律子"], "the subunit keeps her"
    assert rows["765PRO ALLSTARS"] == ["天海春香"], (
        "and the parent drops her -- she renders in the subunit and nowhere else"
    )


async def test_a_parent_whose_members_are_all_in_subunits_renders_empty(session):
    """6 live groups become empty rows. They must still RENDER -- the row is
    the group, and the concert page's own ruling is that an empty member area
    shows the label row silently rather than '0 performers'.

    Mutation this must fail against: dropping a group whose de-duped member
    list is empty.
    """
    parent = await _tag(session, "SideM", TagKind.GROUP, "sidem")
    sub = await _tag(session, "Jupiter", TagKind.GROUP, "jupiter", parent_id=parent.id)
    m = await _tag(session, "天ヶ瀬冬馬", TagKind.CHARACTER, "touma")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=m.id),
        TagMember(group_tag_id=sub.id, member_tag_id=m.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: members for g, members, _d in ctx["no_franchise_groups"]}
    assert "SideM" in rows, "the empty parent still renders"
    assert rows["SideM"] == []
    assert [m.name for m in rows["Jupiter"]] == ["天ヶ瀬冬馬"]


async def test_seiyuu_of_maps_characters_to_their_performer(session):
    """The template needs the seiyuu for split pills, and Tag.voiced_by is NOT
    a loaded relationship -- touching it during async rendering is a
    MissingGreenlet 500. The context resolves it from the loaded tag list.
    """
    seiyuu = await _tag(session, "若林直美", TagKind.ARTIST, "naomi")
    await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko",
               voiced_by_tag_id=seiyuu.id)
    await _tag(session, "三浦あずさ", TagKind.CHARACTER, "azusa")

    ctx = await tag_directory_context(session)
    by_name = {t.name: t for t in [seiyuu]}
    got = {cid: (s.name if s else None) for cid, s in ctx["seiyuu_of"].items()}
    assert "若林直美" in got.values()
    assert None in got.values(), "a character with no seiyuu maps to None, not a KeyError"
    assert len(got) == 2, "CHARACTER tags only -- artists and groups are not keys"
    assert by_name  # silence the unused-name linter
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_directory_subunits.py -q`
Expected: FAIL — `seiyuu_of` is not in the context, and the parent row still contains the shared member.

- [ ] **Step 3: Drop subunit members from the parent's row**

In `src/app/db/tags.py`, the group walk currently reads (around line 1022):

```python
    def group_rows(g: Tag, depth: int = 0) -> list[tuple[Tag, list[Tag], int]]:
        if g.id in walked:
            return []
        walked.add(g.id)
        rows = [(g, members_of.get(g.id, []), depth)]
        for child in children_of.get(g.id, []):
            rows.extend(group_rows(child, depth + 1))
        return rows
```

Add this helper immediately above it, and change the `rows = [...]` line:

```python
    def subunit_member_ids(g: Tag, seen: set[int] | None = None) -> set[int]:
        """Every member of g's subunits, transitively.

        Its own `seen` set, not the walk's `walked`: this runs BEFORE the walk
        reaches those children, and sharing the set would make a parent's
        de-dup depend on visit order. A parent cycle is reachable (rows predate
        `would_create_tag_cycle`), so this needs its own guard or it recurses
        forever.
        """
        seen = set() if seen is None else seen
        if g.id in seen:
            return set()
        seen.add(g.id)
        out: set[int] = set()
        for child in children_of.get(g.id, []):
            out |= {m.id for m in members_of.get(child.id, [])}
            out |= subunit_member_ids(child, seen)
        return out

    def group_rows(g: Tag, depth: int = 0) -> list[tuple[Tag, list[Tag], int]]:
        if g.id in walked:
            return []
        walked.add(g.id)
        # A member who also belongs to one of this group's subunits renders
        # under the subunit and nowhere else (owner, 2026-08-12, THIS PAGE
        # ONLY -- the concert page keeps the repetition, because a bill is a
        # lineup and a catalogue is not). Measured on the live catalogue:
        # 485 member chips -> 343, and 6 parent rows become empty.
        absorbed = subunit_member_ids(g)
        own = [m for m in members_of.get(g.id, []) if m.id not in absorbed]
        rows = [(g, own, depth)]
        for child in children_of.get(g.id, []):
            rows.extend(group_rows(child, depth + 1))
        return rows
```

- [ ] **Step 4: Add the seiyuu map**

Still in `tag_directory_context`, beside the other derived maps (after `venue_regions` is built is fine), add:

```python
    # Characters keyed to the performer who voices her, for the split pill.
    # Resolved HERE off the already-loaded tag list: Tag.voiced_by is not a
    # loaded relationship, and a lazy load during async template rendering is
    # a MissingGreenlet 500. A character whose seiyuu is unset -- or whose
    # seiyuu tag was deleted, since the FK is ON DELETE SET NULL -- maps to
    # None and renders as a plain chip.
    seiyuu_of = {
        t.id: by_id.get(t.voiced_by_tag_id)
        for t in tags
        if t.kind is TagKind.CHARACTER
    }
```

Add `"seiyuu_of": seiyuu_of,` to the returned dict, and add a `seiyuu_of` line to the docstring's "Returns a dict with:" list, matching the existing entries' style.

- [ ] **Step 5: Drop the now-unused `characters` context from the route**

`src/app/web/routes/tags.py:145-149` currently builds:

```python
            "characters": [
                (t, by_id.get(t.voiced_by_tag_id))
                for t in tags
                if t.kind is TagKind.CHARACTER
            ],
```

Delete that key **and the comment block above it that explains the MissingGreenlet reasoning** — move that comment to the context builder in Step 4 if it is not already covered there. The template stops using `characters` in Task 4; deleting the key now would break the page in between, so **leave the key in place until Task 4** if the template still references it. Check: `grep -n "characters" src/app/web/templates/tags.html`. If it is still referenced, skip this step and note it — Task 4 removes it.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run --isolated pytest tests/test_tag_directory_subunits.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 7: Verify the mutations**

Two, separately:
1. Change `own` back to `members_of.get(g.id, [])` → `test_a_subunit_member_leaves_its_parents_row` must FAIL. Restore.
2. Make `group_rows` skip a group whose `own` is empty (`if not own and depth == 0: return []`) → `test_a_parent_whose_members_are_all_in_subunits_renders_empty` must FAIL. Restore.

Report both outcomes. If either passes, the test is not doing its job — stop and say so.

- [ ] **Step 8: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: 2802 + 3 new = 2805, ruff clean.

- [ ] **Step 9: Commit**

```bash
git add src/app/db/tags.py src/app/web/routes/tags.py tests/test_tag_directory_subunits.py
git commit -m "$(cat <<'EOF'
feat(tags): a subunit's members render under the subunit alone

Owner ruling 2026-08-12, THIS PAGE ONLY. The 2026-08-01 spec kept the
repetition because a concert bill must be a truthful lineup and what it
shows depends on which other tags are attached. A catalogue has no
"attached", so the objection does not transfer -- and the concert page is
untouched.

Measured on the live catalogue: 485 member chips in group rows -> 343,
with 6 parent rows becoming empty. They still render: the row is the
group, and an empty member area is the concert page's own silent shape.

Also adds seiyuu_of, resolved off the loaded tag list because
Tag.voiced_by is not a loaded relationship and touching it during async
rendering is a MissingGreenlet 500.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

### Task 2: a chip is a follow control

**Files:**
- Modify: `src/app/web/templates/tags.html` — the `tag_chip` macro (lines 208-221)
- Modify: `src/app/web/static/style.css` — followed-chip state
- Test: `tests/test_tags_follow.py` (new)

**Interfaces:**
- Consumes: `sub_by_tag` (already in the route's context, `routes/tags.py:107`), `search_key` (phase 1).
- Produces: the `tag_chip(t, count)` macro now emits a `<form>`. Task 3's split pill calls the same follow markup per half.

**Why:** today `tag_chip` gives editors a `<button>` opening the edit dialog and everyone else an inert `<span>`. A non-editor cannot follow anything from this page — the only follow control lives in the table, whose toggle is editor-gated.

**The pattern to copy:** `src/app/web/templates/_tag_follow_bell.html` already does this correctly — a real `<form>`, `next=/tags` so you stay put, `notify=true` matching the follow default. **Read it first.** This task generalises it onto the chip.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tags_follow.py`. Read `tests/test_tags.py` for its `client` fixture, `login_as`, `EDITOR_ID` and `VIEWER_ID`, and match that style:

```python
"""Every chip on /tags follows or unfollows, for everyone, without JS.

Before this, the only follow control on the page was in the table view, and
the chips-vs-table toggle is editor-gated -- so a non-editor was shipped a
hidden table full of follow buttons they had no way to reveal, and the chips
they could see were inert <span>s.
"""


async def test_a_non_editor_can_follow_from_the_chips(client):
    """The whole point. VIEWER_ID is not an editor."""
    login_as(client, VIEWER_ID, "viewer")
    # seed a tag ...
    r = client.get("/tags")
    assert 'action="/subscriptions"' in r.text, "a real form, not a JS handler"
    assert 'name="next" value="/tags"' in r.text, "and it comes back here"


async def test_following_a_tag_makes_its_chip_offer_unfollow(client):
    """The chip carries state: follow -> unfollow, and back."""
    ...


async def test_the_follow_form_needs_no_javascript(client):
    """Mutation this must fail against: a chip rewritten as
    <button onclick=...>, which renders identically and does nothing with JS
    off. Assert the FORM, not the button."""
    ...
```

Fill the bodies out against the real fixtures — seed a Tag, assert the rendered markup, and for the second test POST to `/subscriptions` and re-render. Keep each test's docstring naming the mutation it catches.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_tags_follow.py -q`
Expected: FAIL — no `action="/subscriptions"` on the page for a non-editor.

- [ ] **Step 3: Rewrite `tag_chip`**

The macro at `tags.html:212-220` currently branches on `user.is_editor` to choose a `<button>` (edit dialog) or an inert `<span>`. Replace it with a follow form for **both** roles. The editor's edit-on-click returns in Task 6 as a mode.

```html
{#- A tag chip. Every chip is a follow control: a real <form> posting to the
    same routes _tag_follow_bell.html uses, so it works with JS off, with
    next=/tags so you stay where you are. `count` is the concert count (None
    for member chips, which carry no count and never show the unused state).

    Editors get their edit-on-click back through the Follow/Edit mode switch
    (the .viewbar), not by branching here -- a chip that means different
    things to different people is the thing that made this page unusable for
    everyone who is not an editor.

    data-name carries search_key(t) -- all three of the tag's names -- and is
    filterChips()'s hook. It goes on the FORM, which is what filterChips hides
    (it resolves .closest(".chipform")). -#}
{% macro tag_chip(t, count) %}
{%- set kcls = {"franchise": " k-franchise", "group": " k-group", "venue": " k-venue"}.get(t.kind.value, "") -%}
{%- set unused = count is not none and count == 0 -%}
{%- set sub = sub_by_tag.get(t.id) -%}
{%- if sub -%}
<form class="chipform" method="post" action="/subscriptions/{{ sub.id }}/delete" data-name="{{ search_key(t) }}">
  <input type="hidden" name="next" value="/tags">
  <button class="tchip{{ kcls }} on" data-tag-chip
          title="{{ _('Following — click to unfollow') }}">{{ loc(t, "name") }} ✓{% if count is not none %}<span class="n2">{{ count }}</span>{% endif %}</button>
</form>
{%- else -%}
<form class="chipform" method="post" action="/subscriptions" data-name="{{ search_key(t) }}">
  <input type="hidden" name="tag_id" value="{{ t.id }}">
  <input type="hidden" name="notify" value="true">
  <input type="hidden" name="next" value="/tags">
  <button class="tchip{{ kcls }}{% if unused %} unused{% endif %}" data-tag-chip
          title="{{ _('Follow this tag') }}">{{ loc(t, "name") }}{% if count is not none %}<span class="n2">{{ count }}</span>{% endif %}</button>
</form>
{%- endif -%}
{% endmacro %}
```

`data-tag-chip` is the hook Task 6's mode switch reads. It is **not** `data-name` — that one is filterChips's and must not be overloaded.

- [ ] **Step 4: Add the followed-chip style**

In `style.css`, beside the existing `.tchip` rules, add:

```css
/* A followed tag. The ok wash + tick is the same "you have this" vocabulary
   the round rows use; .tchip keeps its 999px chip radius. */
.tchip.on { background: var(--ok-wash); border-color: var(--ok); color: var(--ok); font-weight: 600; }
```

**Verified while writing this plan:** `.tchip` (style.css:1394-1398) already sets `border: 1px solid transparent`, so recolouring the border cannot change the chip's size — the rule above is correct as written and needs no size compensation. Still confirm visually that the followed chip does not reflow its row.

- [ ] **Step 5: Both new strings need both catalogues**

`"Following — click to unfollow"` and `"Follow this tag"` are new msgids. Add them to `src/app/translations/ja/LC_MESSAGES/messages.po` and `.../zh/...`, with translations. Suggested:

- ja: `フォロー中 — クリックで解除` / `このタグをフォロー`
- zh: `关注中 — 点击取消关注` / `关注此标签`

`tests/test_i18n_catalogues.py` fails if either is missing.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --isolated pytest tests/test_tags_follow.py tests/test_i18n_catalogues.py -q`

- [ ] **Step 7: Verify in a browser, both roles**

Start a dev server (the harness pattern is in the phase 1 plan, or mint a session directly). Sign in as a **non-editor** and confirm a chip follows and unfollows and the page returns to `/tags`. Then confirm the same as an editor. Report both.

- [ ] **Step 8: Full suite and lint, then commit**

```bash
git add src/app/web/templates/tags.html src/app/web/static/style.css src/app/translations tests/test_tags_follow.py
git commit -m "$(cat <<'EOF'
feat(tags): every chip follows, for everyone, without JS

The only follow control on this page lived in the table view, and the
chips-vs-table toggle is editor-gated -- so a non-editor was shipped a
hidden table full of follow buttons they could not reveal, while the chips
they could see were inert spans.

A real form per chip, the same routes _tag_follow_bell.html already used,
next=/tags so you stay put. The editor's edit-on-click returns in the
Follow/Edit mode switch rather than by branching inside the chip.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

### Task 3: the split pill

**Files:**
- Modify: `src/app/web/templates/tags.html` — new `member_chip` macro, used by `group_row`
- Modify: `src/app/web/static/style.css` — `.mchip` follow states
- Test: `tests/test_tags_split_pill.py` (new)

**Interfaces:**
- Consumes: `seiyuu_of` (Task 1), `tag_chip`'s follow-form shape (Task 2).
- Produces: `member_chip(m)` — renders a split pill when `m` is a CHARACTER with a seiyuu, a plain `tag_chip(m, none)` otherwise.

**Why:** owner design. A character and the performer who voices her are **different subscriptions** — invariant 3 says attaching 今井麻美 pulls in no characters, *"because she also appears as herself at events with no im@s connection"*. The split pill is the first surface where a user can act on that difference.

**`.mchip` already exists** in `style.css` (the concert page's split pill) — box derived from `.performers .chip`, measured, do not retune it. This task adds only the follow states.

**The conditional-merge rule, unchanged:** both ends present → one pill in two halves; either alone → a plain chip. That contrast is *why* the split shape won over an inline `如月千早（今井麻美）` form.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tags_split_pill.py`:

```python
"""A character and her seiyuu are one chip in two halves, each its own follow.

The distinction is real in the data model and had nowhere to be expressed:
following 秋月律子 and following 若林直美 are different subscriptions.
"""


async def test_a_character_with_a_seiyuu_renders_one_pill_with_two_forms(client):
    """Two forms inside one .mchip -- each half posts on its own tag_id."""
    ...


async def test_a_character_with_no_seiyuu_falls_back_to_a_plain_chip(client):
    """The conditional merge. A one-ended pill would read as inconsistent
    styling; a plain chip reads as an ordinary performer, which is what she is.

    Mutation this must fail against: always rendering .mchip and leaving the
    second half empty."""
    ...


async def test_each_half_follows_independently(client):
    """Follow the character; the seiyuu half must still offer follow, and the
    character half must offer unfollow. This is the state the whole design
    exists for."""
    ...
```

Fill the bodies against the real fixtures: seed an ARTIST, a CHARACTER with `voiced_by_tag_id` set, a CHARACTER without one, and a GROUP with all of them as members so they render in a group row.

- [ ] **Step 2: Run it to verify it fails**

- [ ] **Step 3: Add the `member_chip` macro**

In `tags.html`, above `group_row`:

```html
{#- A member chip. A CHARACTER whose seiyuu is attached renders as ONE chip
    visibly made of two halves, each half its own follow form; either end
    alone falls back to a plain chip.

    That fallback is the design, not a shortcut: the merge is CONDITIONAL, so
    the split shape has to make a one-ended chip read as meaningfully
    different rather than as inconsistent styling (owner, from four mockups,
    2026-08-01).

    ONE data-name on the pill carrying BOTH names -- filterChips hides the
    elements it matches, so a per-half attribute would hide one half of a
    pill and render the other, which is worse than no result. -#}
{% macro member_chip(m) %}
{%- set cv = seiyuu_of.get(m.id) if m.kind.value == "character" else none -%}
{%- if cv -%}
<span class="mchip" data-name="{{ search_key(m) }} {{ search_key(cv) }}">
  {{ follow_half(m, "cn") }}{{ follow_half(cv, "cv") }}
</span>
{%- else -%}
{{ tag_chip(m, none) }}
{%- endif -%}
{% endmacro %}
```

And the half, which is `tag_chip`'s form reduced to one side. Put it directly above `member_chip`:

```html
{#- One half of a split pill: the same follow form tag_chip emits, without
    the count and without its own data-name (the pill above carries one for
    both). `cls` is "cn" (character) or "cv" (seiyuu, the dim half). -#}
{% macro follow_half(t, cls) %}
{%- set sub = sub_by_tag.get(t.id) -%}
{%- if sub -%}
<form class="half" method="post" action="/subscriptions/{{ sub.id }}/delete">
  <input type="hidden" name="next" value="/tags">
  <button class="{{ cls }} on" data-tag-chip
          title="{{ _('Following — click to unfollow') }}">{{ loc(t, "name") }} ✓</button>
</form>
{%- else -%}
<form class="half" method="post" action="/subscriptions">
  <input type="hidden" name="tag_id" value="{{ t.id }}">
  <input type="hidden" name="notify" value="true">
  <input type="hidden" name="next" value="/tags">
  <button class="{{ cls }}" data-tag-chip
          title="{{ _('Follow this tag') }}">{{ loc(t, "name") }}</button>
</form>
{%- endif -%}
{% endmacro %}
```

Then change `group_row`'s member loop from `{{ tag_chip(m, none) }}` to `{{ member_chip(m) }}`.

- [ ] **Step 4: Style the halves**

`.mchip` exists and its box is measured — **do not change it**. Add only:

```css
/* The Tags page's split pill halves are buttons in forms, not links. The
   form wrapper must not introduce a box: .mchip is an inline-flex of two
   padded halves, and a block-level form between them breaks the seam. */
.mchip .half { display: contents; }
.mchip .half button { border: 0; background: none; font: inherit; cursor: pointer; }
.mchip .half button.on { background: var(--ok-wash); color: var(--ok); font-weight: 600; }
```

Confirm against `style.css`'s existing `.mchip > *` rule that padding and `line-height` still land on the buttons — if `.half { display: contents }` makes the button the flex child, the existing `.mchip > *` selector will not match it. **Check this in a browser and adjust the selector rather than guessing**; the pill's box was measured to match a plain chip exactly (28.72px) and must still.

- [ ] **Step 5: Run the tests, then verify all four states in a browser**

Seed a character with a seiyuu and confirm each state renders and is pressable: neither followed, character only, seiyuu only, both. Report the pill's measured height against a plain chip in the same row.

- [ ] **Step 6: Full suite and lint, then commit**

---

### Task 4: delete the Characters section

**Files:**
- Modify: `src/app/web/templates/tags.html` (the section at 266-289)
- Modify: `src/app/web/routes/tags.py` (drop `characters` if Task 1 left it)
- Test: `tests/test_tags_split_pill.py` (extend)

**Why:** every one of the 318 live characters is a member of at least one group — measured, zero exceptions — so once group rows carry split pills, this section renders nothing new. Owner: *"individual characters should be treated as an individual artist."*

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tags_split_pill.py`:

```python
async def test_a_character_in_no_group_appears_with_the_ungrouped_performers(client):
    """"Treated as an individual artist" -- there is no Characters section any
    more, so a character with no group must not vanish. Zero live characters
    are ungrouped today, but the catalogue can change and a tag that renders
    nowhere is unfollowable.

    Mutation this must fail against: deleting the section without widening
    ungrouped_performers, which drops her off the page entirely.
    """
    ...
```

- [ ] **Step 2: Run it to verify it fails** — a lone character currently renders only in the Characters section, and `ungrouped_performers` is ARTIST-only.

- [ ] **Step 3: Widen `ungrouped_performers`**

`src/app/db/tags.py:1054` currently reads:

```python
    ungrouped_performers = [a for a in artists if a.id not in grouped_member_ids]
```

Replace with:

```python
    # ARTIST *and* CHARACTER: the Characters section is gone (2026-08-12), so
    # a character who belongs to no group has no other row to appear in, and a
    # tag that renders nowhere cannot be followed. `tags` is already in name
    # order (the select at the top of this function orders by Tag.name), so
    # the comprehension inherits it exactly as the `artists` one did.
    ungrouped_performers = [
        t for t in tags
        if t.kind in (TagKind.ARTIST, TagKind.CHARACTER)
        and t.id not in grouped_member_ids
    ]
```

Note the local `artists` may now be unused — check and remove it if so, or ruff will flag it.

The section's heading in `tags.html` reads "Performers with no group — solo artists, or members not yet attached". That copy still holds for characters, so **no msgid changes** here.

- [ ] **Step 4: Delete the section**

Remove the whole `{% if characters %} ... {% endif %}` block from `tags.html` (266-289), then remove the `characters` key from `routes/tags.py` and confirm with `grep -n "characters" src/app/web/templates/tags.html src/app/web/routes/tags.py` that nothing references it.

The section heading strings (`"Characters"`, `"— each with the performer who voices her"`) become unused msgids. **Leave them in the catalogues** — `pybabel` prunes obsolete entries on the next extract, and hand-deleting risks removing a string still used elsewhere. Note it in the commit message.

- [ ] **Step 5: Run the tests, full suite, lint, commit**

---

### Task 5: delete the table view

**Files:**
- Modify: `src/app/web/templates/tags.html` — the table block and the Chips⇄Table half of the toggle
- Test: `tests/test_theme_and_tokens.py` (add the `.tagtable` guard)

**Why:** owner ruling — the table is redundant. Editors lose nothing: the per-tag edit dialog already shows events, followers, members and upcoming.

> **THE TRAP.** `.tagtable` is used by **six other templates** — `admin_broadcast`, `admin_deliveries`, `admin_discoveries`, `admin_fetch_domains`, `admin_quiet_ladders`, `rehearsal`. **Only the `/tags` markup goes. The CSS rule stays.**

- [ ] **Step 1: Write the failing test**

Add to `tests/test_theme_and_tokens.py`:

```python
def test_tagtable_css_survives_the_tags_page_losing_its_table():
    """/tags dropped its table view (2026-08-12), but .tagtable is shared with
    six admin templates. Deleting the rule with the markup would silently
    flatten all six.

    Mutation this must fail against: removing the .tagtable rules from
    style.css as part of "cleaning up" the tags page.
    """
    style = css()
    assert ".tagtable" in style
    users = [p.name for p in TEMPLATES.glob("*.html")
             if "tagtable" in p.read_text(encoding="utf-8")]
    assert "tags.html" not in users, "the tags page's table markup is gone"
    assert len(users) >= 6, f"still used by the admin pages: {users}"
```

- [ ] **Step 2: Run it to verify it fails** — `tags.html` still contains the table.

- [ ] **Step 3: Delete the markup**

Remove `<div id="tag-table-wrap" hidden>...</div>` and its contents from `tags.html`. In the `.viewbar`, remove the Chips/Table buttons and their JS (`tagViewToggle` — the block around line 334), but **leave the `.viewbar` element itself**: Task 6 puts the Follow/Edit toggle in it.

- [ ] **Step 4: Run the test, full suite, lint, commit**

---

### Task 6: editor mode

**Files:**
- Modify: `src/app/web/templates/tags.html` — the `.viewbar`, plus the mode JS
- Modify: `src/app/web/static/style.css` — edit-mode chip treatment and the strip
- Modify: both `.po` catalogues
- Test: `tests/test_tags_edit_mode.py` (new)

**Why:** Task 2 took the editor's edit-on-click away. This gives it back as a mode, so a chip means one thing for everyone by default.

**Design (owner-approved):** `Follow ⇄ Edit` in the existing editor-only `.viewbar`, same `aria-pressed` vocabulary as the toggle it replaces, and — matching that toggle — **it does not persist**, so a forgotten mode expires on reload. In Edit, chips go dashed-accent and **drop their follow ticks** (a chip claiming "following" while its click opens an editor is lying about what it does), with an `.edgecard` strip stating the mode. Non-editors see no toggle, no strip and no mode.

- [ ] **Step 1: Write the failing test**

```python
async def test_a_non_editor_sees_no_mode_switch_and_no_strip(client):
    """A mode nobody but an editor can enter must not appear for anyone else.

    Mutation this must fail against: rendering the viewbar unconditionally."""
    ...

async def test_an_editor_gets_the_switch_and_the_strip_markup(client):
    ...

async def test_edit_mode_is_not_persisted(client):
    """Matching the Chips/Table toggle it replaces: no localStorage, so a
    forgotten Edit mode expires on reload. Assert the JS does not write
    localStorage for the mode."""
    ...
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Add the toggle and the strip**

Inside the existing `{% if user.is_editor %}` `.viewbar`:

```html
  <div class="viewtoggle" id="tagModeToggle">
    <button type="button" data-mode="follow" aria-pressed="true">{{ _("Follow") }}</button>
    <button type="button" data-mode="edit" aria-pressed="false">{{ _("Edit") }}</button>
  </div>
```

And the strip, directly after the viewbar, hidden by default:

```html
{#- Ongoing state, so .edgecard (the two-shape callout grammar has no third
    shape and this is not asking for attention). Hidden until Edit is on. -#}
<div class="edgecard editmode" id="tagEditStrip" hidden>
  {{ _("Editing — a chip opens its tag editor. Following is off while this is on.") }}
</div>
```

- [ ] **Step 4: Wire it**

Add JS beside the existing page scripts. It must:
- flip `aria-pressed` on both buttons
- toggle a class on `.tags-scope` (e.g. `editing`) that CSS keys off
- show/hide `#tagEditStrip`
- in Edit, intercept clicks on `[data-tag-chip]` in the **capture phase** and `stopPropagation()`, then open that tag's dialog

> **`preventDefault()` is not enough to stop a form submit here.** This codebase learned it on htmx and again on the outcome-correction dialogs: use a **capture-phase** listener and `stopPropagation()`. A bubble-phase `preventDefault` leaves the press landing on a real form.

The tag id must reach the handler through a `data-` attribute read via `dataset` — **never interpolated into an inline `on*` handler** (invariant 7). Add `data-tag-id="{{ t.id }}"` to the chip buttons in Task 2's macros as part of this task.

- [ ] **Step 5: Style edit mode**

```css
/* Edit mode (editor-only). Chips go dashed-accent and lose their followed
   state, because a chip showing "following" while its click opens an editor
   is lying about what the click does. */
.tags-scope.editing .tchip { border-style: dashed; border-color: var(--accent); }
.tags-scope.editing .tchip.on { background: var(--chip-bg); color: var(--ink); font-weight: 400; }
.tags-scope.editing .mchip { border-style: dashed; border-color: var(--accent); }
.tags-scope.editing .mchip .half button.on { background: none; color: var(--ink); font-weight: 400; }
.editmode { border-left-color: var(--accent); }
```

**Do NOT give edit-mode chips an `--accent-wash` background.** `.tchip.k-franchise` (style.css:1401) already uses exactly that as its own background, so every chip on the page would come to look like a franchise chip. The dashed accent border carries the signal on its own.

`--accent-wash` and `--chip-bg` both exist in `:root` (verified). Style against **both** light and dark.

- [ ] **Step 6: New strings to both catalogues**

`"Follow"`, `"Edit"`, and the strip sentence. ja/zh by hand.

- [ ] **Step 7: Browser check — both modes, both roles**

Confirm: as an editor, Follow mode follows and Edit mode opens dialogs; the strip appears only in Edit; reloading returns to Follow. As a non-editor, no toggle, no strip, chips follow.

- [ ] **Step 8: Full suite, lint, commit**

---

### Task 7: bookkeeping

**Files:**
- Modify: `docs/architecture.md`
- Modify: `WISHLIST.md`

- [ ] **Step 1: architecture.md entries**

Add entries for: the subunit de-dup being `/tags`-only and why the concert page differs; `seiyuu_of` existing because `Tag.voiced_by` is not loaded and a lazy load is a `MissingGreenlet` 500; the chip being a form so it works without JS; and the edit-mode capture-phase requirement. Match the file's style.

- [ ] **Step 2: WISHLIST note**

Add a dated note under the unranked "Following is due a rework" entry recording that phase 2 shipped and phases 3-4 remain. **Do not move the entry to Shipped.**

- [ ] **Step 3: Full suite, lint, commit**

---

## Self-review notes

**Spec coverage.** Implements the spec's §`/tags` in full and the Task 1 half of §Data-and-queries. Deliberately omits: `/following` and its dialog, the Preferences reduction, the standing preset default, and the `/tags` → `/following` link (phase 3 adds it, since the target does not exist yet).

**Task ordering is not optional.** Task 1 (data) → 2 (chips) → 3 (pills) → 4 (delete Characters, which depends on pills rendering characters in group rows) → 5 (delete table) → 6 (editor mode, which restores what Task 2 removed) → 7 (docs). **Tasks 2-5 leave editors unable to click through to a tag dialog; Task 6 closes that and must land in the same branch.**

**Where this plan is thinner than phase 1, on purpose.** Tasks 2, 3 and 6 give test docstrings and mutation names but leave the bodies to the implementer, because they depend on `tests/test_tags.py`'s fixture shapes which the implementer must read anyway. Every such test still names the mutation it must fail against — that is the part that must not be improvised.

**Two CSS interactions flagged for measurement rather than assertion**: whether `.tchip` has a border to keep the followed state from resizing the chip (Task 2 Step 4), and whether `.half { display: contents }` keeps `.mchip > *` matching (Task 3 Step 4). Both are the kind of thing this codebase has a standing rule about — measure, do not reason.
