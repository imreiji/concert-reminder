# Tablet band — implementation plan

Spec: `docs/superpowers/specs/2026-07-24-tablet-band-design.md` (approved;
demo merged in PR #94 as the reference). Branch: `tablet-band-impl`. One
implementation task + orchestrator-run harness verification + docs.

## The delicate part first: absorbing the five breakpoints

The band is currently touched by five max-width queries. Each must be
AUDITED for phone-range (<=700) side effects before it moves — a
`max-width: 860px` rule also governs phones, and absorbing it into a
701-1040 section would silently un-apply it below 701:

- `@media (max-width: 1024px)` — coming-up rows: hides the what-happens
  column. DIES; replaced by the in-band fold (below). Phones have their
  own row rules (<=600 block) — verify nothing between 601-700 relied on
  the 1024 rule alone.
- `@media (max-width: 960px)` — `.board` 2-col + `.peek` 2-col. `.board`
  in-band becomes the swipe board; `.peek` keeps a 2-col rule INSIDE the
  tablet section. Check 601-700 coverage (phone section has its own board
  treatment; confirm .peek too).
- `@media (max-width: 900px)` — `.rnd2`/`.standing` stacking (concert
  page). Measured fine in-band; the RULE STAYS where it is (it serves
  701-900 legitimately and phones below that — moving it buys nothing and
  risks the phone range). Not absorbed; noted in the section banner.
- `@media (max-width: 860px)` — `.plyt`/`.prail` (preferences). Same
  verdict as 900: stays, noted.
- `@media (max-width: 760px)` — `.layout` collapse. MOVES to 1040
  together with `.fsheet`'s `min-width: 761px` flipping to 1041 — the
  coupling invariant is "identical boundary", now 1040/1041. The SECOND
  760 block (mobile fsheet bottom-sheet presentation, style.css ~1631)
  does NOT move: in-band (701-1040) the fsheet renders as an INLINE
  disclosure panel (the demo's look — plain bordered panel under the
  summary chip), not the phone bottom sheet. New in-band presentation
  rules live in the tablet section.

## The tablet section

One banner-commented block, placed between the desktop rules and the
mobile section: `@media (min-width: 701px) and (max-width: 1040px)`.
Contents:

1. **Header (T2)**: `.mark { white-space: nowrap }` (safe to set
   unconditionally if verified visually identical on desktop; otherwise
   in-band); hide `.mark span` (the "dekimasen.app" secondary) in-band;
   auth cluster compaction — hide the username text (keep avatar),
   Preferences and Log out render icon-only. base.html MAY add minimal
   markup for this (e.g. `<span class="nav-lbl">` around the two labels
   plus an icon span with `aria-hidden`), with the icons display:none'd
   by DEFAULT and enabled only inside the tablet section — desktop output
   stays pixel-identical, and every control keeps an accessible name
   (aria-label or title on the anchor, not the hidden span).
2. **Board (T3)**: in-band `.board` becomes the swipe rail —
   `overflow-x: auto`, `grid-auto-flow: column`,
   `grid-auto-columns: 280px`, `scroll-snap-type: x proximity`, columns
   `scroll-snap-align: start`, thin scrollbar, and enough right padding
   that the fourth column's edge peeks. MIRROR the phone board's
   mechanics (read the mobile section first; reuse its class approach —
   if the phone board already does this, the tablet rules should look
   like that block, adapted for 280px columns).
3. **Discover (T4)**: `.layout` one column; `.fsheet > summary` visible
   as the inline chip; the sheet body as an inline bordered panel
   (demo's `.sheetbody` look adapted to the real sidebar markup — the
   real DOM is discover.html's sidebar inside the fsheet details; style
   the EXISTING markup, do not restructure the template).
4. **Coming-up rows (T5)**: in-band, drop the what-happens column from
   the grid and fold its text via the data-happens ::after pattern the
   phone already uses (copy the phone block's approach; confirm
   home.html rows already carry data-happens — they do on phone).
5. `.peek` 2-col (from the old 960 rule).

## Verification

- Implementer: full `pytest -q` foreground (`uv run --isolated`), ruff,
  plus a new guard test pinning the tablet section banner exists and
  that no NEW top-level max-width queries appeared (count them).
- Orchestrator (post-commit): re-measure with the live harness at
  760/820/1000 against the seeded dev server (header no-wrap under ja,
  board swipes, Discover 2/3-across, rows keep context) AND at 1100/1300
  to prove desktop is untouched.

## Docs (orchestrator, same branch)

CLAUDE.md mobile-section paragraph gains the tablet-section sibling
rules (one bounded section, the 1040/1041 coupling note replacing
760/761); WISHLIST ship move + revision; README line.
