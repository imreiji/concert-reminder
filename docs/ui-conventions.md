# UI conventions

The full UI and design detail that used to live in CLAUDE.md. Moved here on
2026-08-07 for the reason given in `architecture.md`: CLAUDE.md pays for this
on every session, and it is needed only when you are changing the interface.

**Nothing here was rewritten or summarised** -- the text is the original,
verbatim. CLAUDE.md keeps the short list of hard rules (sentence case, the
3px radius, dual times, the two callout shapes, measure-don't-reason) and
points here for the rest.

The design source of truth remains the concept demos in
`docs/superpowers/demo/` -- see the inventory below.

## UI conventions

- Sentence case everywhere ("Add group", not "add group").
- The 🌐 language switcher is the concept demo's CYCLE CHIP (`base.html`'s
  `.langform`/`.langtoggle`): one bordered chip showing the current
  language; clicking posts the NEXT language in the EN → 中文 → 日本語 cycle
  as a plain `<form method="post" action="/language">`, so it works with JS
  disabled and renders on every page, signed in or out. A dropdown menu
  shipped first and was replaced at the owner's request -- don't bring it
  back. Language NAMES (EN/中文/日本語) are never translated -- a visitor
  picking their language needs to recognize it before they can read
  anything else.
- **Theming**: the full design-token layer (`--paper`, `--accent`, the
  `*-wash` set, `--raise`, `--chip`, `--shadow`, ...) lives in `style.css`'s
  `:root`. Dark mode is defined BOTH ways -- `@media (prefers-color-scheme:
  dark)` (the OS default when the user has never chosen) AND
  `:root[data-theme="dark"]`/`[data-theme="light"]` (the header toggle
  stamps `data-theme`, which wins on specificity and persists to
  `localStorage`). `base.html` stamps the saved theme in `<head>` before
  first paint -- do this in `<body>` or after CSS loads and every page
  flashes the wrong theme on load. Style new components against both
  directions, not just light.
- Deadline TIMES render as two lines via `fmt_dual_lines`/the `dual_lines`
  Jinja global -- a bold weekday+day+month line, then a "HH:MM JST, HH:MM
  local" time line -- never a flat one-line string on the web (that shape is
  `fmt_dual`, kept only for Discord embeds/plain text, which can't do two
  lines). Performance DATES (a concert's start day, not a deadline) use
  `fmt_day_month`/`day_month` instead: day-month only, no zone, no dual
  apparatus -- a date is a fact about the world, invariant 1 governs
  deadlines you must act by, not this.
- Tag chips are the universal element; "+ Add x" buttons share the exact
  chip silhouette. Pickers are native <dialog> white cards: header (title +
  ×), search, chip list; no footer; backdrop-click and Esc close --
  backdrop-close comes ONLY from base.html's global drag-safe handler; never
  add a local `e.target === dlg` click handler to a dialog (that shipped the
  drag-out-closes-the-dialog bug twice; a sweep test in
  `test_theme_and_tokens.py` now forbids it).
- Editor leg/round cards render through the shared partials
  `_editor_leg_card.html`/`_editor_round_card.html` -- concert_new,
  concert_edit and import_preview (their loops AND `<template>` blocks) all
  use them. Never hand-roll a card copy again; that six-site duplication is
  exactly what the coherence pass removed. Card anatomy: ja label on the
  top row, EN/中文 on the always-visible `.vary` row, fields below.
- Destructive card actions live in the kebab menu (`details.kebab`,
  top-right; menu items keep the `data-remove-leg`/`data-remove-round`
  hooks). It is the app's ONLY overflow menu and stays single-purpose:
  destructive actions only, never a place to bury regular controls. The
  inline × beside the Cancelled toggle was removed deliberately (owner
  call) -- do not reintroduce it. (2026-07-24: folding the concert header's
  Edit/Export into the kebab was proposed and rejected -- the rule stays.)
- Callouts come in two shapes (G2, 2026-07-24): `.edgecard` (raise ground,
  left edge in the tone colour -- ongoing state; `.dg`/`.ok`) and `.banner`
  (wash ground, full border -- needs attention; `.warn`/`.dgr`). Anatomy
  classes (`.standing`, `.next`, `.upgradebox`, `.feedbox`, `.danger-card`,
  `.danger-row`) compose the shape and keep only their layout. An anatomy
  class must be specific enough that it cannot match something else wearing
  the same tone word: `.danger-card` was `.danger` until 2026-08-11, which
  also matched every `<button class="btn danger">` and beat `.btn` on source
  order. Don't invent
  a third callout shape. Radiuses: 3px default, 999px chips, 4px overlay
  cards, 50% circles, bottom sheets `14px 14px 0 0` (documented at the top
  of `style.css`). Type ramp is 400/600/700 only. Motion budget: one 150ms
  card-lift hover plus the functional `#hxbar` progress bar -- nothing
  decorative (owner ruling, 2026-07-24).
- **State strips do not announce themselves** (owner ruling, 2026-08-12): a
  strip that reflects an on-page mode -- e.g. `/tags`'s `.viewtoggle`
  Follow/Edit strip (`#tagModeToggle`) -- carries no `aria-live`. Not an
  oversight to fix on sight: no other state strip in the app has one either,
  and giving this one an announcement a sighted user never sees the peer of
  would make the app inconsistent in a new way rather than fix an existing
  one. If a future strip genuinely needs an announcement, that is a decision
  to raise with the owner and apply app-wide, not a one-off patch.
- **A `<details>` inside a swappable region carries a `data-fold` key.**
  These regions (`#concert-rounds`, `#deadline-rows`) are swapped whole by
  `outerHTML`, so every fold in one is a NEW element rendered from scratch
  and comes back closed -- a reader who expanded a leg's round history and
  then toggled a different leg off watched every fold on the page snap shut.
  `base.html` collects the open keys inside the request target on
  `htmx:beforeRequest` and reopens the matching folds on `htmx:afterSettle`
  (per-request, keyed off the detail object's `xhr`, since two requests can
  overlap). It only ever OPENS. Keys are server-rendered ids/event_ids --
  `leg-{day_id}`, `block-{event_id}`, `more-concerts` -- never user text.
  `open_round_id` is the OTHER half, not a duplicate: it is server-rendered,
  so it survives with JS off, and it reopens the fold that OWNS a round a
  capture press just wrote. The client half generalises to swaps that write
  no round (a leg opt-out) with no per-caller plumbing. Keep both; reaching
  for `open_round_id` to solve a general fold reset is the trap.
- The sentence-style reminder builders (welcome, Preferences) render
  through locale-ordered slot patterns: ONE translatable pattern msgid per
  builder (e.g. ja 「{anchor}の{offset}{direction}に通知。」), split by
  `domain/sentence.py:split_slots` (raises on unknown slots) and rendered
  by the `sentence_slots` Jinja global -- text parts escaped, only the
  server-built selects pass as Markup (invariant 7). Translators own the
  word order; option labels (offsets, anchors) are their own msgids
  translated per-request in `routes/welcome.py`. A dropped placeholder is
  caught by `test_i18n_catalogues.py`'s placeholder-integrity tests.
  Welcome's JS adds rows by cloning the server-rendered
  `<template id="remrule-template">` -- never by assembling English DOM.
- Tile display rules: franchise+group → "F · G"; group only → G; artists
  only → artist chips; >1 venue → "📍 Multiple".
- The concert page's **Performing panel is per-group clusters**
  (`.pcluster`): one block per attached GROUP -- its chip on a `.pclabel`
  row, that group's attached performers in the `.chiprow` beneath -- then an
  unlabelled trailer block for performers in no attached group. Three owner
  rulings hold it: a performer in several attached groups appears under EACH
  (the repetition is information), clusters never fold (the panel is
  reference, not a to-do), and the header's DISTINCT performer count
  disappears entirely at zero rather than reading "0 performers". A
  member-less group keeps its label row and emits NO `.chiprow` -- an empty
  one still pays `.pclabel`'s bottom margin, and `:empty` can't reach it past
  the template's own whitespace.
- **Draw a relationship only when BOTH of its ends are attached to this
  concert.** ONE rule, applied twice (2026-08-01), and both halves are derived
  per concert, never stored:
  - a CHARACTER and her seiyuu render as one **split pill** (`.mchip`, two
    halves each its own link) when she is attached too; the seiyuu is then
    dropped from the standalone list because she is rendered inside the pill.
  - a SUBUNIT nests under its parent (`.pcluster.sub` on the concert page,
    `.grow2.sub` on the Tags page) when the parent is an attached GROUP.
    "Attached GROUP", not "attached tag": a group's parent is usually a
    FRANCHISE, and asking the looser question made every group on a franchise
    bill read as depth 1, emptied `roots`, and made the whole panel VANISH.
  Either end alone renders exactly as it did before -- a lone character is a
  plain chip, a lone subunit an ordinary top-level cluster. That is why the
  split shape was chosen over an inline `如月千早（今井麻美）` gloss (owner, from
  four mockups): the merge is CONDITIONAL, and the shape makes the difference
  read as meaningful rather than as inconsistent styling. A seiyuu attached in
  her OWN right is listed as herself in the unlabelled trailer -- she is not in
  `members_by_group` once a group's members are characters, so the existing
  code already does the right thing. The pill's box is DERIVED from
  `.performers .chip`'s own padding/line-height/border rather than tuned to
  match it (measured: 28.72px both, both themes; a chip SETS `line-height: 1.5`
  and does not inherit it, so a half that inherits comes out 2.5px short), and
  a parity test compares rule to rule so moving the chip desyncs loudly.
- **The Tags directory walks `parent_id` in Python, not in Jinja**
  (`tag_directory_context` returns a FLAT, pre-ordered list of
  `(group, members, depth)`). A template recursion over a children map would
  HANG on a `parent_id` cycle, and a cycle is reachable — rows predate
  `would_create_tag_cycle`. The `walked` set terminates the walk, and a
  leftover pass appends whatever it never reached, which is the property that
  actually matters: **every group renders exactly once, whatever its
  `parent_id` says**. Before that walk existed a GROUP under a GROUP fell out
  of the chips directory entirely and took its members with it (they already
  counted as grouped, so `ungrouped_performers` skipped them too), and a
  signed-in non-editor saw neither anywhere on /tags.
- **Membership is resolved SERVICE-side**, in `db/service.py`'s
  `performer_clusters` (one batched `tag_members` query, pinned by a
  statement-count test), and awaited in the route -- never in the template.
  `Tag.members` is a lazy self-referential m2m and a lazy load during async
  template rendering is a `MissingGreenlet` 500, the failure this project has
  already shipped once. Its caller precondition is that `concert.tags` is
  already eager-loaded, for the same reason.
- Times always render dual: JST + the user's timezone.
- VENUE tags filter by `region` (sidebar groups venues into regions like
  "Kanto"/"Kansai"/"Other"; toggling a region (de)selects every venue tag id
  in it) — filtering by one exact venue was explicitly ruled out as unhelpful.
  A leg's venue IS a VENUE tag now (see the `src/app/db/` note above) and the
  concert's venue tags -- which is what this filter reads -- are the rollup of
  its legs', so a concert whose legs carry no venue tag shows no venue
  anywhere and is invisible to this filter.
- **Home vs Discover** -- the old combined index is split in two by the
  question each page answers. `/` (`home.html`, the handler in `web/app.py`)
  is Home: "where do I stand", personal and login-gated, four blocks in order
  -- Up next, the campaign board, Coming up, a Discover teaser. Signed out it
  is a real landing page instead -- hero, a "how it works" section, a static
  illustrative campaign board (sample data, not the viewer's, since there is
  no viewer), a real Discover taste pulling live public cards, and the
  sign-in CTA. The first block is headed "Up next", NOT "Closes
  next": it picks the soonest row with a round behind it whatever anchor that
  row carries, so the header must stay moment-agnostic while the body names
  the moment. Narrowing the pick to `Anchor.CLOSES` to justify the old header
  was considered and rejected -- an opening round or a results announcement
  genuinely needs attention, and filtering would hide real urgency.
  **Coming up is per-concert BLOCKS, not a flat list**: `my_deadline_blocks`
  collapses each round to its soonest future anchor and groups what is left
  under one block per concert -- a header naming the concert once, the LEAD
  row, and the rest behind a fold -- so the row budget counts concerts, not
  anchors. Which row leads is `_wants_you`, the SAME predicate the concert
  page's "Next for you" uses (`_needs_you` is a thin adapter over it): keep
  them one rule, don't grow a second. Both folds -- "+N more rounds" in a
  block, "+N more events" past `VISIBLE_BLOCKS` -- are native `<details>`
  and are presentation ONLY: every folded row is in the DOM with its
  capture form intact, since rendering on expand would need a round trip
  and would make the fold a second silent limit beside
  `DEADLINE_ROWS_LIMIT`. Discover's "Coming up soon" list stays FLAT on
  purpose -- it calls `upcoming_deadlines` directly, and chronological is
  the right shape for a catalogue nobody has standing on. `/discover` (`routes/discover.py` +
  `discover.html`) is the catalogue: "what's on", and it is **public** --
  `current_user`, not `require_user`, the only content page in the app an
  anonymous visitor can reach. Header nav is Home / Discover / Tags and
  nothing else; the active item carries `aria-current="page"`, which is also
  what the CSS styles off.
- **Capture actions live on Coming up rows, never on board cards.** A
  deadline row is exactly ONE round on ONE leg, where "I have applied" has a
  single meaning. A board card is a whole campaign with a multi-rung ladder,
  where the same button is ambiguous -- applied to which round? A destructive
  control sitting inside something you are scanning to read is also a mode
  error. Do not "improve" the board by adding buttons to it.
  Two gates govern WHICH buttons a row offers, both resolved in
  `service.my_deadline_rows` (round timing is not presentation) and both
  load-bearing because `upcoming_deadlines` emits one row per future ANCHOR,
  so a single round can produce three or four rows. `can_capture` -- the
  round has opened -- because you cannot have applied to a round that has
  not opened, and recording APPLIED is irreversible (`record_round_outcome`
  refuses to overwrite a starting state). `can_report_result` -- outcome is
  APPLIED and the results time (or, failing that, the close) has passed --
  which is the web's ONLY exit from APPLIED: WON/LOST are also DM buttons,
  but a `dm_blocked` user or a `bot_enabled=False` deploy has no DM to
  press, and without them the board's four columns are reachable as two.
  PAID stays offered only from WON. Never "fix" this by relaxing
  `record_round_outcome`'s sequence rule -- the gates belong on the read
  side.
- **The board CAPS its ladder and never expands it.** `domain/board.py`'s
  `visible_rungs` keeps at most `VISIBLE_RUNGS` (2) rungs -- the one whose
  STANDING explains the card's column (ranked by `column_for`'s own
  precedence, never by position) plus the next actionable one -- and the
  remainder renders as a plain `.rmore` count line, deliberately NOT a
  `<details>`: uniform card height is what makes four columns scan as a
  board, and nothing on a card is interactive anyway (capture stays off
  cards, per the rule above). Surviving rungs keep their ORIGINAL ladder
  numbers; a rung's mark IS its place in the full ladder.
- **The concert page folds settled rounds per LEG**, one
  `<details class="moreround">` each (`service._split_leg_rounds` decides
  what stays up; the summary carries `+N more round(s)` plus one `.fchip`
  per state). Its rule is `_wants_you`, which now drives THREE surfaces --
  Home's block lead, the concert page's "Next for you" strip, and this fold
  -- so change it in one place and check all three. A secured leg keeps its
  receipt visible; the fold is presentation only, so a folded round's
  capture form stays in the DOM and works.
- Discover carries **one** status pill per card, merging the event's round
  state with the viewer's standing (`service.discover_statuses`). The
  standing REPLACES the countdown rather than sitting beside it, and the tone
  says who owes the next move: ok = you are covered, danger = you owe an
  action, quiet = you have no standing. Signed out there is no standing to
  merge, so the pill is the event state alone.
- Discover's three filters -- the tag/region chips, the free-text search box
  (matches title, title_en, every attached tag's name, and a free-text-venue
  fallback when no VENUE tag exists, all case-insensitive), and the
  round-status facet (Open now / Opening soon / Not tracking) -- combine as
  AND, not OR: all three narrow the same tile set together, plus the "Coming
  up soon" deadline list below the grids (each `<li>` carries the same
  `data-tags`/`data-search` attributes a tile does; it has no `data-status`,
  so the facet never hides a row). All three follow one contract: the initial
  state is computed server-side so there is no flash of wrongly shown tiles,
  every subsequent change is client-side off `data-tags`/`data-search`/
  `data-status` with no round trip, and each control stays a real `<a href>`
  or a real GET `<form>` so it degrades to slower server-side filtering with
  JS disabled. Add a fourth filter the same way.
- **Mobile is a retrofit, not a second design**: every phone rule lives in
  a banner-commented `@media (max-width: 700px)` section at the end of the
  file -- desktop pixels stay untouched by construction, since nothing
  outside those blocks may change. There are TWO such top-level blocks, not
  one: the main retrofit section, and a second one right after it holding
  the `.fsheet` bottom-sheet presentation alone (its own banner explains
  why it is separate -- it used to be a 760px query, and moved to 700 when
  `.layout`'s collapse went to 1040). New phone rules go in the FIRST
  block; the second stays single-purpose. Both are counted by
  `test_theme_and_tokens.py`'s top-level query pin.
  **The tablet band (701-1040px) follows the same discipline**: one
  banner-commented `@media (min-width: 701px) and (max-width: 1040px)`
  section (just before the phone section) holds every band rule -- the
  compact one-row header (wordmark secondary hidden, username hidden,
  icon-only Preferences/Sign out via base.html's `.nav-lbl`/`.nav-ico`
  spans), the swipeable 280px-column campaign board (the phone board's
  mechanics at tablet numbers), the `.peek` 2-col, the coming-up rows'
  data-happens fold, and the inline filter-sheet panel. The old scattered
  1024/960 breakpoints died into it; the 900 (`.rnd2`) and 860 (`.plyt`)
  queries deliberately remain standalone (they serve phones too);
  `test_theme_and_tokens.py` pins the exact top-level max-width query
  count so scattered-breakpoint drift fails CI.
  The `.fsheet`/`.layout` coupling now sits at 1040/1041: `.layout`
  collapses at 1040 and the `.fsheet` summary-flip is `min-width: 1041`,
  so the two-column desktop and the sidebar presentation flip on the
  identical boundary -- the invariant is "same boundary", not any magic
  number. TWO further boundaries govern the sheet: discover.html's
  collapse-on-load JS runs at <=1040 (must match the summary-visible
  range), while the bottom-sheet OVERLAY presentation is phone-only at
  <=700 -- in-band the sheet opens as an inline panel.
  Narrow phones (<=380px) get a NESTED `@media (max-width: 380px)` inside
  that same 700px block rather than a second top-level one, so the retrofit
  stays one section. It currently holds a single rule: drop the header's
  `dekimasen.app` wordmark, keeping the `できません` mark. That exists for the
  language chip -- it is the one header item whose width follows the
  language (EN -> 中文 -> 日本語), and since `.site-in` is `nowrap` a
  too-narrow bar squeezes the auth cluster instead of overflowing it, so a
  CJK label breaks between characters and the chip folds onto a second line,
  growing the whole header a row (measured: 59px -> 77px at 365px, -> 112px
  at 320px). `nav.auth { flex: 0 0 auto }` + `white-space: nowrap` on the
  chip pin it; hiding the wordmark is what buys them the room. Don't remove
  any of the three.
  Three phone-only patterns recur: a fixed bottom `.tabbar` (Home/Discover/
  Tags/Me or Sign in, `aria-current` on the active tab exactly like the
  desktop nav, keyed off the same `nav_page`) replacing the header nav; an
  editor-only `.fab` ("+", bottom-right) replacing the header's "+ Add";
  and dialogs (`<dialog>`, including `.picker`/`.prune`/`.tagdlg`) becoming
  bottom sheets (anchored to the bottom edge, rounded top corners, `max-
  height: 78dvh`) instead of the desktop's centered card. `docs/superpowers/
  demo/dekimasen-mobile-demo.html` (static frames, reference CSS values)
  and `dekimasen-mobile-live.html` (interactions) are the mobile design
  reference, alongside the existing `dekimasen-demo.html`/`dekimasen-
  onboarding-demo.html` for desktop. The 3px-radius guard
  (`test_style_uses_3px_radius_not_6or8`) still applies inside the mobile
  section -- phone cards and chips use the same `border-radius: 3px` as
  desktop; only the bottom-sheet corners deliberately deviate (`14px 14px
  0 0`, a sheet-specific shape, never 6px or 8px).
- **Measure a layout bug; do not reason about it.** Before diagnosing any
  layout, overflow or breakpoint problem, put the real app in a real viewport
  at a real width and read the numbers off it -- a seeded dev server in an
  iframe harness, the way `docs/superpowers/demo/_tablet_harness.html` was
  used to build the 701-1040px band, and the way the header measurements
  above (59px -> 77px -> 112px) were obtained. Reasoning from the CSS
  shipped a confidently wrong fix twice. The tell is a sentence of the shape
  "this must be overflowing because..." with no measurement in it.
