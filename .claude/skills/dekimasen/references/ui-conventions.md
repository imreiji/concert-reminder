# UI conventions

Read this before touching templates, style.css, or any user-facing copy or layout. When in doubt about UX, ask the owner — don't assume.


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
  Jinja global -- a bold weekday+day+month line, then a
  "HH:MM JST · HH:MM local" time line (a middle dot, not a comma; see
  `fmt_dual_lines` in `domain/timezones.py`) -- never a flat one-line
  string on the web (that shape is
  `fmt_dual`, kept only for Discord embeds/plain text, which can't do two
  lines). Performance DATES (a concert's start day, not a deadline) use
  `fmt_day_month`/`day_month` instead: day-month only, no zone, no dual
  apparatus -- a date is a fact about the world, invariant 1 governs
  deadlines you must act by, not this.
- Tag chips are the universal element; "+ Add x" buttons share the exact
  chip silhouette. Pickers are native <dialog> white cards: header (title +
  ×), search, chip list; no footer; backdrop-click and Esc close.
- Tile display rules: franchise+group → "F · G"; group only → G; artists
  only → artist chips; >1 venue → "📍 Multiple".
- Times always render dual: JST + the user's timezone.
- VENUE tags filter by `region` (sidebar groups venues into regions like
  "Kanto"/"Kansai"/"Other"; toggling a region (de)selects every venue tag id
  in it) — filtering by one exact venue was explicitly ruled out as unhelpful.
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
  genuinely needs attention, and filtering would hide real urgency. `/discover` (`routes/discover.py` +
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
  `style.css`'s single `@media (max-width: 700px)` section at the end of
  the file (banner comment marks its start) -- desktop pixels stay
  untouched by construction, since nothing outside that block may change.
  The one thing that sits just outside it, immediately under the banner
  comment, is `.tabbar, .fab { display: none }` -- the desktop-side default
  the block then overrides. That is the pattern for any future phone-only
  component: declare it hidden there, reveal it inside the media query;
  don't scatter the default back into the desktop rules above.
  The one documented exception is `.fsheet` (Discover's filter sheet),
  which switches at 760px instead because it must track `.layout`'s own
  collapse point exactly (also 760px) -- splitting the two breakpoints
  would open a 701-760px band where `.layout` has already stacked to one
  column but `.fsheet` still thinks it's in two-column desktop mode.
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

