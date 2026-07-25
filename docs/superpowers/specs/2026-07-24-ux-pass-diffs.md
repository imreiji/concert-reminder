# UX pass 2026-07 — change list with diffs

Companion to the concept demo `docs/superpowers/demo/dekimasen-ux-pass-demo.html`
(every change below has a frame there, same ids). 14 changes, each independently
shippable. No migrations, no route changes, no new breakpoints (the
`test_theme_and_tokens` breakpoint count is untouched), every new radius is 3px
(`test_style_uses_3px_radius_not_6or8` safe). New user-facing strings are called
out per change — they need the pybabel extract/update dance and both `.po`
files filled, or `test_i18n_catalogues.py` fails.

| id | page | change | effort | needs backend |
|----|------|--------|--------|---------------|
| A1 | Home | board column identity + won urgency | S | no |
| A2 | Home | merge discovery teaser + peek grid | S | no |
| B1 | Discover | active-filter chip row | M | tiny (pass tag objects) |
| B2 | Discover | live section counts | S | no |
| C1 | concert | move "Next for you" strip up + countdown | S | no (render-site move) |
| ~~C2~~ | concert | **rejected** (owner, 2026-07-24) — kebab stays destructive-only | — | — |
| C3 | concert | "via <tags>" tracking explanation | M | yes (tag derivation) |
| D1 | editor | numbered create-form spine | XS | no |
| D2 | editor | live "this round covers…" legend | S | no |
| E1 | tags | chips ⇄ table view toggle | M | no |
| E2 | tags | follow/unfollow bell per row | M | yes (sub map in context) |
| F1 | global | htmx progress bar | XS | no |
| F2 | global | `:focus-visible` ring | XS | no |
| F3 | global | outcome-capture toast | S | tiny (HX-Trigger header) |
| G1 | style | font-weight ramp → 400/600/700 | S | no |
| G2 | style | callout grammar: two shapes | L | no (phased refactor) |
| G3 | style | radius-system comment block | XS | no |
| G4 | style | eyebrow discipline (labels only) | XS | no |
| G5 | tokens | light `--ok` darken (4.38→4.68) | XS | no |
| G6 | — | motion budget — **decided: no dot** | — | — |
| G7 | style | `.badge` 4px→3px | XS | no |

---

## A1 — board columns get identity; "Won — pay" gets urgency

Why: the board is the app's core, but all four columns render identically.
Money at risk should be findable with peripheral vision.

`src/app/web/templates/_board.html`:

```diff
   {% for key, heading, dot in COLUMNS %}
   {% set cards = columns.get(key, []) %}
-  <div class="col">
+  <div class="col {{ key }}">
     <div class="col-head">
       <span class="dot {{ dot }}"></span> {{ heading }}
```

`src/app/web/static/style.css` (append after the existing `.d-open`/`.d-won` dot rules):

**Already shipped, do not re-add:** `.board .card[data-column="won"]` already
gets `border-left: 3px solid var(--accent)` (style.css:731, "Money you could
still lose…"), and `_board.html` cards already emit `data-column`. The
accent-edge half of the original proposal is redundant. What remains:

```diff
+/* Column identity: the head underline takes the column colour, so a column
+   reads as a lane. (A breathing dot on the won column was proposed and
+   rejected -- owner, 2026-07-24: the colours carry "money at risk" alone,
+   and the cards' accent edge already ships, keyed off data-column.) */
+.col.open .col-head { border-bottom-color: var(--danger); }
+.col.applied .col-head { border-bottom-color: var(--off); }
+.col.won .col-head { border-bottom-color: var(--accent); }
+.col.secured .col-head { border-bottom-color: var(--ok); }
```

`src/app/web/templates/home.html` — the signed-out sample board should match
(it uses the same `.col` markup):

```diff
-    <div class="col">
+    <div class="col open">
       <div class="col-head"><span class="dot d-open"></span> {{ _("Open now") }} <span class="count num">2</span></div>
```
(same for `applied`, `won`, `secured` on the other three)

Notes: no animation, no new media queries — the dot proposal was the only part
that needed either. No new strings.

## A2 — Home: one discovery section, not two

Why: the teaser panel and the peek grid are two blocks selling the same thing.
The teaser copy becomes the grid's header. All existing msgids are preserved
byte-identical — **no catalogue changes**.

`src/app/web/templates/home.html`:

```diff
-{# 4. Discovery teaser -- discovery still exists, it just stops being the
-   front door. #}
-<div class="teaser">
-  <div>
-    <div class="eyebrow">{{ _("Not tracking these yet") }}</div>
-    <h2>{{ _("Discover") }}</h2>
-    <p>
-      {% trans count=catalogue_count %}{{ count }} event in the catalogue,{% pluralize %}{{ count }} events in the catalogue,{% endtrans %}
-      {#- The count is what /discover would LIST, not every Concert row:
-          a concert whose every leg is cancelled is hidden there. -#}
-      {% trans n=open_round_count %}{{ n }} with a round still open.{% endtrans %}
-      {{ _("Browse by franchise, group, artist or region — or search everything.") }}
-    </p>
-  </div>
-  <a class="btn" href="/discover">{{ _("Open discover →") }}</a>
-</div>
-
-{# The peek grid: a taste of what /discover holds, ... #}
-{% if peek_concerts %}
-<div class="peek" id="peek">
-  {% for c in peek_concerts %}{{ peek_card(c, peek_statuses.get(c.id)) }}{% endfor %}
-</div>
-{% endif %}
+{# 4. Discovery -- the teaser's copy is the peek grid's header; one section.
+   Deliberately excludes tracked concerts so this stays a door OUT. #}
+{% if peek_concerts %}
+<section class="discover-block">
+  <div class="head">
+    <h2>{{ _("Not tracking these yet") }}</h2>
+    {#- The count is what /discover would LIST, not every Concert row. -#}
+    <a class="more" href="/discover">{{ _("Open discover →") }}</a>
+  </div>
+  <p class="sub">
+    {% trans count=catalogue_count %}{{ count }} event in the catalogue,{% pluralize %}{{ count }} events in the catalogue,{% endtrans %}
+    {% trans n=open_round_count %}{{ n }} with a round still open.{% endtrans %}
+    {{ _("Browse by franchise, group, artist or region — or search everything.") }}
+  </p>
+  <div class="peek" id="peek">
+    {% for c in peek_concerts %}{{ peek_card(c, peek_statuses.get(c.id)) }}{% endfor %}
+  </div>
+</section>
+{% endif %}
```

`style.css`: add the `.discover-block` rules from the demo; delete the now-dead
`.teaser` rules — that's TWO spots, not one: the component block (style.css
`.teaser`, ~line 822) AND the grouped phone rule `.next, .teaser, .peek
{ grid-template-columns: 1fr; }` in the mobile section (style.css:1612), which
becomes `.next, .peek`. (The tablet/phone sections own `.next`/`.peek` too —
leave those.) Confirm with `grep -n "\.teaser"` that zero remain.

## B1 — Discover: active-filter chip row

Why: on phone the filter sheet starts closed, so active filters are invisible.
Each active selection renders as a removable chip between the search bar and
the tiles — real links with JS off, client-side with JS on (the same contract
the sidebar chips already keep).

`src/app/web/templates/discover.html`, after the search form:

```diff
     </form>
 
+    {#- Active filters, visible without opening the sheet. Rendered from the
+        same selections the sidebar reflects; with JS off each chip is a real
+        link to the URL without that selection. -#}
+    {% if selected_tags or status %}
+    <div class="active-filters" id="active-filters">
+      <span class="af-label">{{ _("Filters") }}</span>
+      {% for t in selected_tag_objs %}
+      {% set rest = selected_tags | reject("equalto", t.id) | list %}
+      <a class="chip on" data-af-tag="{{ t.id }}"
+         href="/discover?{{ filter_query(sort, rest, status) }}"
+         onclick="return removeTagFilter(event, this)">{{ loc(t, "name") }}<span class="x">×</span></a>
+      {% endfor %}
+      {% if status %}
+      <a class="chip on" data-af-status
+         href="/discover?{{ filter_query(sort, selected_tags, "") }}"
+         onclick="return clearStatusFilter(event)">{{ status_facet_label(status) }}<span class="x">×</span></a>
+      {% endif %}
+      <a class="af-clear" href="/discover?sort={{ sort }}"
+         onclick="return clearAllFilters(event)">{{ _("Clear all") }}</a>
+    </div>
+    {% endif %}
```

In the page's existing IIFE:

```diff
+    window.removeTagFilter = function (event, el) {
+      event.preventDefault();
+      const selected = selectedIds();
+      selected.delete(Number(el.dataset.afTag));
+      el.remove();
+      applyAll(selected, currentQuery, currentStatus());
+      syncActiveRow();
+      return false;
+    };
+    window.clearStatusFilter = function (event) {
+      event.preventDefault();
+      document.querySelector("[data-af-status]")?.remove();
+      applyAll(selectedIds(), currentQuery, "");
+      syncActiveRow();
+      return false;
+    };
+    window.clearAllFilters = function (event) {
+      event.preventDefault();
+      applyAll(new Set(), "", "");
+      document.getElementById("active-filters")?.remove();
+      return false;
+    };
+    // Hide the whole row once its last chip is gone (the CSS has
+    // `.active-filters.allclear { display: none; }`).
+    function syncActiveRow() {
+      const row = document.getElementById("active-filters");
+      if (row) row.classList.toggle("allclear", !row.querySelector(".chip"));
+    }
```

Notes:
- The row's chips reuse the shipped `.chip`/`.chip.on` vocabulary (999px pill,
  solid accent when on) plus the shipped `.chip.on .x` white — the diff's
  markup already carries those classes; no new chip CSS beyond the row layout
  (`.active-filters`, `.af-label`, `.af-clear`). The demo renders them this way.
- Backend: `routes/discover.py` must pass `selected_tag_objs` (Tag rows for the
  selected ids — the sidebar query already loads tags by kind, so this is a
  lookup, not a new query) and a `status_facet_label(key)` helper (map over the
  existing `status_facets`).
- The row renders server-side on page load. `syncActiveRow()` hides the row
  when the last chip is gone. Chips ADDED via the sidebar mid-session don't
  appear in the row until reload unless we also rebuild the row client-side —
  that needs a tag-id→name map (`{{ active_filter_names | tojson }}`, raw dict,
  never pre-serialised). Rebuild-in-JS is included in the demo's JS; decide at
  implementation whether to take the simple (remove-only) or full (rebuild)
  version.
- New strings: "Filters", "Clear all".

## B2 — Discover: live section counts

```diff
-    <h2>{{ _("Open &amp; upcoming") }}</h2>
-    <div class="tiles">
+    <h2>{{ _("Open &amp; upcoming") }} <span class="n" data-sec-count="open">{{ open_concerts | length }}</span></h2>
+    <div class="tiles" data-section="open">
```
(same for the `Upcoming` section, key `upcoming`)

In `applyVisibility`:

```diff
     function applyVisibility(selected, query, status) {
       let anyVisible = false;
+      const counts = {};
       document.querySelectorAll(".tile, #deadline-list li").forEach((tile) => {
         ...
         tile.style.display = visible ? "" : "none";
+        if (visible && tile.classList.contains("tile")) {
+          const sec = tile.closest(".tiles")?.dataset.section;
+          if (sec) counts[sec] = (counts[sec] || 0) + 1;
+        }
         if (visible) anyVisible = true;
       });
+      document.querySelectorAll("[data-sec-count]").forEach((el) => {
+        el.textContent = counts[el.dataset.secCount] || 0;
+      });
```

The `classList.contains("tile")` guard matters: deadline rows share the
selector but are not section tiles. CSS: `h2 .n` count styling from the demo.

## C1 — concert page: move the "Next for you" strip up, add its countdown

**Correction after CSS verification:** this component already exists and ships.
`.standing` ("Next for you", accent left border, style.css `.standing`) is
rendered by `_round_rows.html:96-106` at the top of the rounds region — but
that is *below* the performers block, and it carries no countdown. So C1 is a
move plus one addition, with two subtleties the first draft missed:

**Staleness (owner review).** `_round_rows.html` is exactly the fragment the
outcome-capture POST re-renders on the concert page (`outcomes.py:134`) — today
the strip updates live when you record an outcome. Naively moving it into the
detail header freezes it until reload. The fix is the same out-of-band pattern
`_board.html` already keeps on Home: the strip becomes its own partial with a
stable id, and the outcome route re-renders it with `hx-swap-oob`.

**The pill stays (owner review).** The first draft's diff replaced the status
pill with the countdown whenever a date exists — silently dropping your
standing from the strip. Rejected: countdown (time) and pill (standing) are two
facts; the strip shows both, pill under the countdown.

New partial `src/app/web/templates/_standing_strip.html`:

```html
{# "Next for you" -- rendered in the concert header on GET, and re-rendered
   with oob=true by POST /rounds/{id}/outcome so recording an outcome updates
   it live (the same contract _board.html keeps on Home). The outer wrapper is
   ALWAYS in the DOM, empty when the reader has no standing: it is the oob
   swap target, and a target that vanished between renders could not be
   swapped back in. #}
<div id="concert-standing"{% if oob %} hx-swap-oob="true"{% endif %}>
  {% if next_row %}
  <div class="standing">
    <div>
      <div class="l">{{ _("Next for you") }}</div>
      <div class="w">{{ loc(next_row.round_, "label") }}{% if next_row.primary_anchor %} — {{ deadline_label(next_row.primary_anchor) }}{% endif %}</div>
      <div class="s num">{% if next_row.primary_at_utc %}{% set sl = dual_lines(next_row.primary_at_utc, tz) %}{{ sl[0] }} {{ sl[1] }}{% else %}{{ _("No date announced yet") }}{% endif %}</div>
    </div>
    <span class="countside">
      {% if next_row.primary_at_utc %}
      <span class="countdown">
        <span class="big num" data-countdown-big data-iso="{{ next_row.primary_at_utc.isoformat() }}"></span>
        <span class="unit"></span>
      </span>
      {% endif %}
      {{ status_pill(next_row.outcome) }}
    </span>
  </div>
  {% endif %}
</div>
```

`_round_rows.html` — cut the standing block (lines 92-106), which the partial
now owns. `concert_detail.html` — include it in `<header class="chead">`, after
the `<h1>`/tags block:

```diff
+    {% include "_standing_strip.html" %}
```

`outcomes.py` concert-page branch — re-render the strip out of band alongside
the rounds region (one context build feeds both renders):

```diff
         concert = await get_concert_by_event_id(session, event_id)
         db_user = await session.get(User, user.id)
-        return HTMLResponse(templates.get_template("_round_rows.html").render(
-            request=request,
-            user=user,
-            tz=db_user.timezone if db_user else settings.default_timezone,
-            **await concert_rounds_context(session, user.id, concert),
-        ))
+        tz = db_user.timezone if db_user else settings.default_timezone
+        ctx = await concert_rounds_context(session, user.id, concert)
+        return HTMLResponse(
+            templates.get_template("_round_rows.html").render(
+                request=request, user=user, tz=tz, **ctx)
+            + templates.get_template("_standing_strip.html").render(
+                request=request, user=user, tz=tz, oob=True, **ctx)
+        )
```

Notes: `next_row` must be visible to the detail GET's header include — the GET
builds the rounds region from the same `concert_rounds_context`, so this is a
context-key check, not new query work. The htmx oob swap keys off
`#concert-standing` (outerHTML). `.standing` already collapses to one column at
max-width:900. New CSS: `.standing .countside` (flex column, right-aligned,
countdown over pill) + `.standing .countdown` — see demo. No new strings; the
concert-page render test should assert the strip appears in `.chead` and that
the outcome POST response contains `hx-swap-oob`.

## C2 — REJECTED (owner, 2026-07-24)

Proposal was: fold the concert header's Edit/Export buttons into a kebab.
Rejected: the kebab was made destructive-only in the 2026-07-24 editor
coherence pass, and relaxing that rule a day later to hide two quiet buttons
dilutes a rule that just started paying rent. Two buttons aren't clutter. The
editor bar stays as shipped. No changes here; the demo's C section shows C1+C3
only.

## C3 — concert page: "via Liella! + 1 more"

Why: "Tracked" is derived (`tracked_concert_ids`, invariant 8), and users can't
see why. One line beside the Following control demystifies it.

`src/app/web/templates/_following_toggle.html`, in both `following` branches:

```diff
   <span class="clock">{{ _("You will be reminded about every round below.") }}</span>
+  {% if via_tags %}
+  <span class="via">{{ _("via") }}
+    {% for t in via_tags[:2] %} <span class="tchip">{{ loc(t, "name") }}</span>{% endfor %}
+    {% if via_tags | length > 2 %} {% trans n=via_tags | length - 2 %}+{{ n }} more{% endtrans %}{% endif %}
+  </span>
+  {% endif %}
```

Backend: `following_toggle_context` gains `via_tags` — the viewer's followed
tags that match this concert (the same join `tracked_concert_ids` performs,
returning names). Empty for a manually `subscribed` concert (then the line
reads "via — you follow this event directly": decide copy at implementation).
New strings: "via", "+{n} more".

## D1 — create form: numbered spine

`src/app/web/templates/concert_new.html`:

```diff
-<form class="stack wide editor" method="post" action="/concerts" id="new-concert"
+<form class="stack wide editor numbered" method="post" action="/concerts" id="new-concert"
       data-variant-scope data-variant-guard>
 
+  <div class="section-head">
+    <h2>{{ _("Event") }}</h2>
+    <p class="dim tiny">{{ _("Title and URL — required.") }}</p>
+  </div>
   <div class="ebar">
```

`style.css` (pure counters — no other markup):

```diff
+.numbered { counter-reset: spine; }
+.numbered .section-head h2 { counter-increment: spine; display: flex; align-items: baseline; gap: .55rem; }
+.numbered .section-head h2::before {
+  content: counter(spine);
+  font-size: .72rem; font-weight: 700; color: var(--accent);
+  border: 1.5px solid var(--accent); border-radius: 50%;
+  width: 1.35rem; height: 1.35rem; display: inline-grid; place-items: center;
+  flex: none;
+}
```

Notes: create page only (the edit page is rounds-first by design — numbering
would lie there). `border-radius: 50%` is safe: the radius test bans only the
literals `6px`/`8px`, and 50% already ships (`.howstep .k`, `.avatar`, the
board dots). New strings: "Event", "Title and URL — required.".

## D2 — editor: live "this round covers…" legend

Why: "no chips selected = whole event" is currently stated once at the top and
silently surprises. The legend sits under the chips and follows every toggle.

**One msgid, not two (owner review).** The first draft rendered the legend
server-side with a `{% trans %}` block *and* client-side with
`_("This round covers {names}.")` — two different msgids for one sentence,
both needing translation, free to drift. The fix: the server renders an empty
span and JS builds the legend (initial fill included) from the single
`{names}` msgid. No-JS editors see no legend — consistent with the chips
themselves, which render server-side but only *toggle* with JS.

`src/app/web/templates/_round_leg_chips.html`:

```diff
   {% for d in legs %}
   <button ...>...</button>
   {% endfor %}
+  {#- Empty on purpose: _leg_chips_script.html fills this on load and on every
+      toggle from the ONE {names} msgid. No server-side text here -- a second
+      ({% trans %}) msgid for the same sentence could drift from the first. -#}
+  <span class="covers" data-covers></span>
 </div>
```

The legend is a flex item inside `.leg-chips` (a flex row), so it must claim
its own line — its CSS is `.covers { flex: 1 1 100%; ... }` (see demo).

`src/app/web/templates/_leg_chips_script.html`:

```diff
+    const COVERS_ALL = {{ _("Nothing selected — applies to the whole event.") | tojson }};
+    const COVERS_SOME = {{ _("This round covers {names}.") | tojson }};
+    function updateCovers(box) {
+      const line = box?.querySelector("[data-covers]");
+      const field = box?.querySelector('input[name="round_legs"]');
+      if (!line || !field) return;
+      const chosen = new Set(field.value.split(/[\s,]+/).filter(Boolean));
+      const names = legsInDom().filter((l) => chosen.has(l.key)).map((l) => l.label);
+      line.textContent = names.length
+        ? COVERS_SOME.replace("{names}", function () { return names.join(", "); })
+        : COVERS_ALL;
+    }
+
     function renderChips(box) {
       ...
+      updateCovers(box);
     }
```
in the delegated chip-toggle handler, after `chip.setAttribute(...)`:

```diff
+      updateCovers(chip.closest("[data-leg-chips]"));
```
and the initial fill, at the end of the IIFE (safe on both editor pages: a
saved leg's day_key IS its ConcertDay id, so the server-rendered hidden value
and `legsInDom()` agree):

```diff
+    document.querySelectorAll("[data-leg-chips]").forEach(updateCovers);
```

Notes: labels are joined with ", " for all locales — acceptable, but the msgid
carries `{names}` so translators control placement (and the i18n catalogue test
checks placeholders survive translation). A reference the chips can't render (a
cancelled leg) stays invisible to the legend too; the hidden input round-trips
it untouched as today (edge case: legend says "nothing selected" while a
cancelled leg is still bound — acceptable, matches the chips' own behaviour).
New strings: the two above, and only those two.

## E1 — tags page: chips ⇄ table view

Why: chips are for picking, tables for comparing. Editors audit usage; give
them the density. Editor-only.

`src/app/web/templates/tags.html`, after the search input:

```diff
+{% if user.is_editor %}
+<div class="viewbar">
+  <div class="viewtoggle" id="tagViewToggle">
+    <button type="button" data-view="chips" aria-pressed="true">{{ _("Chips") }}</button>
+    <button type="button" data-view="table" aria-pressed="false">{{ _("Table") }}</button>
+  </div>
+</div>
+<div id="tag-table-wrap" hidden>
+  <table class="tagtable">
+    <thead><tr>
+      <th>{{ _("Tag") }}</th><th>{{ _("Kind") }}</th>
+      <th class="r">{{ _("Events") }}</th><th class="r">{{ _("Followers") }}</th>
+      <th class="r">{{ _("Upcoming") }}</th>
+    </tr></thead>
+    <tbody>
+      {% for t in all_tags %}
+      {% set c = counts[t.id] %}
+      <tr data-name="{{ t.name | lower }}">
+        <td><button type="button" class="tchip"
+              onclick="document.getElementById('tag-dialog-{{ t.id }}').showModal()">{{ loc(t, "name") }}</button></td>
+        <td><span class="dim">{{ {"franchise": _("franchise"), "group": _("group"), "venue": _("venue"), "artist": _("performer")}.get(t.kind.value, t.kind.value) }}</span></td>
+        <td class="r">{{ c.concerts }}</td>
+        <td class="r">{{ c.followers }}</td>
+        <td class="r">{{ c.upcoming }}</td>
+      </tr>
+      {% endfor %}
+    </tbody>
+  </table>
+</div>
+{% endif %}
```

Small page script:

```diff
+document.getElementById("tagViewToggle")?.addEventListener("click", function (e) {
+  const b = e.target.closest("button[data-view]");
+  if (!b) return;
+  this.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
+  document.querySelector(".tags-page").hidden = b.dataset.view !== "chips";
+  document.getElementById("tag-table-wrap").hidden = b.dataset.view !== "table";
+});
```

Notes:
- The kind-label dict is currently inlined in the `tag_dialog` macro — hoist it
  to a page-level `{% set %}` so both macro and table use one copy.
- Reuse shipped vocabulary instead of inventing: the row's tag button is the
  existing `button.tchip` (no `.linklike` needed), and the kind cell is a plain
  `.dim` span (eyebrow discipline, G4 — no `.eyebrow`, no `.k-pill`).
- `filterChips(this, '.tags-page')` won't reach the table's `data-name` rows;
  either scope the search to a common wrapper or accept chips-view-only search
  (demo keeps search working in both — recommended: wrap both views in one
  `.tags-scope` div and change the filterChips scope).
- `hidden` attribute + `[hidden] { display: none !important }` is already
  global. New strings: "Chips", "Table", "Tag", "Kind", "Events", "Followers",
  "Upcoming".
- **Standalone (owner review):** E1 ships as this 5-column table — the
  Following column belongs to E2, which introduces both the bell partial and
  the `sub_by_tag` context it needs. Either order works: E1-first ships without
  the column; E2-first adds a column to a table that doesn't exist yet, so the
  documented order is E1 → E2.

## E2 — tags page: follow/unfollow bell

Why: following a tag currently requires Preferences or a concert page. The
directory is the natural place. E2 adds one column to E1's table plus the
viewer subscription state it renders from.

The column (against E1's table markup):

```diff
       <th class="r">{{ _("Upcoming") }}</th>
+      <th>{{ _("Following") }}</th>
```
```diff
         <td class="r">{{ c.upcoming }}</td>
+        <td>{% include "_tag_follow_bell.html" %}</td>
       </tr>
```

New partial `src/app/web/templates/_tag_follow_bell.html` — reusing the
shipped `.swb` toggle (the Preferences Notify/Auto-apply vocabulary:
ok-green when `aria-pressed="true"`), not a new `.bell` class:

```html
{# One tag's follow state as a quiet toggle. Real forms, no JS needed. The
   redirect is the `next` FORM FIELD -- POST /subscriptions and
   /subscriptions/{id}/delete both take next_url = Form("/preferences",
   alias="next") and bounce through _safe_next (preferences.py:303/379).
   Without the hidden input the bell would dump the user on Preferences
   after every toggle. #}
{% set sub = sub_by_tag.get(t.id) %}
{% if sub %}
<form method="post" action="/subscriptions/{{ sub.id }}/delete" class="inline">
  <input type="hidden" name="next" value="/tags">
  <button class="swb" aria-pressed="true" title="{{ _('Stop following this tag') }}">🔔 {{ _("Following") }}</button>
</form>
{% else %}
<form method="post" action="/subscriptions" class="inline">
  <input type="hidden" name="tag_id" value="{{ t.id }}">
  <input type="hidden" name="next" value="/tags">
  <button class="swb" aria-pressed="false" title="{{ _('Follow this tag') }}">{{ _("Follow") }}</button>
</form>
{% endif %}
```

Backend: `routes/tags.py`'s directory context gains `sub_by_tag` for the
signed-in viewer — the same subscription map Preferences builds; extract it
into `service.py` if it isn't already shared. Route compatibility verified:
`POST /subscriptions` takes `tag_id` with `preset_id`/`notify` defaulting
(preferences.py:303), so the bare bell form works — note it follows with
**notify OFF** (no DM on new events), unlike the Preferences picker which
defaults notify on. Owner call: acceptable (the bell is a lightweight follow),
or ship the bell with a hidden `notify` on. (The first draft also said the
routes "redirect back via Referer" — wrong, verified: they take the `next`
field, default `/preferences`. The hidden inputs above are the whole fix.)
New strings: "Following", "Follow", the two titles.

## F1 — global: htmx progress bar

`src/app/web/templates/base.html`:

```diff
 <body class="has-tabbar" data-tz="{{ tz or '' }}" data-tz-auto="{{ '1' if tz_auto else '0' }}">
+  <div id="hxbar" aria-hidden="true"></div>
   <header class="site">
```

In base.html's shared script:

```diff
+    // Progress bar: creeps while any htmx request is in flight, snaps to full
+    // and fades on completion. No timing is asserted -- it's a feel, not a meter.
+    (function () {
+      var bar = document.getElementById("hxbar");
+      document.body.addEventListener("htmx:beforeRequest", function () {
+        bar.classList.remove("done"); bar.classList.add("on");
+      });
+      document.body.addEventListener("htmx:afterRequest", function () {
+        bar.classList.remove("on"); bar.classList.add("done");
+        setTimeout(function () { bar.classList.remove("done"); }, 700);
+      });
+    })();
```

`style.css`: the `#hxbar` rules from the demo. No strings.

## F2 — global: `:focus-visible` ring

`style.css`, one rule:

```diff
+/* One global keyboard focus ring. :focus-visible only, so pointer users never
+   see it; the demo components already carry per-component copies of this. */
+:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

Note: one shipped rule will defeat the global ring on keyboard focus:
`.picker-body input[type=search]:focus { outline: none; ... }` (style.css:422)
has higher specificity than a bare `:focus-visible`, and `:focus` also matches
keyboard focus. Change that rule to keep its border/background change but drop
the `outline: none` (or scope it `:focus:not(:focus-visible)`). Sweep for other
`outline: none` occurrences when implementing — that's the one found in this
pass.

## F3 — global: outcome-capture toast

Why: recording an outcome moves a board card with zero acknowledgement.

`src/app/web/routes/outcomes.py` — both `HTMLResponse` returns gain the header
(the concert-page branch and the Home board branch):

```diff
-        return HTMLResponse(templates.get_template("_round_rows.html").render(
-            ...
-        ))
+        return HTMLResponse(
+            templates.get_template("_round_rows.html").render(...),
+            headers={"HX-Trigger": json.dumps({"toast": {"outcome": outcome.value}})},
+        )
```

`base.html` — container, message map (locale-correct at render time), listener:

```diff
+  <div id="toasts" aria-live="polite"></div>
```
```diff
+    // Outcome toast. The outcome POST names what happened in an HX-Trigger
+    // header; htmx re-fires it as a DOM event. Keys are LotteryOutcome values
+    // (domain/types.py: not_applied/applied/won/lost/paid) -- the map must
+    // cover all five or the generic fallback shows.
+    var TOAST_MSGS = {{ {
+      "applied": _("Recorded — moved to Applied"),
+      "won": _("Marked as won — payment reminder set"),
+      "lost": _("Recorded — marked as lost"),
+      "paid": _("Recorded — ticket secured"),
+      "not_applied": _("Noted — reminders for this round are off"),
+    } | tojson }};
+    document.body.addEventListener("toast", function (e) {
+      var msg = TOAST_MSGS[e.detail && e.detail.outcome] || TOAST_MSGS.applied;
+      var host = document.getElementById("toasts");
+      var t = document.createElement("div");
+      t.className = "toast";
+      var dot = document.createElement("span");
+      dot.className = "ok-dot";
+      t.appendChild(dot);
+      t.appendChild(document.createTextNode(msg));
+      host.appendChild(t);
+      setTimeout(function () { t.remove(); }, 3200);
+    });
```

Notes:
- Verify the "not applying" narrow action's flow: if it posts to a different
  route (a prune/opt-out route rather than `/rounds/{id}/outcome`), that route
  needs the same one-line header, or its toast never fires.
- The JS-less path (plain redirect) shows no toast — acceptable; the board
  itself is the confirmation there.
- `#toasts` + `.toast` CSS from the demo. New strings: the five messages.

---

---

# Part 2 — the aesthetic pass (G1–G7)

Whole-sheet judgements, each with a frame in the demo's Part 2. G1/G3/G5/G7 are
mechanical; G2 is a phased refactor; G4 is guidance with one concrete change;
G6 is a decision, not code.

## G1 — one weight ramp: 400 / 600 / 700

Why: the sheet uses 400/500/550/600/650/660/680/700, but `system-ui` is Segoe
UI on Windows, which has no intermediate grades — 550/650/680 snap to 600/700
there. The fine gradation only renders on macOS, so the design says two
different things on two platforms.

`style.css`, mechanical sweep (also grep templates/static for inline
`font-weight`):

| find | replace |
|------|---------|
| `font-weight: 550` | `font-weight: 600` |
| `font-weight: 650` | `font-weight: 700` |
| `font-weight: 660` | `font-weight: 700` |
| `font-weight: 680` | `font-weight: 700` |

Notes: 54 occurrences in style.css (550×9, 650×42, 660×1, 680×2); `500` stays
only where it means "normal emphasis" (`.col-head .count`, `.tsec h3 span` —
judge each). Visually verify on macOS after the sweep, the only platform where
anything changes. No strings, no layout change (weights are not used for sizing
anywhere measured).

## G2 — callout grammar: two shapes, ten one-offs

Why: `.callout(+warn)`, `.banner-warn`, `.signin-note`, `.dupe`, `.upgradebox`,
`.standing`, `.next`, `.feedbox`, `.danger-row`, `.danger` are ten hand-rolled
treatments of two ideas. Two base shapes, three tones each:

```diff
+/* Status card: an ongoing state. Raise ground, left edge in the tone colour.
+   Absorbs .standing, .next, .upgradebox, .feedbox. */
+.edgecard {
+  border: 1px solid var(--line); border-left: 3px solid var(--accent);
+  border-radius: 3px; background: var(--raise); box-shadow: var(--shadow);
+}
+.edgecard.dg { border-left-color: var(--danger); }
+.edgecard.ok { border-left-color: var(--ok); }
+/* Banner: needs attention. Wash ground, full border in the tone colour.
+   Absorbs .callout(+warn), .banner-warn, .signin-note, .dupe, .danger-row,
+   .danger. */
+.banner { border: 1px solid var(--accent); background: var(--accent-wash); color: var(--accent); border-radius: 3px; }
+.banner.warn { border-color: var(--off); background: var(--off-wash); color: var(--off); }
+.banner.dgr { border-color: var(--danger); background: var(--danger-wash); color: var(--danger); }
```

`.banner` name check: no collision (`banner-warn` is a different class).
Phasing, visual-parity-first: (1) add both shapes + tones; (2) migrate one
callout at a time, template by template, deleting the old class when its last
caller moves — each migration is its own small diff, verified by the page's
render test. Don't do the migrations blind in one commit; ten classes touch
twelve templates.

## G3 — write the radius system down

`style.css`, after the `[hidden]` rule comment block:

```diff
+/* ── Radius system ─────────────────────────────────────────────────
+   3px              everything, by default (cards, inputs, buttons, pills)
+   999px            chips and chip-like toggles (.chip, .tchip, .kindpick)
+   4px              overlay CARDS only: .kmenu, dialogs (.prune)
+   50%              dots, avatars, counters, ticks (circles, not radii)
+   14px 14px 0 0    phone bottom sheets (dialogs + .fsheet panel)
+   test_style_uses_3px_radius_not_6or8 guards the 3px default. */
```

## G4 — eyebrow discipline: labels only, never metadata

Guidance, one concrete change. The `.eyebrow` (uppercase, letterspaced) earns
its keep as lineage (the F · G performer line on cards/tiles/peek) and as a
section/field label (`.performers .plabel`, `.leg-chips` "Applies to", `.tsec`
heads). It should never decorate metadata. Concrete change: the E1 tags-table
kind cell renders `.dim`, not `.eyebrow` (already reflected in the E1 diff and
the demo). When touching other pages, demote any metadata eyebrow to `.dim` in
passing — do not do a dedicated sweep; the shipped sheet is already close.

## G5 — light `--ok` darken (measured)

Measured WCAG ratios from the token hexes (ledger in the demo): every tone pair
passes 4.5:1 except light-mode ok-on-wash at **4.38:1**, and pills render at
.6875rem where the 4.5 bar applies. Minimal nudge:

`style.css` — `:root` AND `:root[data-theme="light"]` (two places):

```diff
-  --ok: #1a7f4e;
+  --ok: #187a49;   /* 4.38:1 -> 4.68:1 on --ok-wash (WCAG AA, small text) */
```

Note: this shifts every light-mode ok usage (pills, `.follow.on`, setup
stepdots), not just the pill — all of them get *more* contrast, so the whole
family improves. Dark `--ok` (#5fc48c, 6.98:1) is untouched.

## G6 — motion budget: one hover, zero decoration — DECIDED

**Owner decision (2026-07-24): no breathing dot.** The app's motion
personality is one 150ms card lift; the column-head colours from A1 already
carry "money at risk". A1's diff has been updated (the `@keyframes dotbreathe`
block and its `prefers-reduced-motion` guard are gone), and the demo's A1 and
G6 frames show the no-dot version. Standing rule going forward: a new
animation has to argue its way in like this one did.

## G7 — `.badge` joins the 3px family

`style.css:132`:

```diff
-.badge { color: var(--ok); font-style: normal; font-size: .8em; border: 1px solid currentColor; padding: 0 .3em; border-radius: 4px; }
+.badge { color: var(--ok); font-style: normal; font-size: .8em; border: 1px solid currentColor; padding: 0 .3em; border-radius: 3px; }
```

---

## Implementation order (if all approved)

1. F2, F1, D1, A1, G3, G5, G7 — tiny, no backend, immediate feel.
2. A2, B2, D2, F3, G1, G4 — small, still no real backend work (F3 is one
   header; G1 is a mechanical sweep, verified visually).
3. B1, E1, E2 — medium, need small context additions.
4. C1, C3 — C1 is a render-site move; C3 needs the `via_tags` derivation in
   `service.py`. (C2 was rejected.)
5. G2 — the callout consolidation, phased one caller at a time, after
   everything above has settled (C1's `.standing` move should land first so G2
   migrates its final position, not its old one).

Each step: pybabel extract/update + fill both `.po` files for its new strings,
`uv run pytest -q` and `uv run ruff check .` green, demo updated if the shipped
design deliberately moves from these frames.
