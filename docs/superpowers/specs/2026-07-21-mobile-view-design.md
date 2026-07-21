# Mobile view — design

Date: 2026-07-21
Status: approved for planning
Reference demos: `docs/superpowers/demo/dekimasen-mobile-demo.html` (the
per-decision frames — the reconciliation reference) and
`dekimasen-mobile-live.html` (the same design as an interactive phone
walkthrough). The frames govern where they and this document disagree with
memory or improvisation.

## Goal

Full mobile parity: every page usable and good on a phone. Today the header
shatters below ~700px (vertical wordmark, overflowing auth cluster,
horizontal page scroll), Discover buries content under stacked filters, and
most pages have no mobile treatment at all.

## Approach (decided)

**Responsive retrofit, desktop untouched (Option A).** All mobile behavior
lands as `max-width: 700px` media-query rules appended to the existing
`style.css`, plus the small set of template changes CSS cannot do. No
mobile-first rewrite, no separate mobile templates, no route changes, htmx
flows unchanged. Desktop pixels must not change — the existing render tests
and the demo-reconciled desktop design are the regression net.

One primary phone breakpoint: **700px**. The existing intermediate
collapses (1024/960/900/860) stay and get a consistency pass. The demo's
own broken `@media (max-width: 700px) {` (unclosed brace nesting the later
breakpoints, `dekimasen-demo.html` ~line 715) gets fixed as part of this
work.

## The pieces (all demonstrated in the frames)

1. **Compact header** (frame 1): one row — wordmark forced to one line
   (`white-space: nowrap`, slightly smaller), spacer, theme toggle, language
   chip. The auth cluster (avatar, name, badges, + Add, Preferences, Log
   out) leaves the header entirely on phones; its jobs move to the tab bar's
   Me and the FAB. The dm-blocked banner keeps rendering full-width below.

2. **Bottom tab bar** (all frames): fixed bottom, `--raise` background, top
   hairline, `env(safe-area-inset-bottom)` padding. Signed in: Home /
   Discover / Tags / Me (Me = avatar glyph → `/preferences`). Signed out:
   Home / Discover / Sign in. Active tab: `--accent` + 650 weight,
   `aria-current="page"` (same contract as the desktop nav). Inline SVG
   icons (house / magnifier / tag / avatar), stroke `currentColor`. The tab
   bar exists ONLY under the breakpoint; desktop header is untouched.
   `main` gains bottom padding so content never hides behind the bar.

3. **Editor FAB**: editors only, Home + Discover only — a `--ink` circle
   "+" above the tab bar linking to `/concerts/new`. This is the phone home
   of the header's "+ Add".

4. **Swipeable campaign board** (frame 1): the four columns become a
   center-snap carousel — `grid-auto-flow: column`, `grid-auto-columns:
   78%`, `scroll-snap-type: x mandatory`, columns `scroll-snap-align:
   center`, container `padding-inline: 11%` so the ends can center too;
   neighbours peek symmetrically. Pure CSS, no JS. Column headers keep the
   dot + label + count.

5. **Two-line deadline rows** (frame 2): the desktop grid header row is
   hidden; each row renders as a card — line 1: anchor pill + concert title
   with round/tag subline; line 2: the `dual_lines` block (bold date,
   dim JST · local). Capture buttons move below, full-width flex, min
   44px tall. Same markup, reshaped by CSS (the desktop row grid becomes
   the card layout under the breakpoint); the htmx swap targets are
   unchanged.

6. **Discover content-first** (frames 3-4): under the breakpoint the
   sidebar is not rendered in-flow. Order: page head + counts, search row,
   one filter line (active-filter chips + a "Filters (N)" button), then the
   tiles. The full filter set (sort, round status, franchise/group chips,
   regions, tag search) lives in a bottom sheet opened by the button. The
   sheet is a real `<dialog>` reusing the sidebar's existing form/anchor
   controls — server-side GET filtering still works with JS off via a
   no-JS fallback (the dialog rendered open-in-flow, or the `<details>`
   equivalent; implementation plan picks the mechanism, behavior is what
   this spec fixes: content first, filters reachable, no JS required to
   filter).

7. **Dialogs become bottom sheets** (frame 4): under the breakpoint every
   native `<dialog>` (tag picker, tags edit, confirmations) restyles to a
   full-width bottom sheet — rounded top corners, max-height ~78dvh,
   internal scroll. Markup and open/close mechanics unchanged.

8. **Concert page** (frame 5): already mostly stackable — lineage eyebrow,
   title, wrapped performer chips, full-width Following toggle, per-leg
   round group cards with inline capture buttons (44px). CSS-only.

9. **Editor and import** (frame 6): parity, not redesign — form grids go
   single-column, day/round cards stack, leg/qualifier chips wrap with
   ≥34px touch height, Save/danger buttons full-width. The import preview
   uses the same collapses.

10. **Me / Preferences** (frame 7): preferences page reshapes to stacked
    rows (label + value + chevron pattern per the frame) under the
    breakpoint; the account block gets the card treatment. Structure/
    routes unchanged — this is the Me tab's landing.

11. **Tags** (walkthrough only): search + kind sections with wrapped chip
    rows — the desktop page's natural collapse, plus touch-height chips.

12. **Remaining surfaces**: welcome wizard, setup flow, legal pages,
    landing — single-column collapses of their existing cards; the landing
    hero type scales down (`clamp` already present); horizontal-overflow
    audit on every page (nothing may x-scroll at 390px).

## Cross-cutting rules

- **No desktop pixel changes.** Every new rule lives inside
  `@media (max-width: 700px)` (or an existing intermediate query).
  Template changes (tab bar, FAB, filter sheet) render new elements that
  are `display: none` on desktop or replicate existing links — never
  restructure desktop DOM that CSS depends on.
- **Touch targets**: interactive controls ≥44px tall on phones (chips in
  dense informational rows may be 34px).
- **i18n**: any new user-visible string (e.g. "Filters") is gettext-wrapped
  and lands in BOTH catalogues (the hygiene test enforces).
- **Theming**: tokens only; both dark-mode paths get the new components for
  free, verified by eye.
- **Invariants**: dual-time rendering (1), capture-gate placement (UI
  conventions), injection boundaries (7) all unchanged — this is CSS +
  presentation-only template work.

## Testing

- Existing render tests stay green unmodified (markup additions are
  additive).
- New render tests: tab bar present with correct items signed in/out and
  `aria-current`; FAB editor-gated; filter sheet contains the filter
  controls; new strings translated (catalogue hygiene covers it).
- Layout verification is visual: 390 / 700 / 1024 checks per surface in
  the browser during implementation (CSS layout is not suite-testable).
- Desktop no-change: full suite + spot visual check.

## Out of scope

- PWA/installability, offline, push — separate ideas for the wishlist.
- Native apps.
- Any change to desktop layout or to the round/leg/capture semantics.
- Cache-busting (wishlist #4) — but note the deploy of this feature will
  need a Cloudflare purge, same as the i18n CSS.
