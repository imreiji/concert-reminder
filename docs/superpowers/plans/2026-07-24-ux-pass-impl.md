# UX pass 2026-07 — implementation plan

**STATUS: SHIPPED 2026-07-24** — all five batches landed; 1234 tests green
(one pre-existing local failure, `test_test_dm_when_bot_disabled`, is a
discord.py-on-Windows issue unrelated to this pass) and `ruff check .`
clean. Deviations from the plan as written: the F3 toast skips
`not_applied` (its prune dialog is already the confirmation), and the E1
table renders after the chips directory in DOM order (document-order
semantics preserved).

Spec (change-by-change diffs): `docs/superpowers/specs/2026-07-24-ux-pass-diffs.md`
Design frames (source of truth for the visuals): `docs/superpowers/demo/dekimasen-ux-pass-demo.html`

Scope: 20 changes, A1–G7 minus C2 (rejected) and the A1 breathing dot
(rejected). No migrations, no route-order changes, no new breakpoints.

## Decisions locked in this plan (owner can veto anytime)

- **B1 = full client-rebuilt chips** (spec's fuller version): chips render
  server-side on load AND rebuild client-side when filters are added via the
  sidebar, using an embedded tag-id→name map (`| tojson` on a raw dict).
  Rationale: the removal path already needs most of the same JS; a row that
  only ever shrinks looks broken.
- **E2 bell follows with notify ON** (hidden `notify` input, `true`).
  Rationale: matches the Preferences follow default ("DM me on new events"
  starts checked); one action, one default. The Notify toggle in Preferences
  remains the off switch.
- **C1 signed-out viewers**: no change in behavior — the strip renders only
  when `next_row` exists, which requires standing, so anonymous visitors keep
  not seeing it.

## Batches

### Batch 1 — static polish (no backend, near-zero risk)
F2 (global `:focus-visible` + drop the `.picker-body input` `outline: none`),
F1 (`#hxbar` + listeners), D1 (numbered spine on concert_new), A1 (column-head
colors, `_board.html` + landing sample board), G3 (radius comment), G5
(`--ok` `#1a7f4e`→`#187a49` in `:root` AND `:root[data-theme="light"]`),
G7 (`.badge` 4px→3px).
i18n: D1 adds "Event", "Title and URL — required.".
Acceptance: `pytest -q` green (theme/token tests pin radius + breakpoint
count), `ruff` clean, visual check of `/`, `/concerts/new`.

### Batch 2 — small template/JS (no backend)
A2 (teaser+peek merge; `.teaser` removed from BOTH style.css spots incl. the
phone grouped rule), B2 (section counts in `applyVisibility`), D2 (covers
legend, ONE msgid, JS-built incl. initial fill), F3 (outcome toast via
HX-Trigger on BOTH `outcomes.py` HTMLResponse branches; 5 msgids),
G1 (weight sweep 550→600, 650/660/680→700, 54 spots), G4 (nothing extra beyond
E1 — guidance only).
i18n: D2 ×2, F3 ×5. A2 preserves all msgids byte-identical — verify no churn.
Acceptance: outcome capture on Home shows toast + card moves; create form
legend updates on chip toggle and on load; i18n catalogue test green.

### Batch 3 — medium (small context additions)
B1 (active-filter row: `selected_tag_objs` + `status_facet_label` in discover
context, chips server-rendered, client rebuild via `| tojson` name map,
`syncActiveRow`), E1 (chips⇄table toggle, 5 columns, editor-only, kind cell
`.dim`, `.tags-scope` wrapper for filterChips), E2 (Following column + bell
partial + `sub_by_tag` in tags context + hidden `next`/`notify` inputs).
i18n: B1 ×2, E1 ×8, E2 ×4.
Acceptance: filters removable from chip row with AND without JS (real links);
table toggle works; bell follow/unfollow round-trips to `/tags`; tags page
logged-in render test updated.

### Batch 4 — concert page (C1, C3)
C1 (`_standing_strip.html` partial with permanent `#concert-standing` wrapper;
cut from `_round_rows.html`; include in `.chead`; `outcomes.py` concert branch
re-renders it with `hx-swap-oob`; countdown over pill in `.countside`),
C3 (`via_tags` in `following_toggle_context`, derived from the same join
`tracked_concert_ids` performs; line in both `following` branches).
i18n: C3 ×2 ("via", "+{n} more").
Acceptance: strip in header updates live on outcome capture (assert
`hx-swap-oob` in POST response); strip absent with no standing; pill still
shown; render tests for concert page pass.

### Batch 5 — G2 callout consolidation (phased, own commits)
Add `.edgecard`(`.dg`/`.ok`) + `.banner`(`.warn`/`.dgr`), then migrate one
callout at a time: `.standing`, `.next`, `.upgradebox`, `.feedbox` → edgecard;
`.callout(+warn)`, `.banner-warn`, `.signin-note`, `.dupe`, `.danger-row`,
`.danger` → banner. One caller per commit, old class deleted when its last
caller moves.

## Per-batch ritual (non-negotiable)

1. `uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .` →
   `pybabel update -i messages.pot -d src/app/translations -l ja` and `-l zh`
   → fill new msgstrs by hand → delete `messages.pot`. (Only batches with new
   strings.)
2. `uv run pytest -q` green — including `test_i18n_catalogues`,
   `test_theme_and_tokens`, page render tests.
3. `uv run ruff check .` clean.
4. No `git commit` without asking the owner first.

## Guards this plan must not break

- Breakpoint guard: exactly 6 `@media (max-width: Npx) {` blocks — none added.
- Radius guard: no literal 6px/8px; new radii are 3px (or documented 4px/50%).
- `[hidden] { display: none !important }` stays first-class.
- Kebab stays destructive-only (C2 rejected).
- i18n: existing msgids byte-identical (A2); EN tests never assert translated
  strings; `{placeholders}` survive in both `.po` files.
- `_board.html`/`_round_rows.html` eager-load contracts: no new relationship
  touches in those partials (MissingGreenlet = 500).
- outcome POST keeps rendering BOTH Home partials + (new) the strip partial;
  no-JS path (plain redirect) unchanged.
- After the last batch: update the demos if shipped design moved deliberately,
  and move shipped entries to `WISHLIST.md`'s Shipped section with re-rank.
