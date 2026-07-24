# Tablet band (701-1040px) — design spec

Date: 2026-07-24. Status: owner picked all three forks; demo in progress;
implementation after demo approval. WISHLIST #1 (tablet band).

## Problem, as measured

Measured against the real app (locally seeded DB, iframe harness at
760/820/1000px — not reasoned from CSS). The band is currently governed by
five unrelated ad-hoc breakpoints (1024/960/900/860/760), each patching one
component; nothing composed the band as a whole. Findings, worst first:

1. **Header, every page**: the できません wordmark line-breaks
   character-by-character (fully vertical at 820, splits even at 1000);
   the auth cluster wraps two-deep ("tablet-tester" and "Log out" both
   break mid-word; "+ Add" goes tall).
2. **Home board**: 4 columns until 960px — at 960-1040 each column is
   ~220px and every card element wraps (eyebrow, title, venue, pill row).
   The 2x2 below 960 reads fine.
3. **Discover**: the fixed 15rem sidebar persists down to 760px; at 820
   the content column is ~540px, tiles collapse to a gappy single column.
4. **Coming up rows**: <=1024 the "what happens" column is display:none'd,
   so the capture buttons lose their context.
5. Concert page and the editor survive the band well (flex cards wrap
   gracefully) — OUT OF SCOPE beyond inheriting the header fix.

## Decisions (owner, 2026-07-24 brainstorm)

**T1 — One bounded tablet section.** All band rules live in a single
`@media (min-width: 701px) and (max-width: 1040px)` section in style.css,
banner-commented like the mobile section — the same discipline that keeps
desktop pixels untouched by construction. The five scattered breakpoints
are absorbed: rules that exist only to patch this band move INTO the
section; rules that legitimately serve desktop (>1040) stay where they
are. The phone section (<=700) is untouched.

**T2 — Compact top header.** Desktop header shape, made to fit:
- `.mark` gets `white-space: nowrap`; the `dekimasen.app` secondary
  wordmark is hidden in-band (same move the phone makes <=380, for the
  same reason); できません never wraps.
- Auth cluster tightens: username text hidden (avatar remains the "who"),
  Preferences and Log out become icon-sized buttons with aria-labels and
  title tooltips; the language chip and theme toggle stay (the language
  chip must keep its nowrap/no-shrink guarantees from the phone work).
- Nav (Home/Discover/Tags) stays put. Nothing wraps at 701px in ja/zh
  (the widest language state) — that is the acceptance test.

**T3 — Swipeable 4-column board (the phone pattern).** The board keeps
all four columns at full card width and scrolls horizontally in-band,
preserving the left-to-right campaign metaphor (owner's call over 2x2:
the board IS the apply->win->pay progression, and folding it breaks the
story). Reuse the phone board's swipe mechanics/classes wherever they
can be shared rather than duplicated; scroll-snap per column; the column
headers stay visible with their counts. A subtle affordance (edge peek of
the next column) signals scrollability.

**T4 — Discover filter sheet <=1040.** The phone's `.fsheet` (collapsible
Filters disclosure above the grid) takes over for the whole band; the
`.layout` two-column grid collapses at the same 1040 boundary. The
documented 760/761 `.fsheet`/`.layout` coupling MOVES AS ONE UNIT to
1040/1041 — the invariant is "identical boundary", not "760". Tiles then
auto-fill the full width (2-3 across in-band). The summary chip shows the
active-filter count exactly as on phone.

**T5 — Coming up rows keep their context.** In-band, reuse the phone's
data-happens pattern (the "what happens" text folds into the title
cell's small line) instead of hiding the column outright. The >=1041
desktop grid is untouched.

## Demo

`docs/superpowers/demo/dekimasen-tablet-demo.html` — static frames at
reference widths (820 and 1000), same self-contained token-driven style
as the mobile demos: compact header, swipeable board (with edge peek),
Discover under the filter sheet, a coming-up row with folded context.
It joins `dekimasen-mobile-demo.html` as the band's design reference.

## Acceptance (implementation phase)

1. One tablet `@media` section; no new scattered breakpoints; phone
   section and >1040 desktop byte-untouched except rules the section
   absorbs.
2. Header never wraps at 701px under ja/zh locales (render test on
   header markup + the measured harness pass).
3. Board scrolls horizontally in-band with snap; all four columns
   reachable; no vertical layout change to cards.
4. `.fsheet` and `.layout` share the 1040/1041 boundary (update the
   cross-referencing comments in style.css and CLAUDE.md).
5. Coming-up rows show what-happens context at every width.
6. Harness re-measurement at 760/820/1000 shows no char-wrapped
   wordmark, no single-column Discover tiles, no wrapped auth cluster.
