# Mobile View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full mobile parity per `docs/superpowers/specs/2026-07-21-mobile-view-design.md` — compact header, bottom tab bar + editor FAB, swipeable board, two-line deadline rows, content-first Discover with a filter sheet, dialogs as bottom sheets, and single-column collapses everywhere, with desktop pixels untouched.

**Architecture:** Responsive retrofit (spec Option A). All mobile behavior is `@media (max-width: 700px)` rules appended to `src/app/web/static/style.css` plus additive template markup (tab bar, FAB, filter-sheet wrapper) that is `display: none` on desktop. The committed demos are the visual reference: `docs/superpowers/demo/dekimasen-mobile-demo.html` (frames; its `<style>` block holds the reference CSS values — copy dimensions/paddings from there) and `dekimasen-mobile-live.html` (interactions).

**Tech Stack:** Jinja2 templates, plain CSS (design tokens), no new dependencies, no JS beyond what exists (the filter sheet is a no-JS `<details>` mechanism).

## Global Constraints

- Desktop must not change by a pixel: every new CSS rule lives inside `@media (max-width: 700px)` (or extends an existing intermediate query); new template elements are hidden on desktop via CSS. Existing tests pass UNMODIFIED — they are the desktop-regression net.
- `uv run pytest -q` (known local-env failure: `tests/test_crud.py::test_test_dm_when_bot_disabled`; `tests/test_healthz.py::test_healthz` may flake on suite wall-time) and `uv run ruff check .` clean before every commit.
- Every NEW user-visible string is gettext-wrapped (`{{ _("...") }}`) AND added to BOTH catalogues (`src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`) — `tests/test_i18n_catalogues.py` fails otherwise. New msgids in this plan: `Me`, `Sign in`, `Filters`, `Add concert`. Translations: Me→ja `マイページ` zh `我的`; Sign in→ja `サインイン` zh `登录`; Filters→ja `絞り込み` zh `筛选`; Add concert→ja `コンサートを追加` zh `添加演出`.
- Tokens only in new CSS (`--paper --raise --ink --dim --line --accent --chip --shadow` + washes); both dark-mode paths then work for free.
- Touch targets ≥44px; dense informational chips ≥34px.
- Sentence case. Language names (EN/中文/日本語) never translated.
- Invariants 1/7 and the capture-gate UI conventions unchanged — this is presentation-only work.
- After template/CSS changes, verify at 390px via the same-origin iframe probe (Step "Visual check" in each task) — the dev server runs web-only with `$env:DISCORD_TOKEN=""; uv run python -m app.main`.
- Commit messages end with:
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YVsSGr7i7PxAbtPyLU7WoB

**Visual check procedure** (used by every task; needs no window resize): with the dev server running, from the browser console of any open page run:
```js
document.body.innerHTML=''; const f=document.createElement('iframe');
f.src='/PAGE'; f.style.cssText='position:fixed;top:0;left:0;width:390px;height:780px;border:2px solid red;background:#fff;z-index:9999';
document.body.appendChild(f);
```
then screenshot, and additionally assert no horizontal overflow: `f.contentDocument.documentElement.scrollWidth <= 390`. (Implementers without a browser: state so in the report; the controller runs the visual pass.)

---

### Task 1: Mobile scaffold — compact header, tab bar, FAB

**Files:**
- Modify: `src/app/web/templates/base.html` (header block ~lines 24-73; add tab bar + FAB before `</body>` content area)
- Modify: `src/app/web/static/style.css` (append a `/* ── Mobile (≤700px) ─...` section at end)
- Modify: both `messages.po` files (new msgids `Me`, `Sign in`, `Add concert` per Global Constraints)
- Test: `tests/test_mobile.py` (new)

**Interfaces:**
- Produces: `.tabbar` / `.tab` / `.fab` class names and the convention that `body` gets class `has-tabbar` — later tasks' CSS references them. `nav_page` values (`home`/`discover`/`tags`) drive `aria-current` exactly as the desktop nav does.

- [ ] **Step 1: Failing tests** — create `tests/test_mobile.py` reusing `tests/test_i18n_web.py`'s fixture shape (sync `TestClient` + `login()` helper; copy those ~55 lines of fixture code, it is the established per-file pattern):

```python
"""Mobile scaffold: tab bar, FAB, compact header markup (presence + gating)."""
# fixtures: copy the db/client/login trio from tests/test_i18n_web.py


def test_tabbar_signed_out(client):
    r = client.get("/")
    assert 'class="tabbar"' in r.text
    assert r.text.count('class="tab"') + r.text.count('class="tab" aria-current') >= 2  # Home, Discover
    assert "Sign in" in r.text            # third tab
    assert 'class="fab"' not in r.text    # FAB is editor-only


def test_tabbar_signed_in_marks_current(client):
    login(client)
    r = client.get("/discover")
    assert 'class="tabbar"' in r.text
    assert "Me" in r.text
    # active tab carries aria-current="page" like the desktop nav
    assert 'aria-current="page"' in r.text


def test_fab_editor_only(client):
    login(client)                          # plain user
    assert 'class="fab"' not in client.get("/").text
    # editor: tests/test_i18n_web.py's login gives a plain user; grep how
    # editor status is granted in tests (EDITOR_WHITELIST env or users.is_editor)
    # and reuse that mechanism here, then:
    # assert 'class="fab"' in client.get("/").text
```

(Resolve the editor-grant mechanism from existing tests — `rg "is_editor|EDITOR_WHITELIST" tests/ | head` — and complete the third test with it; it must actually assert the positive case.)

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_mobile.py -q` → no `tabbar` in output.

- [ ] **Step 3: Implement base.html** — inside `<nav class="auth">` nothing changes. AFTER `</header>` (and after the dm-blocked banner block), add:

```jinja
  {# Mobile-only bottom tab bar (spec §2). Hidden on desktop by CSS; the
     active item carries aria-current="page", same contract as nav.main.
     Me routes to /preferences — the phone home of the auth cluster. #}
  <nav class="tabbar">
    <a class="tab" href="/"{% if nav_page == "home" %} aria-current="page"{% endif %}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11 12 4l9 7v8a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>{{ _("Home") }}</a>
    <a class="tab" href="/discover"{% if nav_page == "discover" %} aria-current="page"{% endif %}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4.5 4.5"/></svg>{{ _("Discover") }}</a>
    {% if user %}
    <a class="tab" href="/tags"{% if nav_page == "tags" %} aria-current="page"{% endif %}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12V5a1 1 0 0 1 1-1h7l8 8-8 8z"/><circle cx="8.5" cy="8.5" r="1.3"/></svg>{{ _("Tags") }}</a>
    <a class="tab" href="/preferences">
      {% if user.avatar_url %}<img class="ava" src="{{ user.avatar_url }}" alt="">{% else %}<span class="ava"></span>{% endif %}{{ _("Me") }}</a>
    {% else %}
    <a class="tab" href="/auth/login">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 6-5 8-5s6.5 1 8 5"/></svg>{{ _("Sign in") }}</a>
    {% endif %}
  </nav>
  {% if user and user.is_editor and nav_page in ("home", "discover") %}
  <a class="fab" href="/concerts/new" title="{{ _("Add concert") }}">+</a>
  {% endif %}
```

Check `SessionUser` exposes `is_editor` and `avatar_url` (`rg "is_editor|avatar_url" src/app/web/auth.py`) — adapt attribute names to what exists.

- [ ] **Step 4: Implement CSS** — append to `style.css` (reference values: the committed demo's `<style>`, sections `.m-head`, `.tabbar`, `.tab`, `.fab`):

```css
/* ── Mobile (≤700px) ─────────────────────────────────────────────────────
   Spec: docs/superpowers/specs/2026-07-21-mobile-view-design.md; visual
   reference: docs/superpowers/demo/dekimasen-mobile-demo.html. Everything
   below this banner is phone-only — desktop must not change by a pixel. */
.tabbar, .fab { display: none; }
@media (max-width: 700px) {
  /* compact header: one row, wordmark on one line, auth cluster leaves */
  header.site .site-in { flex-wrap: nowrap; gap: .55rem; }
  header.site .home .mark { font-size: .95rem; white-space: nowrap; }
  header.site nav.main { display: none; }         /* nav lives in the tab bar */
  header.site nav.auth > .avatar,
  header.site nav.auth > span,
  header.site nav.auth > .btn { display: none; }  /* theme + lang chip stay */

  body { padding-bottom: 4.6rem; }                /* content clears the bar */

  .tabbar {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 20;
    display: grid; grid-auto-flow: column; justify-items: center;
    background: var(--raise); border-top: 1px solid var(--line);
    padding: .45rem 0 calc(.5rem + env(safe-area-inset-bottom, 0px));
  }
  .tab {
    display: grid; justify-items: center; gap: .12rem; text-decoration: none;
    color: var(--dim); font-size: .68rem; letter-spacing: .02em;
    min-width: 64px; padding: .15rem .5rem; border-radius: 6px;
  }
  .tab svg { width: 22px; height: 22px; stroke: currentColor; fill: none; stroke-width: 1.7; }
  .tab[aria-current="page"] { color: var(--accent); font-weight: 650; }
  .tab .ava, .tab img.ava {
    width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--line);
    background: linear-gradient(135deg, var(--accent-wash), var(--chip));
  }
  .fab {
    position: fixed; right: 1rem; bottom: calc(4.6rem + env(safe-area-inset-bottom, 0px));
    z-index: 20; width: 3.1rem; height: 3.1rem; border-radius: 50%;
    background: var(--ink); color: var(--paper); font-size: 1.6rem; line-height: 1;
    box-shadow: var(--shadow); display: grid; place-items: center;
    text-decoration: none; padding-bottom: .2rem;
  }
}
```

Check the real header selectors first (`rg "class=\"home\"|class=\"mark\"|site-in" src/app/web/templates/base.html`) and adjust; the auth-cluster hiding must NOT hide `.theme-toggle` or `.langform`.

- [ ] **Step 5: Catalogues** — add the three msgid/msgstr pairs (Global Constraints table) to both `.po` files, keeping alphabetical-ish placement near similar strings. Run `uv run pytest tests/test_i18n_catalogues.py -q` → PASS.

- [ ] **Step 6: Tests green + gates** — `uv run pytest tests/test_mobile.py -q` PASS; full suite + ruff clean (existing tests untouched — the tab bar is additive markup).

- [ ] **Step 7: Visual check** — dev server; iframe probe on `/` signed-out at 390px: one-line wordmark, tab bar visible with Sign in, no horizontal overflow, and desktop (full-width page) unchanged. Screenshot both.

- [ ] **Step 8: Commit** — `mobile: scaffold — compact header, tab bar, FAB`

---

### Task 2: Home — board carousel + two-line deadline rows

**Files:**
- Modify: `src/app/web/static/style.css` (inside the `@media (max-width: 700px)` mobile section)
- Test: existing suite only (CSS-only task; `tests/test_home.py` is the no-change net)

**Interfaces:**
- Consumes: Task 1's mobile section banner in style.css.
- Produces: nothing new — reshapes existing `.board`/`.bcol`-equivalent classes. Find the REAL class names first: `rg "class=\"(board|column|col |row\b|rowhead)" src/app/web/templates/home.html src/app/web/templates/_board.html src/app/web/templates/_deadline_rows.html`.

- [ ] **Step 1: Board carousel** — inside the 700px block, transpose the demo's `.board-swipe`/`.bcol` values onto the app's real board container/column classes:

```css
  /* campaign board → center-snap carousel (spec §4; demo .board-swipe) */
  .board {                     /* ← real container class from _board.html */
    display: grid; grid-auto-flow: column; grid-auto-columns: 78%;
    gap: .8rem; overflow-x: auto; scroll-snap-type: x mandatory;
    padding: 0 11% .5rem; scrollbar-width: none;
  }
  .board::-webkit-scrollbar { display: none; }
  .board > * { scroll-snap-align: center; }
```

(Keep whatever desktop grid rule exists untouched — this OVERRIDES inside the media query only. If the desktop board uses `grid-template-columns: repeat(4, 1fr)`, neutralize it here with `grid-template-columns: none`.)

- [ ] **Step 2: Deadline rows → two-line cards** — per demo `.m-row`: hide the `.rowhead`, turn each `.row` into a bordered card, stack what/when, make capture buttons full-width 44px. Use the REAL row-grid class names; the htmx swap target ids must not change.

- [ ] **Step 3: Up next + teaser + peek grid** — single-column (`grid-template-columns: 1fr`) for `.next`, `.teaser`, `.peek`, landing `.grid` if not already covered by the existing 600px rules; RECONCILE with the pre-existing `@media (max-width: 600px)` block in style.css (~line 698) — move/merge those rules into the 700px section so there is ONE phone breakpoint (spec: "one primary phone breakpoint: 700px"), deleting the 600px query after confirming every rule migrated.

- [ ] **Step 4: Gates** — full suite + ruff (CSS-only → no test changes).

- [ ] **Step 5: Visual check** — probe `/` (signed-out landing: sample board swipes centered, no overflow) at 390px; desktop unchanged at full width. A signed-in board check needs a logged-in browser session — state in the report if not done; the controller covers it in the final pass.

- [ ] **Step 6: Commit** — `mobile: home — board carousel, deadline row cards`

---

### Task 3: Discover — content-first + filter sheet

**Files:**
- Modify: `src/app/web/templates/discover.html` (wrap the sidebar in the `<details>` sheet; move it after the content column in DOM order if it precedes it)
- Modify: `src/app/web/static/style.css`
- Modify: both `messages.po` (new msgid `Filters`)
- Test: `tests/test_mobile.py` (append), `tests/test_discover.py` is the no-change net

**Interfaces:**
- Consumes: Task 1 scaffold.
- Produces: `.fsheet` (the `<details>` wrapper), `.fsheet-panel`, used only here.

- [ ] **Step 1: Failing test** — append to `tests/test_mobile.py`:

```python
def test_discover_filter_sheet_contains_controls(client):
    r = client.get("/discover")
    assert 'class="fsheet"' in r.text
    # the sheet holds the relocated sidebar controls (sort + facet + tags)
    body = r.text
    sheet = body.split('class="fsheet"')[1]
    assert "Filters" in body
    for fragment in ("sort=", "status="):   # the existing GET filter links
        assert fragment in sheet
```

(Adapt the asserted fragments to what the sidebar really renders — read discover.html first; the test must prove the controls moved INSIDE the fsheet wrapper, not disappear.)

- [ ] **Step 2: Template** — in `discover.html`, wrap the ENTIRE existing sidebar block in:

```jinja
<details class="fsheet">
  <summary class="btnq-summary">{{ _("Filters") }}{% if active_filter_count %} ({{ active_filter_count }}){% endif %}</summary>
  <div class="fsheet-panel">
    ...existing sidebar markup, unchanged...
  </div>
</details>
```

If the template exposes no `active_filter_count`, derive it in the template from what it already has (`selected_tags | length + (1 if status else 0)`) — no route changes. If the sidebar currently precedes the content column in the DOM, move the whole `<details>` AFTER the content column and let desktop CSS place it back in the sidebar grid area (check the `.layout`/`.plyt` grid: `grid-template-areas` or column order — if the desktop grid relies on DOM order, keep DOM order and instead use `order:` on the flex/grid children under the breakpoint; pick whichever preserves desktop exactly, and say which you chose in the report).

- [ ] **Step 3: CSS** — desktop: `.fsheet > summary { display: none; }` and `.fsheet` renders as the plain sidebar block (`details` is open? No — a closed `<details>` hides its content). Therefore the template must render `<details class="fsheet" open>` and desktop CSS hides the summary; mobile CSS RE-CLOSES it by default? A `<details open>` cannot be closed by CSS. Resolution (the no-JS-safe mechanism): render `open` ONLY for desktop is impossible server-side (no viewport knowledge) — so use the one-line JS-free trick: render `<details class="fsheet" open>` and in the MOBILE media query style `.fsheet:not([open])` normally; add a tiny inline script (base-page level, 3 lines, progressive enhancement) that closes it under 700px on load: `if (matchMedia('(max-width:700px)').matches) document.querySelectorAll('.fsheet[open]').forEach(d=>d.removeAttribute('open'))`. With JS off, mobile users see the filters expanded in-flow after the content column (moved in Step 2) — fully functional, just not sheet-shaped: this IS the no-JS fallback the spec requires. Mobile CSS for the open sheet (values from demo `.sheet`):

```css
  .fsheet > summary { list-style: none; cursor: pointer; }
  .fsheet > summary::-webkit-details-marker { display: none; }
  /* desktop: plain sidebar, summary hidden */
  @media (min-width: 701px) { .fsheet > summary { display: none; } }
  @media (max-width: 700px) {
    .fsheet > summary {   /* the Filters button, .btnq-summary look */
      display: inline-flex; align-items: center; min-height: 36px;
      padding: .25rem .75rem; border: 1px solid var(--line); border-radius: 3px;
      font-size: .87rem; color: var(--dim); background: none;
    }
    .fsheet[open] > summary::after {  /* dim overlay behind the sheet */
      content: ""; position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 24;
    }
    .fsheet[open] .fsheet-panel {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 25;
      max-height: 78dvh; overflow-y: auto; background: var(--raise);
      border-radius: 14px 14px 0 0; box-shadow: var(--shadow);
      padding: 1rem 1.1rem calc(1.2rem + env(safe-area-inset-bottom, 0px));
    }
  }
```

(The overlay-on-summary trick keeps "tap outside to close" working: the fixed overlay IS the summary's pseudo-element, so tapping it toggles the details closed. Verify this actually closes on tap — a `summary::after` receives clicks for the summary. If it misbehaves in testing, fall back to overlay-without-close + the summary button reading "Close" while open via `.fsheet[open] > summary { ... }` repositioned above the sheet.)

- [ ] **Step 4: Content order + tiles** — mobile: content column single-column tiles (likely covered by existing 960/600 rules — migrate into 700 section as in Task 2 Step 3); search row + filter line spacing per demo frames 3-4.

- [ ] **Step 5: Catalogues** — add `Filters` msgid to both `.po` files; hygiene test green.

- [ ] **Step 6: Gates** — new test green, `tests/test_discover.py` UNMODIFIED green (the sidebar controls still render — moved, not removed), full suite + ruff.

- [ ] **Step 7: Visual check** — probe `/discover` at 390px: tiles on screen one, Filters button opens the sheet, overlay closes it, no overflow; desktop full-width: sidebar identical to before (screenshot-compare by eye).

- [ ] **Step 8: Commit** — `mobile: discover — content first, filter bottom sheet`

---

### Task 4: Bottom-sheet dialogs, concert page, tags page

**Files:**
- Modify: `src/app/web/static/style.css` only (CSS-only task)
- Test: existing suite is the net

- [ ] **Step 1: Dialogs → sheets** — global rule in the 700px section, transposed from demo `.sheet` (find the app's dialog classes: `rg "\<dialog" src/app/web/templates -l`):

```css
  dialog {           /* every native dialog becomes a bottom sheet */
    inset: auto 0 0 0; width: 100%; max-width: none; margin: 0;
    border: 0; border-radius: 14px 14px 0 0; max-height: 78dvh;
    box-shadow: var(--shadow);
  }
```

Check each dialog's own width/margin rules for specificity conflicts (picker dialogs may set `width:`/`max-width:` on a class — override those class selectors inside the media query, not with `!important`).

- [ ] **Step 2: Concert page** — stack per demo frame 5: full-width Following toggle, round-group cards single-column, capture buttons flexed 44px, performer chip row wraps (probably already does). Real class names from `concert_detail.html`/`_round_rows.html`/`_capture_actions.html`.

- [ ] **Step 3: Tags page** — chip families wrap (they should already), dialogs covered by Step 1, touch-height chips (`min-height: 34px`) for the counted chips, single-column for the `.tsec` grids if any are multi-column.

- [ ] **Step 4: Gates** — full suite + ruff.

- [ ] **Step 5: Visual check** — probe a concert page and `/tags` (needs login for tags — report if not reachable; controller covers). Dialog check: open the tag picker on desktop (unchanged) — mobile dialog rendering verified in the controller's final pass.

- [ ] **Step 6: Commit** — `mobile: sheets, concert page, tags`

---

### Task 5: Forms batch — editor, import, preferences, setup, welcome, legal + overflow audit

**Files:**
- Modify: `src/app/web/static/style.css` (CSS-only)
- Test: existing suite is the net

- [ ] **Step 1: Editor + concert_new + import** — single-column form grids (find the grid classes: `rg "grid-template-columns" src/app/web/static/style.css | head -30` and identify which serve the editor/import forms), stacked day/round cards, wrapped leg/qualifier chips at ≥34px, full-width Save/danger buttons (spec §9, demo frame 6).
- [ ] **Step 2: Preferences** — stacked rows per demo frame 7 (`.prow` reference): the left-rail layout collapses (existing 860px rule — migrate to 700 per the one-breakpoint rule ONLY if it purely concerns phones; the 860 rail-collapse is tablet-legit, keep it and add phone polish in the 700 block).
- [ ] **Step 3: Setup, welcome, legal, retroactive** — tiles single-column, wizard cards stack, `.legal` prose max-width already fluid; check `.lede`, `.revealstats`, wizard step controls.
- [ ] **Step 4: Overflow audit** — with the dev server up, iframe-probe EVERY signed-out page (`/`, `/discover`, `/privacy`, `/terms`, a concert page) at 390px asserting `scrollWidth <= 390`; grep for fixed widths that could overflow: `rg "width: ?[4-9][0-9]{2,}px|min-width: ?[4-9][0-9]{2,}px" src/app/web/static/style.css` and fix any that apply under 700px (typical fix: `max-width: 100%`).
- [ ] **Step 5: Gates** — full suite + ruff.
- [ ] **Step 6: Commit** — `mobile: forms, preferences, setup/welcome/legal, overflow audit`

---

### Task 6: Demo fix, docs, final polish

**Files:**
- Modify: `docs/superpowers/demo/dekimasen-demo.html` (~line 715: the unclosed `@media (max-width: 700px) {` — determine the author's intent from the rules inside (they look like they belong at top level with their OWN breakpoints) and fix the brace structure so 1024/960/600 queries are top-level again; verify by loading the demo and resizing)
- Modify: `CLAUDE.md` (UI conventions: the tab bar/FAB/sheet patterns, the one-phone-breakpoint rule, the "desktop pixels untouched" retrofit convention, demo refs)
- Modify: `WISHLIST.md` (move nothing — mobile wasn't a listed entry; do the standing revision pass: re-rank considering mobile shipped; new-idea candidates from this build: PWA/installability)
- Test: full suite

- [ ] **Step 1: Fix the demo's media-query nesting**; sanity-load it in a browser at narrow width.
- [ ] **Step 2: CLAUDE.md + WISHLIST** per above (tight prose, why included, house style).
- [ ] **Step 3: Gates** — full suite + ruff.
- [ ] **Step 4: Commit** — `mobile: demo media-query fix + docs`

---

## Self-review notes (applied)

- Spec coverage: §1→T1, §2→T1, §3→T1, §4→T2, §5→T2, §6→T3, §7→T4, §8→T4, §9→T5, §10→T5, §11→T4, §12→T5; cross-cutting i18n→T1/T3 catalogues; demo fix + docs→T6. Landing hero scaling: existing `clamp()` — T5 Step 3 verifies.
- The filter-sheet mechanism (spec's delegated decision) is pinned here: `<details open>` + matchMedia-close enhancement; no-JS mobile = filters expanded in-flow after content. The 3-line script is the plan's ONE new JS.
- Real class names are deliberately resolved by implementers via the listed `rg` commands (the plan's CSS shows the values and shape; the demo in-repo carries the reference dimensions).
