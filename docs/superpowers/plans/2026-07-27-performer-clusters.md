# Performer Clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group the concert page's Performing chips by their attached GROUP tags, per `docs/superpowers/specs/2026-07-27-performer-clusters-design.md`.

**Architecture:** A service-side derivation (`performer_clusters`) reads the attached group/artist tags plus ONE batched `tag_members` query and returns ordered clusters; `concert_detail.html` renders label rows plus chips. Display only — attachment and group expansion (invariant 3) are untouched.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, FastAPI + Jinja2, babel gettext (ja/zh).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Suites run in the FOREGROUND. Accepted baseline: exactly 2 pre-existing env failures (`test_test_dm_when_bot_disabled`, `test_healthz`).
- Branch is `performer-clusters` (off `main`). Commit there; never switch branches.
- `Tag.members` must NEVER be touched during template rendering — it is a lazy self-referential m2m and a lazy load inside async rendering is a `MissingGreenlet` 500 this project has shipped once. All membership resolution happens in the service, in ONE query.
- Do NOT use the existing per-group `group_members(session, group_tag_id)` helper here — it would be an N+1.
- Tag attachment, group expansion and pruning (invariant 3) are OUT of scope: this reads the materialized set, never writes it.
- New user-visible strings `{% trans %}`/`ngettext`, hand-filled ja+zh, no fuzzy, plurals intact; run the pybabel cycle and delete `messages.pot`; remove any msgid this work orphans.
- CSS in the main body, or inside the existing `@media (max-width: 700px)` / `701-1040px` sections — no new top-level media query (guard pins 6). Radius 3px, existing tokens, both themes.
- Invariant 7: `| tojson` never `| safe`; no user text in inline `on*`; never `data-name`.
- Commit messages as given, plus `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `performer_clusters` service derivation

**Files:**
- Modify: `src/app/db/service.py` (the `# ── Tags ──` section, beside `group_members`)
- Test: `tests/test_concert_page.py` or `tests/test_tags.py` — whichever already has concert+tag seeding helpers; read both and pick, saying which in your report.

**Interfaces:**
- Consumes: `Concert.tags` (already eager-loaded by the detail route), `TagMember`, `TagKind`.
- Produces:

```python
@dataclass(frozen=True)
class PerformerCluster:
    group: Tag | None                 # None = the ungrouped trailer
    artists: tuple[Tag, ...] = ()

async def performer_clusters(session: AsyncSession, concert: Concert) -> list[PerformerCluster]
```

**Rules (spec §A, binding):**
1. One cluster per attached GROUP tag, in the order they appear in `concert.tags` (already `Tag.name`-ordered), holding the attached ARTIST tags that are its members.
2. An artist in several attached groups appears in EACH of their clusters (owner decision 1).
3. A trailing `group=None` cluster holds every attached artist in no attached group; omitted when empty.
4. A group with no attached members keeps its label row (empty `artists`).
5. Membership is read only for the attached group ids, in ONE query over `tag_members`.
6. Artists inside a cluster keep `concert.tags`' order (name order) — do not re-sort.

- [ ] **Step 1: Write the failing tests.** Seed a concert with two groups sharing a member, one artist in neither, and one group with no attached members:

```python
async def test_clusters_hold_each_groups_attached_members(session): ...
async def test_a_performer_in_two_groups_appears_in_both(session):
    ids = {c.group.id: [a.id for a in c.artists] for c in clusters if c.group}
    assert shared.id in ids[group_a.id]
    assert shared.id in ids[group_b.id]
async def test_ungrouped_artists_land_in_the_trailer(session):
    assert clusters[-1].group is None
    assert [a.id for a in clusters[-1].artists] == [solo.id]
async def test_the_trailer_is_omitted_when_every_artist_is_grouped(session):
    assert all(c.group is not None for c in clusters)
async def test_a_group_with_no_attached_members_keeps_its_label(session):
    assert any(c.group is not None and c.group.id == empty_group.id and c.artists == ()
               for c in clusters)
async def test_a_member_whose_group_is_not_attached_stays_in_the_trailer(session):
    # artist IS a member of a group tag, but that group is not attached here
    ...
async def test_membership_loads_in_one_query(session):
    # follow the statement-counting idiom already in tests/test_service.py
    # (before_cursor_execute listener); assert exactly ONE statement is
    # issued against tag_members regardless of group count — this is what
    # stops a future group_members() loop creeping back in.
```

- [ ] **Step 2: Run** — FAIL (ImportError).

- [ ] **Step 3: Implement.** Docstring must state (a) why the derivation is service-side (the `MissingGreenlet` trap), (b) that repetition across clusters is deliberate per owner decision 1, and (c) why `group_members` is deliberately not reused.

- [ ] **Step 4: Run** the test file, then the FULL suite + ruff.

- [ ] **Step 5: Commit** — `feat: derive performer clusters from attached group tags (task 1)`

---

### Task 2: Render the clusters

**Files:**
- Modify: `src/app/web/routes/concerts.py` (the concert-detail context), `src/app/web/templates/concert_detail.html`, `src/app/web/static/style.css`
- Modify: both `messages.po`
- Test: `tests/test_concert_page.py`

**Interfaces:**
- Consumes: Task 1's `performer_clusters` / `PerformerCluster`.
- Produces: template context key `performer_clusters: list[PerformerCluster]`.

**Current markup to replace** — `concert_detail.html`'s `.performers` panel currently renders `.plabel` with a composed header, then a flat `{% for g in cg %}` group-chip loop followed by `{% for a in ca %}` artist chips. The `cg`/`ca` locals stay in use by the lineage line above; only the panel body changes.

- [ ] **Step 1: Write the failing render tests** (this file's existing logged-in GET helpers):

```python
async def test_the_performing_panel_groups_chips_under_their_group(client, db): ...
async def test_a_two_group_performer_appears_under_both(client, db):
    assert body.count(">Shared Member<") == 2
async def test_the_header_counts_distinct_performers_not_the_sum(client, db):
    # 3 distinct artists, one of them in two groups -> "3", never "4"
    ...
async def test_a_concert_page_with_groups_renders(client, db):
    # the MissingGreenlet guard: a plain 200 with a lazy Tag.members touch
    # would be a 500 instead
    assert r.status_code == 200
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Route.** Add `performer_clusters` to the concert-detail context. It must be awaited in the route, never in the template.

- [ ] **Step 4: Template.** Replace the panel body with one cluster block per entry:

```jinja
{% for cluster in performer_clusters %}
<div class="pcluster">
  {% if cluster.group %}
  <div class="pclabel">
    {% if cluster.group.eventernote_url %}<a class="chip grp" href="{{ cluster.group.eventernote_url }}" target="_blank" rel="noopener">{{ loc(cluster.group, "name") }}</a>
    {% else %}<span class="chip grp nolink" title="{{ _('No eventernote link yet') }}">{{ loc(cluster.group, "name") }}</span>{% endif %}
  </div>
  {% endif %}
  <div class="chiprow">
    {% for a in cluster.artists %}{% if a.eventernote_url %}<a class="chip" href="{{ a.eventernote_url }}" target="_blank" rel="noopener">{{ loc(a, "name") }}</a>{% else %}<span class="chip nolink" title="{{ _('No eventernote link yet') }}">{{ loc(a, "name") }}</span>{% endif %}{% endfor %}
  </div>
</div>
{% endfor %}
```

The trailer cluster (`group` None) renders its chips with no label row. Keep the existing `.chip`/`.chip.grp`/`.nolink` classes and the eventernote link behaviour exactly — this task moves chips, it does not restyle them.

- [ ] **Step 5: Header.** Replace the three composed msgids in `.plabel`'s span with ONE plural-aware distinct count (e.g. `{n} performer` / `{n} performers`) over the distinct attached ARTIST tags — NOT the sum of cluster sizes. If the removed strings become orphans, delete them from both catalogues by hand (pybabel only comments them out as `#~`, which is uncaught drift).

- [ ] **Step 6: CSS.** `.pcluster` (vertical rhythm between clusters), `.pclabel` (the group row), `.chiprow` (wrap) in the main body near the existing `.performers`/`.chip` rules. If the phone or tablet sections need a counterpart it goes inside them. Then MEASURE, per this project's rule: seed a temp dev DB (never the repo's `app.db`), run web-only (empty `DISCORD_TOKEN`), and look at a two-group concert and a one-group concert at 375/730/1200 in both themes. Report what you measured.

- [ ] **Step 7: Catalogues**; delete `messages.pot`.

- [ ] **Step 8: Run** the test file + `tests/test_i18n_catalogues.py` + `tests/test_theme_and_tokens.py`, then the FULL suite + ruff.

- [ ] **Step 9: Commit** — `feat: render the Performing panel as per-group clusters (task 2)`

---

### Task 3: Closing sweep

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) + `uv run ruff check .`; record tallies.
- [ ] **Step 2:** Spec Status → implemented (2026-07-27) plus an "Implementation deviations (recorded)" section if any arose.
- [ ] **Step 3:** WISHLIST: move #1 to Shipped dated, house style, naming both owner decisions (repetition across groups; no folding); renumber the remaining Proposed; add the revision-pass paragraph; fix `#N` cross-references. Note in the revision pass whether shipping this changes the rank of anything else.
- [ ] **Step 4:** `docs/superpowers/demo/dekimasen-demo.html` is the design source of truth (CLAUDE.md) — reconcile its concert-page Performing panel with the shipped clusters, keeping it self-contained and on the same tokens.
- [ ] **Step 5:** CLAUDE.md UI conventions: one or two sentences on the clustered Performing panel and the rule that membership is resolved service-side because `Tag.members` is lazy.
- [ ] **Step 6: Commit** — `chore: performer clusters closing sweep (task 3)`
