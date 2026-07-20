# Demo Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shipped UI match the frozen concept demo — port the missing design-token layer (incl. dark mode), restore each view's lost components, and wire the one control the demo promised but shipped stubbed (delete account).

**Architecture:** Three ordered layers — a global `style.css` token pass first (fixes most drift everywhere at once), then per-view component gaps, then the delete-account route. A cross-cutting time-format reshape lands early so per-view work renders correctly.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, SQLite, pytest. No new deps.

**Spec:** `docs/superpowers/specs/2026-07-20-demo-reconciliation-design.md`

**Reference implementation:** `docs/superpowers/demo/dekimasen-demo.html` (2287 lines). This is the
source of truth for markup and CSS — **port from it, do not redesign.** View line ranges:

| View | Demo lines | Primary shipped files |
|---|---|---|
| head / tokens / CSS / header | 1-765 | `static/style.css`, `templates/base.html` |
| home | 766-981 | `home.html`, `_board.html`, `_board_summary.html`, `_deadline_rows.html` |
| setup | 982-1151 | `setup.html` |
| concert | 1152-1287 | `concert_detail.html`, `_round_rows.html`, `_following_toggle.html`, `_rules.html` |
| editor | 1288-1546 | `concert_edit.html`, `_round_leg_chips.html`, `_round_qualifier_chips.html`, `_tag_picker_fields.html` |
| prefs | 1547-1736 | `preferences.html` |
| tags | 1737-1926 | `tags.html`, `_tag_picker_fields.html`, `_tag_picker_script.html` |
| discover | 1927-2287 | `discover.html` |

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit. Both are CI gates.
- Baseline: run the suite first and record the real number. A repo-root `.env` with a real `DISCORD_TOKEN` makes `tests/test_crud.py::test_test_dm_when_bot_disabled` fail locally only — pre-existing, CI-green, **OUT OF SCOPE**. Verify against reality, not this plan's arithmetic.
- TDD where testable: for CSS/markup, the test discipline is a render/parity test (assert the class/token/markup is emitted), matching the project's "every page a logged-in GET render test" rule.
- **No schema change.** `service.delete_user` already exists (`db/service.py:110`).
- Invariant 1: times stay dual, JST first; aware-UTC in the DB. The reshape is presentation only.
- Invariant 7: editor URLs via `form_url`; picker data `| tojson` never `| safe`; no user-controlled text in inline `on*` handlers; `data-name` collides with `base.html`'s `filterChips()` — use `data-tag-name`/`data-preset-name`.
- `routes/imports.py` stays registered before `routes/concerts.py` in `web/app.py`.
- `src/app/domain/` stays pure — the time formatter is pure string logic.
- CSS comments ASCII-only (owner's GBK locale). Sentence case throughout.
- DB fixtures register the `PRAGMA foreign_keys=ON` connect listener.
- **Do NOT revert** the three deliberate divergences (spec "Deliberately NOT reverted"): dual times (reshape only), the merged Discover status pill, the "Up next" header.

## Ordering & concurrency

Tasks 1 and 2 are foundational and must land first (everything visual depends on the tokens; the
per-view tasks render times through the new formatter). Tasks 3-9 each own a different view's
templates but all may append to `style.css` — run them **sequentially** on this branch to avoid
`style.css` merge conflicts, or if parallelising in worktrees, give each a clearly delimited
view-scoped CSS block. Task 10 (docs) is last.

## File Structure

| File | Responsibility |
|---|---|
| `static/style.css` (modify, all tasks) | The token layer (Task 1) + each view's restored rules. |
| `templates/base.html` (modify) | Sticky/pill header, theme toggle control + script. |
| `domain/timezones.py` (modify) | Two-line dual-time formatter (Task 2). |
| `templates/*.html` (modify, per view) | Restored markup per Tasks 3-8. |
| `web/routes/preferences.py` (modify) | Delete route + Following/Delivery/Time context (Tasks 8-9). |
| `templates/preferences.html` (rewrite) | Rebuilt on the demo vocabulary (Task 8). |

---

### Task 1: Global token pass + theme toggle

**Files:**
- Modify: `src/app/web/static/style.css`, `src/app/web/templates/base.html`
- Test: `tests/test_theme_and_tokens.py` (new)

**Port from demo lines 1-64** (the `:root`, `@media (prefers-color-scheme: dark)`,
`:root[data-theme="dark"]`, `:root[data-theme="light"]` blocks, `.num`, `.eyebrow`) and demo lines
48-64 + the `header.site`/`.site-in`/`nav.main` rules (demo ~50-90) for the header.

- [ ] Step 1: Write failing tests in `tests/test_theme_and_tokens.py`:
  - `test_style_defines_previously_missing_tokens` — read `static/style.css`, assert it contains `--raise`, `--chip`, `--shadow`, `--ok-wash`, `--off-wash`, `--danger-wash`, `--accent-wash`. (Guards the fallback-to-white regression.)
  - `test_style_defines_both_theme_directions` — assert both `@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]` and `:root[data-theme="light"]` blocks are present.
  - `test_style_uses_3px_radius_not_6or8` — assert `border-radius: 6px` and `border-radius: 8px` no longer appear (or appear 0 times) in the button/input/card/dialog rules; `3px` is used.
  - A `base.html` render test (via the existing test client / a logged-in GET on `/`) asserting the theme-toggle control is emitted (e.g. an element with `data-theme-toggle`) and the nav renders as `nav.main` with pill-eligible markup.
- [ ] Step 2: Run them, confirm they fail for the right reason.
- [ ] Step 3: Implement:
  - Replace the `:root` block in `style.css` with the demo's full token set; add the three theme blocks verbatim from the demo (exact hexes).
  - Port `.num`, `.eyebrow`; set `body` font to include `-apple-system`.
  - Make `header.site` sticky with a `.site-in` max-width wrapper matching the content column; restyle `nav.main a` as pills with `var(--chip)` hover + active (`aria-current`) background (demo `nav.main`/`nav.main a` rules).
  - Change button/input/card/dialog `border-radius` to `3px` app-wide; set `.chip` to `background: var(--chip)`, transparent border, `.on { background: var(--accent) }`, add hover `transition`.
  - Add the theme toggle to the header in `base.html`: a button that reads `localStorage.theme` (default: unset → OS preference via the media query) and stamps `:root[data-theme=...]` on click, persisting to localStorage. Small inline script; no external deps; must run before first paint to avoid a flash (place the read-and-stamp snippet in `<head>`).
- [ ] Step 4: Run tests → pass. Load `/` and eyeball both themes (OS toggle) — this is the visual foundation for every later task.
- [ ] Steps 5-6: `uv run pytest -q` + `uv run ruff check .`, commit.

```bash
git commit -m "Port the demo token layer, dark mode, and sticky pill header"
```

---

### Task 2: Two-line dual-time formatter

**Files:**
- Modify: `src/app/domain/timezones.py`
- Test: `tests/test_timezones.py` (extend)

**Interfaces produced:** a pure formatter returning the demo's two-line shape — a bold
weekday+day+month line and a `HH:MM JST · HH:MM <user-zone>` line — WITHOUT reverting to terse
relative placeholders. Decide the exact signature during implementation (either extend `fmt_dual`
with a structured return, or add `fmt_dual_lines(utc, tz) -> tuple[str, str]`); whichever, the
existing `fmt_dual` callers must keep working or be migrated in this task. Say which you chose in the
report.

- [ ] Step 1: Failing test: given a known UTC instant and a user tz, the formatter returns a
  weekday+day+month string (e.g. "Sat 1 Aug") and a time string containing BOTH "JST" and the user
  zone abbreviation, in that order. Pin invariant 1: both zones always present.
- [ ] Step 2: Confirm fail.
- [ ] Step 3: Implement the pure formatter. No I/O, no fastapi/sqlalchemy imports.
- [ ] Step 4: Migrate the call sites that should show the two-line shape — Home "Up next"
  (`home.html`), board (`_board.html`), Coming up (`_deadline_rows.html`), concert page
  (`_round_rows.html`), setup (`setup.html`) — to render the two lines with the demo's markup
  (bold weekday `<b>`/`display:block` line + small secondary line). Leave `.ics`/YAML export
  formatters untouched (they are not the demo's concern).
- [ ] Steps 5-6: suite + lint, commit.

```bash
git commit -m "Render dual times in the demo's two-line shape"
```

---

### Task 3: Home view gaps

**Files:**
- Modify: `src/app/web/templates/home.html`, `_board.html`, `_board_summary.html`, `_deadline_rows.html`, `static/style.css`
- Modify: `src/app/web/app.py` (the `/` handler — needs the open-round count and 4 peek cards in context)
- Test: `tests/test_home_page.py` (extend)

Port from demo lines 766-981. Restore:
- The **peek grid** — 4 sample Discover cards under the teaser (demo `.peek`); handler supplies 4 cards.
- The **foot-note** paragraph (demo `.foot-note`).
- The teaser's **"N with a round still open"** clause (handler supplies the open-round count).
- Board **"Won — pay" card** `border-left: 3px solid var(--accent)` accent (demo card variant).
- Board **countdown pill tone reflects urgency** (time-remaining), not only the column — port the demo's per-card pill-tone logic; keep it a read-side presentation detail.
- The **`.eyebrow`** class on board-card and tile artist/franchise names (was plain).
- Two-tier "Up next" countdown (big number + `.unit` caption) — demo `.next .big`/`.unit`.

- [ ] Step 1: Failing render tests: `/` emits a `.peek` grid with 4 cards; emits the foot-note; the teaser context includes an open-round count; a Won-column card carries the accent class; board/tile artist names use `.eyebrow`.
- [ ] Steps 2-6: confirm fail, implement (handler + templates + CSS), verify, suite+lint, commit.

```bash
git commit -m "Restore Home's peek grid, foot-note, board accents and eyebrow labels"
```

---

### Task 4: Concert page gaps

**Files:**
- Modify: `src/app/web/templates/concert_detail.html`, `_following_toggle.html`, `_round_rows.html`, `_rules.html`, `static/style.css`
- Test: `tests/test_concert_page.py` (extend)

Port from demo lines 1152-1287. Restore:
- **`.follow` toggle CSS** — the green "covered" pill with checkmark and the dim/outline unfollowed state (demo `.follow`). Add the caption "You will be reminded about every round below." to `_following_toggle.html`.
- **`.performers .chip { justify-content: center; text-align: center }`** — the owner's explicit centering ask (demo `.performers .chip`).
- **"My reminders" redesign** (`_rules.html`) into the demo's row-based layout: a "From your default preset — <name>" note, reminder rows with a small "Remove" action, and an "Add a reminder" affordance that opens the form (not an always-open inline form). Preserve the existing rule add/delete routes and `render_rules_fragment` — this is presentation only.
- Remove the stray legacy `meta-grid` / `performers_text` block (demo header has lineage → h1 → tags → links only).
- `.nolink` dim treatment on performer chips with no eventernote link (demo `.performers .chip.nolink`).

- [ ] Step 1: Failing tests: the concert page emits `.follow` markup + the reminder caption; `.performers .chip` centering rule exists in `style.css`; the reminders section renders the row-based layout with an "Add a reminder" control and no always-open `<select>`; the legacy `meta-grid` block is gone.
- [ ] Steps 2-6: confirm fail, implement, verify (keep the audit `snapshot`-before/`record`-after ordering untouched — this task doesn't touch `edit_concert`), suite+lint, commit.

```bash
git commit -m "Fix the follow toggle, centre performer chips, redesign My reminders"
```

---

### Task 5: Discover view gaps

**Files:**
- Modify: `src/app/web/templates/discover.html`, `static/style.css`
- Modify: `src/app/web/routes/discover.py` (per-chip usage counts + tile tag data in context)
- Test: `tests/test_discover_page.py` (extend)

Port from demo lines 1927-2287. Restore:
- **Per-chip usage counts** on sidebar tag chips (demo `.chip .n`) — the route supplies counts.
- **Tile tag-row "minichips"** below each card (demo `.tagrow`/`.minichip`).
- **Tile ordering**: performer as an uppercase `.eyebrow`/`.artist` above the title, title below (demo tile).
- **Boxed/pill chrome on the sort + round-status facets** with counts (demo `.sorts button`, facet chips) — currently plain text links.
- The **dotted divider** above the status pill (demo `.status-line` `border-top`).
- The **search-bar chrome** (demo `.searchbar`/`.filter-search`).
- Keep the merged single status pill (do NOT split it). Keep the open/upcoming bucketing.

- [ ] Step 1: Failing tests (signed-in AND the existing signed-out render): sidebar chips show counts; tiles emit a `.tagrow`; the facet controls carry chip/pill classes and counts; the tile leads with the eyebrow performer. Keep the logged-out render test green.
- [ ] Steps 2-6: confirm fail, implement, verify, suite+lint, commit.

```bash
git commit -m "Restore Discover chip counts, tile minichips and faceted chip chrome"
```

---

### Task 6: Tags view gaps

**Files:**
- Modify: `src/app/web/templates/tags.html`, `_tag_picker_fields.html`, `static/style.css`
- Test: `tests/test_tags_page.py` (extend)

Port from demo lines 1737-1926. Fix:
- **`dialog.tagdlg` box-shadow** — add `box-shadow: var(--shadow)` and 3px/4px radius to match the app's other dialogs.
- **"Delete tag" right-alignment** — the `<form>` wrapper breaks `margin-left:auto`; move the auto-margin onto the flex item (the form) or restructure so the button right-aligns.
- **New-tag dialog footer** — remove the inline override that strips its border/padding; match the edit dialog's footer.
- **"Add member"** styled as the `+ Add member` pill (demo `.legchip`), not a bare button.
- **Retroactive-apply** styled as a button (demo `.btn.quiet`), not a bare underlined link.

- [ ] Step 1: Failing tests: `style.css` gives `dialog.tagdlg` a box-shadow; the tags page render places Delete at the footer's end; the add-member control carries the pill class. (Alignment is CSS — assert the rule shape.)
- [ ] Steps 2-6: confirm fail, implement, verify, suite+lint, commit.

```bash
git commit -m "Fix the tag dialog shadow, delete alignment and control chrome"
```

---

### Task 7: Setup view gaps

**Files:**
- Modify: `src/app/web/templates/setup.html`, `static/style.css`
- Test: `tests/test_setup_flow.py` (extend)

Port from demo lines 982-1151. Fix:
- The reveal screen's **next-deadline stat** must not cram a full dual-datetime into the big-font stat tile — render it in a smaller form or its own line (the other three tallies are short digits).
- Reconcile the **step tracker** with the demo (the prior-wizard done-pills + the escape link, vs the shipped Ready dot) — match the demo's treatment; keep the flow's three real screens.
- Apply the 3px radius / `.num` tokens from Task 1 (should already inherit; verify the stepdot/tile radius).

- [ ] Step 1: Failing tests: the reveal screen does not put the dual-datetime string inside the `.big` stat class; the stepdot markup matches the demo's tracker shape.
- [ ] Steps 2-6: confirm fail, implement, verify, suite+lint, commit.

```bash
git commit -m "Fix the setup reveal stat overflow and step tracker"
```

---

### Task 8: Preferences rebuild

**Files:**
- Rewrite: `src/app/web/templates/preferences.html`
- Modify: `src/app/web/routes/preferences.py` (Following/Delivery/Time context: per-tag counts, DM last-delivered, calendar created-at, detection mode), `static/style.css`
- Test: `tests/test_preferences_following.py` (extend), `tests/test_preferences_page.py` (render)

Port from demo lines 1547-1736. Rebuild on the demo's vocabulary (`.prail`, `.subrow`, `.swb`,
`.presetcard`, `.statline`, `.pill`/`.p-ok`, `.danger`, `.ruleline`):
- **Left rail** with an **active-section indicator** — the demo's small JS that toggles `.on` on the rail link for the section in view; port it (no framework).
- **Following:** per-tag Notify/Auto-apply toggle rows with counts; the summary pills (tags followed / upcoming / pruned). Keep the pruned-restore list and the invariant-8 override model — presentation change only, do not touch `set/clear_concert_subscription` semantics.
- **Reminders:** the demo's rule-row layout (keep the real preset/rule routes behind it).
- **Time:** the two-select layout (zone + detection mode) with the live JST/local preview. Reuse the existing timezone routes.
- **Delivery:** the DM and calendar **status pills + timestamps** (route supplies last-delivered / created-at); keep the one-time calendar-token reveal (invariant 5).
- **Account:** the bordered danger-card framing. The delete button itself is wired in Task 9.
- **Editors:** admin-gated as today.

- [ ] Step 1: Failing render/behaviour tests: the rail renders with the active-indicator hook; Following shows per-tag toggles + summary pills; Delivery shows the DM/calendar status pills; Time shows both selects; a non-admin sees no Editors section; the page keeps its logged-in GET render test.
- [ ] Steps 2-6: confirm fail, implement, verify, suite+lint, commit.

```bash
git commit -m "Rebuild Preferences on the demo's vocabulary with the active rail"
```

---

### Task 9: Delete-account route

**Files:**
- Modify: `src/app/web/routes/preferences.py`, `src/app/web/templates/preferences.html`, `static/style.css`
- Test: `tests/test_account_deletion.py` (new)

**Interfaces consumed:** `service.delete_user(session, discord_id) -> bool` (`db/service.py:110`, GDPR
erasure — anonymises authored content via `ondelete=SET NULL`), and the session-revoke path used by
logout.

- [ ] Step 1: Failing tests:
  - `POST /me/delete` for a logged-in user calls `delete_user`, returns a redirect/confirmation, and the session no longer resolves (revoked).
  - After deletion the user row is gone but a concert they authored survives with an anonymised author (assert via `delete_user`'s existing guarantees).
  - The route is `require_user` and scoped to the caller (no user-id from the form).
  - Preferences Account section renders a real delete button behind the heavy confirmation (assert the confirming markup + the loss-naming copy: reminders/subscriptions/preferences removed, authored catalogue kept anonymised).
- [ ] Step 2: Confirm fail.
- [ ] Step 3: Implement the route (calls `delete_user`, revokes the session, redirects to a signed-out page). Heavy confirmation is a client gate naming the specific loss, mirroring `privacy.html`'s wording and the invariant-8 opt-out confirmation weight — a deliberate second action, not a generic `confirm()`. No user-controlled text in inline `on*` handlers (invariant 7).
- [ ] Steps 4-6: verify, suite+lint, commit.

```bash
git commit -m "Add the self-serve account deletion route behind a heavy confirmation"
```

---

### Task 10: Docs

**Files:** modify `CLAUDE.md`, `WISHLIST.md`

- Update the CLAUDE.md UI-conventions section: the token layer + dark-mode theming contract (OS
  default, `data-theme` toggle, both directions), and note the two-line dual-time render shape.
- Update the test-count line to the real merged count.
- Move the shipped wishlist entry with the date; full re-rank pass. ASCII-only.

```bash
git commit -m "Document the reconciliation: theming contract and dual-time render"
```

---

## Verification

**Gates:** `uv run pytest -q` (baseline + new) and `uv run ruff check .` clean.

**Drive it** — `uv run python -m app.main`, blank `DISCORD_TOKEN`:
1. Toggle dark mode from the header; every page legible in both themes; refresh keeps the choice; a fresh browser follows OS preference.
2. Open a concert: follow button is a green pill, performer chips centred, reminders list is the row-based layout.
3. Preferences: rail highlights the active section; per-tag toggles and status pills render; Account offers a real delete behind a heavy confirmation.
4. Discover: chip counts, tile minichips, faceted chip chrome; the single status pill is intact.
5. Home: peek grid + foot-note present; Won-column card accented.
6. Compare each page side-by-side with `docs/superpowers/demo/dekimasen-demo.html`.

## Self-review notes

- Every spec scope item maps to a task: token pass → T1; time reshape → T2; Home/Concert/Discover/Tags/Setup/Preferences gaps → T3-T8; delete route → T9; docs → T10.
- The three "do not revert" items are constraints, not tasks — called out in Global Constraints and each relevant task.
- The setup upgrade-eligibility tile (spec "out of scope, investigate") is intentionally NOT a task here; if planning-time investigation shows it is real parity work, add it as T7a rather than smuggling it in.
