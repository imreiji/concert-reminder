# Following Rework — Phase 3: `/following`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the reader one page listing what they follow, where a chip states its own deviation from their defaults and clicking it opens a dialog to change or drop that subscription.

**Architecture:** One new route and template, one new write route, and a config dialog reusing the app's existing `<dialog>` vocabulary. No schema change — `TagSubscription.preset_id`/`notify` and `ReminderPreset.is_default` all already exist.

**Tech Stack:** FastAPI, Jinja2, plain CSS, vanilla JS, SQLAlchemy async, pytest, Babel.

**Spec:** `docs/superpowers/specs/2026-08-12-following-rework-design.md`, §`/following` — sequencing step 3. Phases 1 and 2 are merged (PRs #151, #152).

**NOT in this phase:** the Preferences reduction and the standing-default UI with its retroactive fill. That is phase 4.

## Global Constraints

- Run everything from the repo root `E:\click clack clan\concert-reminder`.
- **Always `uv run --isolated`** — an external process holds a lock on `.venv`; never `uv sync`.
- **Run test commands in the FOREGROUND with `timeout: 900000`.** The suite takes 5-10 minutes and a backgrounded run has stalled implementers twice.
- **Baseline: 2825 passing**, `uv run --isolated ruff check .` clean, before any commit.
- **Sentence case everywhere.** Radiuses 3px default / 999px chips / 4px overlay cards / 50% circles — never 6px or 8px, there is a sweep test.
- **Two callout shapes and no third**: `.edgecard` (ongoing state) and `.banner` (needs attention).
- **Pickers and dialogs are native `<dialog>` white cards.** Backdrop-close comes ONLY from `base.html`'s global drag-safe handler — **never add a local `e.target === dlg` handler**; that shipped the drag-out-closes bug twice and a sweep test forbids it.
- **Never interpolate user-controlled text into an inline `on*` handler** (invariant 7). Tag names and preset names are user-controlled. Use `data-` attributes read via `dataset`.
- **Inline `<script>` data uses `| tojson`, never `| safe`**, and the context value stays a raw Python object — handing `tojson` the output of `json.dumps` double-encodes.
- **New user-visible strings need BOTH catalogues** filled by hand. `tests/test_i18n_catalogues.py` fails otherwise.
- **Invariant 8 is not to be touched**: following stays derived, `tracked_concert_ids` remains the single derivation. This phase adds no second derivation.
- **Header nav stays Home / Discover / Tags.** `/following` takes no nav slot; it is reached by link.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```

## What already exists — do not rebuild it

| Thing | Where |
|---|---|
| `TagSubscription.preset_id` (nullable FK) and `.notify` | `db/models.py:687,690` |
| `ReminderPreset.is_default` | `db/models.py:650` |
| `get_default_preset(session, user_id)` | `db/core.py:4117` |
| `my_presets(session, user_id)` — the viewer's presets | called at `routes/preferences.py:121`; **grep for its definition, it is imported from the facade** |
| `followed_tag_counts(session, user_id) -> {tag_id: (total, upcoming)}` | `db/core.py:1867` |
| `search_key(obj)` Jinja global (all three names) | `web/app.py` |
| `filterChips(input, scope)` with container hiding | `base.html` |
| `_safe_next` and its `_ALLOWED_NEXT` allowlist | `routes/preferences.py:82` |

**There is no route that sets a specific preset on a subscription.** `/subscriptions/{id}/auto-apply` only links the default or clears it. Task 4 adds one.

---

### Task 1: phase 2 leftovers and stale docs

**Files:**
- Modify: `src/app/web/templates/tags.html` (the `tag_chip` macro's followed branch)
- Modify: `src/app/db/tags.py` (the `summary` dict's `performers` count)
- Modify: `docs/superpowers/specs/2026-08-12-following-rework-design.md` (the sequencing section)
- Modify: `WISHLIST.md`
- Modify: `docs/ui-conventions.md`
- Test: `tests/test_tags_follow.py`, `tests/test_tags.py`

**Interfaces:** none produced or consumed. Fully independent of the rest of this plan.

**Two owner rulings, 2026-08-12**, both from reviewing phase 2:

- **A followed chip must keep its `unused` marking.** `tag_chip`'s followed branch omits `{% if unused %} unused{% endif %}` while the unfollowed branch has it, so a tag attached to zero events stops looking dead the moment you follow it. Both branches carried it before phase 2 — this was an accident of writing two branches, not a decision. The two facts are independent and both should show.
- **The header tally counts characters too.** `summary.performers` is `len(artists)` — ARTIST only — while the "Performers with no group" section now renders CHARACTER tags as well. Owner: characters and artists both count as performers.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags_follow.py`:

```python
async def test_a_followed_chip_keeps_its_unused_marking(client):
    """`.tchip.unused` (opacity .55, dashed border) is the signal that a tag is
    attached to nothing. Following a tag must not hide that -- the two facts
    are independent, and the tags most likely to be dead are the ones an
    editor is watching.

    Mutation this must fail against: dropping `unused` from tag_chip's
    FOLLOWED branch, which is how it shipped in phase 2.
    """
    ...
```

Seed a tag with **zero** concerts, follow it, re-render `/tags`, and assert the followed chip's class list contains both `on` and `unused`. Scope to that chip's own element — a page-wide `"unused" in r.text` would pass off any other chip.

And for the tally, add to `tests/test_tags.py` (or wherever `summary` is asserted — grep first):

```python
async def test_the_performer_tally_counts_characters_too(client):
    """Owner ruling 2026-08-12: characters and artists are both performers.
    The section below the tally renders both, so a tally that counts only
    ARTIST rows says a number the page contradicts.

    Mutation: reverting `performers` to len(artists).
    """
    ...
```

Seed one ARTIST and one CHARACTER, and assert the rendered tally reads 2.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Fix the chip**

In `tags.html`, `tag_chip`'s followed branch, add `{% if unused %} unused{% endif %}` to the button's class list, matching the unfollowed branch exactly. Read both branches and make them agree.

- [ ] **Step 4: Fix the tally**

`src/app/db/tags.py:1125` currently reads `"performers": len(artists),`. Change it to count ARTIST and CHARACTER tags. `tags` is in scope and already name-ordered:

```python
        # Owner ruling 2026-08-12: characters are performers too. The
        # "Performers with no group" section renders both kinds, so counting
        # only ARTIST here made the tally contradict the page beneath it.
        "performers": sum(
            1 for t in tags if t.kind in (TagKind.ARTIST, TagKind.CHARACTER)
        ),
```

Check whether `artists` is still used elsewhere in the function after this; remove it if not, or ruff will flag it.

- [ ] **Step 5: Correct the spec's sequencing section**

`docs/superpowers/specs/2026-08-12-following-rework-design.md`, the §Suggested sequencing block, says phase 4 must not precede phase 3 because *"it is the step that removes the only working follow path"*. **Phase 2 retired that argument** — `/tags` is that path now.

Replace the reason, keep the ordering. The live reason: phase 4 removes the per-tag notify/preset toggles from Preferences, and nothing replaces them until this phase's dialog exists. Mark it as a correction with the date, rather than silently rewriting — a spec that quietly changes its own reasoning teaches nobody.

- [ ] **Step 6: File the two deferred items in WISHLIST**

Add a short dated entry (or a note under the unranked "Following is due a rework" entry — your judgement, say which) recording:

- **An ungrouped character renders as a plain chip, not a split pill.** `/tags`'s "Performers with no group" section calls `tag_chip` directly rather than `member_chip`, so a lone character's seiyuu is invisible. Zero live instances — every one of the 318 characters is in a group. **Not a one-word fix**: `member_chip` hard-codes `count=none` because group-row members carry no counts, and this section does show them.

- [ ] **Step 7: Record the aria-live policy**

Owner ruling 2026-08-12: **state strips do not announce themselves.** The `/tags` edit-mode strip has no `aria-live`, and no other state strip in the app does either; a one-off would make the app inconsistent in a new way rather than fixing it. Record it in `docs/ui-conventions.md` beside the callout-grammar rules, so the next person does not re-raise it as an oversight.

- [ ] **Step 8: Run the tests, verify both mutations, full suite, lint, commit**

Revert each fix in turn and confirm its test fails. Then:

```bash
git add src/app/web/templates/tags.html src/app/db/tags.py docs WISHLIST.md tests
git commit -m "$(cat <<'EOF'
fix(tags): a followed chip still shows it is unused, and characters count

Two owner rulings from reviewing phase 2.

tag_chip's followed branch had lost `unused`, so a tag attached to zero
events stopped looking dead the moment you followed it -- and the tags
most likely to be dead are the ones an editor is watching. Both branches
carried it before phase 2; this was an accident of writing two branches.

And the header tally counted len(artists) while the section beneath it
renders characters too, so the number contradicted the page.

Also corrects the spec's sequencing reason, which phase 2 retired: /tags
is now the working follow path, so phase 4's hazard is removing the
per-tag toggles before phase 3's dialog replaces them.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
EOF
)"
```

---

### Task 2: a follow press swaps its own chip

**Files:**
- Create: `src/app/web/templates/_tag_chip.html` (the macro, extracted so a route can render one chip)
- Modify: `src/app/web/templates/tags.html`
- Modify: `src/app/web/routes/preferences.py` (the four `/subscriptions*` routes)
- Test: `tests/test_tags_follow_htmx.py` (new)

**Interfaces:**
- Produces: the follow/unfollow routes return **one rendered chip** to an htmx request and keep their 303 redirect otherwise. Task 3's `/following` page reuses this exactly.

**Why — owner report, 2026-08-12:** *"whenever a user clicks a tag to follow, the website will post the follow and refresh the entire website. This will bring the page back to top, not where the user is currently scrolled at, and the loading time after clicking each follow and unfollow is incredibly slow."*

Phase 2 shipped plain forms that POST and 303 back to `/tags`. On the live catalogue that re-renders **878 chips plus a `<dialog>` per tag for editors**, and throws away scroll position, on every single press. It is correct and unusable.

**This is a live regression on merged code and takes priority over the rest of this plan.** It also has to land before `/following` is built, or that page ships the same defect.

**The pattern already exists — copy it.** `src/app/web/templates/_capture_actions.html` puts `hx-post` / `hx-target` / `hx-swap="outerHTML"` on a form that *also* carries `method="post" action="..."`. htmx swaps one element in place; with JavaScript off the same markup is an ordinary form and the 303 still works. **Read that file before writing anything.** Do not invent a different mechanism, and do not drop the plain-form attributes — JS-off following is a property phase 2 was built to have and there is a test pinning it.

- [ ] **Step 1: Measure the current cost**

Before changing anything, seed a catalogue at realistic scale — **735 tags, 65 groups, 318 characters** is the live shape; a few hundred is enough to be honest — and record: the wall-clock time of `GET /tags`, and the response size. Then time a follow round-trip. **Report the numbers.** Without them "better" is unfalsifiable, and this project has a standing rule against reasoning about performance instead of measuring it.

- [ ] **Step 2: Write the failing tests**

```python
async def test_a_follow_from_htmx_returns_only_the_chip(client):
    """An htmx follow must swap one chip, not re-render 878 of them.

    Mutation this must fail against: the route ignoring HX-Request and
    redirecting, which still "works" and is what shipped in phase 2.
    """
    # POST with headers={"HX-Request": "true"}; assert 200 (not 303),
    # assert the body contains the chip's form and does NOT contain the page
    # shell (e.g. the search box or the section headings).


async def test_a_follow_without_htmx_still_redirects(client):
    """JS off keeps working. Mutation: making the htmx branch unconditional,
    which leaves a non-JS user staring at a bare chip fragment."""


async def test_the_swapped_chip_carries_the_opposite_action(client):
    """Following returns a chip offering unfollow, and vice versa -- otherwise
    the swapped chip is a dead end until a full reload."""
```

Plus: the returned fragment must carry `data-name="{{ search_key(t) }}"` so a swapped chip is still findable by search, and its `data-tag-id` so editor mode still works on it.

- [ ] **Step 3: Extract the chip macro to a partial**

`tag_chip` currently lives inside `tags.html` and closes over page context (`sub_by_tag`, `counts`, `user`). A route cannot render it there. Move it to `src/app/web/templates/_tag_chip.html` taking everything it needs as explicit parameters, and have `tags.html` import it. **The split-pill halves (`follow_half`) need the same treatment** — a pill half is also a follow control and must swap too.

Keep the rendered markup **byte-identical** to what phase 2 ships, or the existing tests will tell you (they pin the form action, the hidden fields, `data-name`, `data-tag-id` and the `unused` class). That is the point of having them.

- [ ] **Step 4: Make the routes htmx-aware**

The four routes are in `routes/preferences.py`: `/subscriptions` (:314), `/subscriptions/{id}/notify` (:355), `/subscriptions/{id}/auto-apply` (:371), `/subscriptions/{id}/delete` (:390).

Follow and unfollow are the two that matter here. On an htmx request (`HX-Request` header present) they return the re-rendered chip; otherwise they keep the existing `RedirectResponse`. Grep for how other routes in this codebase detect htmx — **there is an established way, use it** rather than reading the header raw if a helper exists.

**A route that returns a fragment must not also 303** — htmx would follow the redirect and swap the whole page in, which is worse than what we started with.

- [ ] **Step 5: Wire the template**

Add `hx-post`, `hx-target="this"`, `hx-swap="outerHTML"` to the chip forms, keeping `method="post"` and `action` exactly as they are.

- [ ] **Step 6: Measure again and compare**

Same seeded catalogue. Report the follow round-trip before and after, and the response size before and after. State plainly whether it is better and by how much.

- [ ] **Step 7: Confirm scroll position survives**

The original complaint. In a real browser: scroll to a chip well down the page, follow it, and confirm the page does **not** jump to the top and the chip updates in place. This is the acceptance criterion — a faster response that still scrolls to the top has not fixed the reported problem.

- [ ] **Step 8: Confirm JS-off still works**

Disable JavaScript, follow a tag, confirm the 303 path still lands you back on `/tags` with the chip updated.

- [ ] **Step 9: Full suite, lint, commit**

---

### Task 3: the `/following` page

**Files:**
- Create: `src/app/web/templates/following.html`
- Modify: `src/app/web/routes/preferences.py` — **verified**: every TAG subscription route lives there (`/subscriptions` at :314, `/notify` :355, `/auto-apply` :371, `/delete` :390). `routes/subscriptions.py` is a different thing entirely — it holds CONCERT subscription and leg opt-out routes (`/concerts/{event_id}/subscription`). Do not put tag routes there.
- Modify: `src/app/web/app.py` only if you give `/following` its own router. Putting the GET beside the tag-subscription routes in `preferences.py` needs no registration change; a new module does. Either is defensible — say which you chose and why.
- Modify: `src/app/web/static/style.css`
- Test: `tests/test_following_page.py` (new)

**Interfaces:**
- Produces: `GET /following`, `require_user`. Task 3 adds the dialog inside this template; Task 4 links to it.

**What it renders:** the viewer's subscriptions as **plain chips** — one chip per subscription, grouped by franchise. Not split pills: this page lists what you follow, and a subscription is one tag.

**The deviation markers.** A chip states how it differs from the viewer's defaults, and nothing else:

- **`notify` is False** → a muted 🔕 on the chip.
- **`preset_id` differs from the user's default preset** → the preset's name on the chip. That includes `preset_id is None` when a default preset *does* exist ("no preset"), and a named preset when no default exists.

Everything conforming renders plain. Scanning forty chips, only the exceptions draw the eye.

`get_default_preset(session, user_id)` (`db/core.py:4117`) returns the user's `is_default` preset or `None` — that is the comparison basis, and it exists today. **No schema change.**

**Grouping.** By franchise, the same shape `/tags` uses. A followed tag with no franchise ancestry goes in a final "Other" group. Derive the grouping in the route or a db helper, not the template.

**Search.** One `<input class="tag-search">` calling `filterChips(this, '.following-scope')`, exactly as `/tags` does. Chips carry `data-name="{{ search_key(t) }}"`; section wrappers carry `data-filter-container` so an emptied section hides. **No table view.**

**Empty state.** A user following nothing must get a sentence and a link to `/tags`, not a blank page.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_following_page.py` covering, each with a docstring naming its mutation:

- the page renders for a signed-in user and lists a followed tag
- a tag you do **not** follow does not appear
- a subscription with `notify=False` shows the muted marker; one with `notify=True` does not
- a subscription whose `preset_id` differs from the default preset shows that preset's name; one matching the default shows none
- the empty state renders a link to `/tags`
- signed out, `/following` bounces to `/` (the `LoginRequired` path — 303, and `HX-Redirect` + 204 for htmx)

Read `tests/test_preferences_page.py` and `tests/test_tags.py` for the fixture shapes and match them.

- [ ] **Step 2: Run to verify they fail** — the route does not exist.

- [ ] **Step 3: Build the route**

Grep for where subscription routes live and put it beside them. It must:

- `require_user`
- load the viewer's subscriptions joined to their tags
- call `get_default_preset` and `my_presets` (Task 3 needs the preset list; loading it here is fine)
- call `followed_tag_counts` for the context line Task 3 uses
- group by franchise
- pass `search_key`-ready Tag objects, not pre-rendered strings

**Do not add a second derivation of "what do I follow".** This page reads `TagSubscription` rows directly, which is what they are — explicit user edits. `tracked_concert_ids` answers a different question (which *concerts* am I tracking) and must not be touched.

- [ ] **Step 4: Build the template**

Follow `/tags`'s structure: a head, the search box, `.tsec` sections with `data-filter-container`, chips in a flow. Reuse `.tchip` — do **not** invent a new chip class.

- [ ] **Step 5: Style the deviation markers**

Two small rules. The preset-name marker should read as secondary to the tag name (the `.n2` count vocabulary is the precedent). The 🔕 marker should use the `--off` / `--off-wash` tokens, which is the app's existing "attention, but not danger" pair. Style against **both** light and dark.

- [ ] **Step 6: Both catalogues** for every new string.

- [ ] **Step 7: Browser check**

Seed a user following several tags across two franchises, with one `notify=False` and one carrying a non-default preset. Confirm: the markers appear only on the deviating chips; search filters and hides emptied sections; the empty state renders for a user following nothing.

- [ ] **Step 8: Full suite, lint, commit**

---

### Task 4: the per-tag config dialog

**Files:**
- Modify: `src/app/web/templates/following.html`
- Modify: `src/app/web/routes/preferences.py` — the new write route goes beside the other four `/subscriptions*` routes, and `_owned_subscription` (`:346`) is already there
- Modify: `src/app/web/static/style.css`
- Test: `tests/test_following_dialog.py` (new)

**Interfaces:**
- Consumes: Task 3's page and context.
- Produces: `POST /subscriptions/{sub_id}/settings`.

**The dialog holds three things, because three is all a subscription has:**

- **Reminder preset** — a `<select>` over the viewer's presets plus a "none" option. Writes `TagSubscription.preset_id`.
- **Notifications** — writes `TagSubscription.notify`.
- **Unfollow** — deletes the subscription. This is destructive; it goes in the footer, styled as the destructive action, and **must not** sit where a mis-click lands.

Plus a **context line**: the tag's event counts from `followed_tag_counts`, and where the tag is a CHARACTER with a seiyuu (or an ARTIST who voices one), a sentence naming the other half — so the decision can be made without leaving the dialog.

**One dialog per subscription**, rendered in a loop like `/tags`'s tag dialogs, opened by the chip. **The tag id reaches the opener through a `data-` attribute read via `dataset` — never interpolated into an `on*` handler.**

- [ ] **Step 1: Write the failing tests**

Cover, each naming its mutation:

- the dialog renders for each followed tag, with the viewer's presets as options and the current one selected
- `POST /subscriptions/{id}/settings` writes `preset_id` and `notify`
- **it 404s for a subscription belonging to someone else** — ownership checks 404, not 403, per invariant 5. This is the assertion that matters most.
- an invalid `preset_id` (one the viewer does not own) is refused — `owned_preset` is the existing guard; use it
- unfollowing from the dialog deletes the subscription and returns to `/following`
- the redirect target is pinned (`location == "/following"`), not just the 303

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Add the write route**

```python
@router.post("/subscriptions/{sub_id}/settings")
async def update_subscription_settings(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    preset_id: int = Form(0),
    notify: bool = Form(False),
    next_url: str = Form("/following", alias="next"),
):
```

It must use the existing `_owned_subscription` helper (grep — `/subscriptions/{id}/notify` uses it) so a foreign id 404s, and `owned_preset` for a non-zero `preset_id`. `preset_id == 0` means "none" and writes `None`.

**`/following` must be added to `_ALLOWED_NEXT`** in `routes/preferences.py:82`, or `_safe_next` silently bounces every save to `/preferences`. That exact bug shipped once already with `/tags`. Pin the redirect target in a test.

- [ ] **Step 4: Build the dialog**

Native `<dialog>`, reusing the app's existing dialog vocabulary (`.tagdlg` is the closest fit — read it). **No local backdrop-close handler**; `base.html`'s global drag-safe one covers it, and a sweep test forbids a local one.

- [ ] **Step 5: Wire the opener**

A capture-phase delegated listener, `stopPropagation()`, reading `dataset`. Note for the comment: `preventDefault()` alone *does* cancel a native submit from any phase — capture is used here because the listener must run before anything else can see or swallow the press, and `stopPropagation` because nothing else should act on it. `docs/architecture.md` carries the corrected model; do not reintroduce the false one.

- [ ] **Step 6: Both catalogues**

- [ ] **Step 7: Browser check** — open a dialog, change the preset, save, confirm the chip's marker updates; change notify; unfollow and confirm the chip disappears.

- [ ] **Step 8: Full suite, lint, commit**

---

### Task 5: link `/tags` to `/following`

**Files:**
- Modify: `src/app/web/templates/tags.html`
- Test: `tests/test_tags.py` or `tests/test_following_page.py`

Phase 2 deliberately omitted this because the target did not exist. The spec says `/following` is reachable from **both** Preferences and `/tags`; Preferences' entry point is phase 4, so this is the only door until then.

Put it where the follow counts already are — the page head is the natural home. It must be a real `<a href="/following">`, visible to every signed-in user, not editor-gated.

- [ ] **Step 1: Write the failing test** — `/tags` contains a link to `/following`, for a non-editor.
- [ ] **Step 2-4:** run red, add the link, run green.
- [ ] **Step 5:** both catalogues for the new string.
- [ ] **Step 6:** full suite, lint, commit.

---

### Task 6: bookkeeping

**Files:** `docs/architecture.md`, `WISHLIST.md`

- [ ] **Step 1: architecture.md**

Entries for: `/following` and what it owns (the page lists subscriptions directly; it is NOT a second derivation of invariant 8's tracked-concert question); the deviation-marker rule and that its comparison basis is `ReminderPreset.is_default`, which already existed; and `/following` being in `_ALLOWED_NEXT` with the reason (the `/tags` precedent, where its absence silently bounced every follow).

- [ ] **Step 2: WISHLIST**

Dated note under the unranked entry: phase 3 shipped, phase 4 remains, and the sequencing reason as corrected in Task 1.

- [ ] **Step 3: Full suite, lint, commit**

---

## Self-review notes

**Spec coverage.** Implements §`/following` in full. Task 1 additionally clears two owner rulings and three stale documents left by phase 2. Phase 4 (§Preferences, the standing default and its retroactive fill) is untouched.

**No migration.** Every column this phase writes already exists — `TagSubscription.preset_id`, `.notify`, and `ReminderPreset.is_default`.

**The `_ALLOWED_NEXT` trap is called out twice on purpose.** Its absence for `/tags` silently bounced every follow to `/preferences` from the 2026-07-24 UX pass until phase 2 found it. The same bug is one omitted line away here, and only a redirect-target assertion catches it.

**Task independence.** Task 1 is fully independent and could ship alone. Task 2 must land before Task 3, or /following ships the same full-page-reload defect. Tasks 3 → 4 are sequential (the dialog lives in the page). Task 5 needs Task 3's route to exist. Task 6 is last.

**Where this plan is deliberately less prescriptive than phase 1's.** Tasks 3 and 4 give the required behaviours, the mutations each test must catch, and the traps — but not full test bodies, because they depend on fixture shapes the implementer must read anyway. What must not be improvised is the list of mutations.
