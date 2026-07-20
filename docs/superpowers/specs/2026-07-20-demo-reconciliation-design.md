# Demo reconciliation — make the shipped UI match the frozen concept

Date: 2026-07-20

The six-branch UI/UX refactor (PRs #45-#50) ported the concept demo's structure into the live app.
A seven-view reconciliation against the frozen demo (`dekimasen-demo.html`, Jul 19) found the bones
faithful — right sections, right order — but a consistent layer of drift underneath. This branch
closes that drift so the shipped UI matches the demo.

## Problem

The reconciliation traced most findings to one root cause: **the demo's design-token layer never
fully landed in `style.css`.** The shipped `:root` defines only 8 of the demo's ~19 tokens — every
`*-wash`, `--raise`, `--chip`, and `--shadow` token is absent, so components silently fall back
(chips render white instead of the soft `--chip` wash) — and there is **no dark mode at all**
(0 `prefers-color-scheme` / `data-theme` rules), though dark mode was an explicit requirement.

On top of the token gap, individual views each lost specific pieces during their port: Preferences
was rebuilt from generic classes and lost its entire bespoke vocabulary; the concert page's
`.follow` toggle has zero CSS; the performer-chip centering the owner explicitly asked for never
made it into the stylesheet; Home lost its peek grid and foot-note; Discover lost chip counts and
tile minichips; Tags lost a dialog shadow and a button alignment.

## Approach

Three layers, applied in order so the cheap global fix lands before the per-view work builds on it:

1. **Global token pass** (`style.css`) — port the demo's complete token set including both themes,
   fix the radius/chip/eyebrow/`.num`/header vocabulary once. This corrects the majority of the
   drift across all seven pages simultaneously.
2. **Per-view component gaps** — restore the specific missing markup/CSS on each page.
3. **The delete-account route** — the one place the demo promised a control the backend could not
   yet honor. The erasure logic already exists (`service.delete_user`); this wires a route + heavy
   confirmation + session revoke to it.

Throughout, three demo elements are deliberately NOT reverted — they were placeholders the shipped
app improved on. We honor the demo's *visuals* while keeping the correct *behavior* (see below).

## Scope

### 1. Global token pass (`style.css`)

Port from the demo's `<head>` verbatim:

- **Full token set**, light values on `:root` and `:root[data-theme="light"]`, dark values under
  both `@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]` — the exact pattern and
  hex values from the demo (`--paper #f7f6f2`/`#17161a`, `--accent #4f46b8`/`#9a92f0`, the five
  `*-wash` tokens, `--raise`, `--chip`, `--shadow`, etc.).
- A theme toggle in the header that stamps `data-theme` on `:root` (the demo ships the palette but
  the live app needs the actual control + persistence; localStorage, default to OS preference).
- **Corner radius 3px** everywhere buttons/inputs/cards/dialogs currently use 6px/8px.
- **`.chip`**: `background: var(--chip)`, transparent border, `.on` → `var(--accent)` fill; add the
  hover `transition`.
- **`.eyebrow`** applied to the artist/franchise micro-labels on board cards, Discover tiles, and
  the editor's "Applies to"/"Qualifies" labels (`.lbl3`).
- **`.num { font-variant-numeric: tabular-nums }`** defined and applied to countdowns/tallies.
- **Header**: sticky, `.site-in` max-width cap matching the content column, nav items as pills with
  a `var(--chip)` background on hover and on the active (`aria-current`) item.
- Font stack regains `-apple-system`.

Both themes get equal design care — dark is not a naive invert; use the demo's dark hexes, which are
already tuned for contrast.

### 2. Per-view component gaps

Enumerated exhaustively in the implementation plan; the load-bearing ones:

- **Concert page:** add the `.follow` toggle CSS (green "covered" pill + checkmark) and its "You will
  be reminded about every round below." caption; add `.performers .chip { justify-content: center;
  text-align: center }` (the owner's explicit ask); redesign "My reminders" (`_rules.html`) into the
  demo's row-based layout with the preset note and an "Add a reminder" affordance instead of the old
  always-open inline form; remove the stray legacy `meta-grid`/`performers_text` block; dim `.nolink`
  performer chips.
- **Preferences:** rebuild on the demo's vocabulary — per-tag Notify/Auto-apply toggle rows, the
  Following summary pills + counts, Delivery status pills + timestamps, the two-select timezone
  (zone + detection mode) with the live JST/local preview, the Account danger-card framing, and the
  active-rail indicator (needs the small JS the demo has and shipped omits entirely).
- **Home:** restore the peek grid (4 sample Discover cards) and the foot-note; add the teaser's
  "N with a round still open" clause; give the "Won — pay" board card its `border-left` accent and
  make the countdown pill tone reflect urgency, not just column; two-tier "Up next" countdown.
- **Discover:** per-chip usage counts, tile tag-row minichips, performer-as-eyebrow-above-title tile
  ordering, boxed/pill chrome on the sort + round-status facets, the dotted divider above the status
  pill, the search-bar chrome.
- **Tags:** restore `dialog.tagdlg` box-shadow; fix the "Delete tag" right-alignment (the `<form>`
  wrapper breaks `margin-left:auto`); restore the new-tag dialog footer border/padding; style
  "Add member" as the `+ Add member` pill and retroactive-apply as a button.
- **Setup:** stop cramming the dual-datetime into the small stat tile on the reveal screen;
  reconcile the step tracker.

### 3. Delete-account route (new backend surface)

The demo's Account section shows a "Delete account" button; shipped shows a manual-request
placeholder. Wire it up:

- `POST /me/delete` (`routes/preferences.py`): `require_user`, calls the existing
  `service.delete_user(session, discord_id)`, revokes the session, redirects to a signed-out
  confirmation.
- **Heavy confirmation** matching the weight of the invariant-8 opt-out confirmation — names exactly
  what is removed vs kept ("Your reminders, subscriptions, and preferences are deleted. Concerts and
  tags you authored stay, with your name removed."), and requires a deliberate second action, not a
  generic prompt. This mirrors the erasure wording already on `privacy.html`.
- Present the Account items in the demo's bordered danger-card framing.

### Time format reshape (cross-cutting, keep the behavior)

Shipped emits one flat ISO string (`Sat 2026-08-01 19:00 JST (07:00 ADT)`). Reshape the render into
the demo's two-line form — bold weekday+day+month, then `19:00 JST · 07:00 your-time` below — WITHOUT
reverting to the demo's fake terse placeholders ("closes in 6h"). This honors invariant 1 (dual JST
+ local, always) and the demo's visual at once. Touches `domain/timezones.py`'s `fmt_dual` (or a new
sibling formatter) and its call sites on Home, board, deadline rows, concert page, and setup.

## Deliberately NOT reverted

"Match the demo" has three traps where the demo was a placeholder and shipped is correct. Keep the
shipped behavior; honor only the demo's visual treatment:

1. **Dual JST + local times** replace the demo's terse "closes in 6h" — invariant 1. Reshape
   visually (above), do not revert.
2. **The merged single status pill** on Discover replaced a two-part "[state] YOU [standing]" the
   owner rejected. Keep the single pill.
3. **The "Up next" header** on Home replaced "Closes next" — a documented decision; the body names
   the actual moment. Keep.

Also kept, as post-demo shipped features with no demo counterpart: the open/upcoming Discover
bucketing, the `.ics` calendar-row icons, the concert edit-history fold, the editor's
applies-to-before-times ordering and cancelled-as-chip control.

## Out of scope

- Any change to the six shipped features' *behavior* — this is a presentation-and-parity branch. The
  only new behavior is the delete-account route (explicitly requested) and the theme toggle.
- New features not in the demo.
- The setup upgrade-eligibility tile: investigate during planning whether upgrade rounds (shipped in
  #49) should now surface there; if it is real feature work rather than parity, split it out.

## Constraints (invariants — unchanged)

- Invariant 1: times stay dual, JST first, aware-UTC in the DB. The reshape is presentation only.
- Invariant 7: editor URLs through `form_url`; picker data via `| tojson` never `| safe`; no
  user-controlled text in inline `on*` handlers; `data-name` collides with `filterChips()`.
- `routes/imports.py` stays registered before `routes/concerts.py`.
- `src/app/domain/` stays pure — the time formatter is pure string logic, no I/O.
- Config/CSS files ASCII-only where the owner's GBK locale reads them (CSS is served, not locale-read,
  but keep comments ASCII to be safe).
- No schema change. `service.delete_user` already exists; the delete route adds no columns.
- Sentence case; every page keeps its logged-in GET render test; `/discover` keeps its logged-out one.

## Testing

- **Theme:** a render test asserting the dark palette tokens exist and the toggle attribute path
  works; both themes legible (contrast) — at minimum assert the `data-theme` override rules are
  present for both directions.
- **Token parity:** assert `style.css` defines the previously-missing tokens (`--chip`, `--raise`,
  the `*-wash` set, `--shadow`) so the fallback-to-white regression can't silently return.
- **Delete route:** `POST /me/delete` erases the user (reuse `delete_user`'s existing tests as the
  backend guarantee), revokes the session, and authored content survives anonymized; the route is
  `require_user` and scoped to the caller.
- **Per-view render tests:** each redesigned page keeps/gains a logged-in GET render test; the
  concert page asserts the `.follow` and centered-performer CSS classes are emitted; Preferences
  asserts the rail/toggle/status-pill markup renders.
- **Time reshape:** the formatter emits the two-line shape and still contains both JST and the user
  zone (invariant 1 pinned).

## Verification

Drive it (`uv run python -m app.main`, blank `DISCORD_TOKEN`): toggle dark mode and confirm every
page is legible in both themes; open a concert and confirm the follow button is a green pill with
centered performer chips and the redesigned reminders list; open Preferences and confirm the rail
highlights the active section, the per-tag toggles and status pills render, and the Account section
offers a real delete button behind a heavy confirmation; open Discover and confirm chip counts, tile
minichips, and faceted chip chrome; compare each page side-by-side with the demo view.
