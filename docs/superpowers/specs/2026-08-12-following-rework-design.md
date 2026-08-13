# The Following rework

Date: 2026-08-12
Status: design agreed, ready to plan
Entry: WISHLIST's unranked head-of-Proposed entry, "Following is due a rework"

## What this is

The owner: *"I wanted to rework how following tags work since it's getting
pretty big."* Filed unranked because its scope did not exist yet. This is that
scope.

## The problem, measured

Not estimated — computed from the live catalogue export (`tags.yaml`,
735 tags) and read out of the templates.

| | |
|---|---|
| Tags in the catalogue | **735** — 334 artist, 318 character, 65 group, 14 venue, 4 franchise |
| Chips the Preferences picker renders, in one `<details>` | **878** |
| — of which one flat "Solo artists" section | **310** |
| `group_members` queries per page render | **65**, an N+1 — **on both `/preferences` and `/tags`** |
| Tags unfindable by the name they display | **681 of 735** |
| Characters offered anywhere in the Preferences picker | **0** |

Four things follow from those numbers, and only the first was in the original
complaint:

1. The picker is 878 chips behind one fold, on a page the owner visits to prune
   and to tune — never to browse.
2. **Search is broken for 93% of the catalogue.** `data-name` carries `t.name`
   (Japanese) while the chip displays `loc(t, "name")`. An English viewer sees
   `Aina Aiba` and types "Aiba"; `data-name` is `相羽あいな`; nothing matches.
   This is live today on both surfaces.
3. **Following a character is impossible.** `preferences.py` never mentions
   `CHARACTER`. 318 tags — 43% of the catalogue — can only be followed if they
   happen to be group members, and then only as an unlabelled member chip.
4. **On `/tags`, following is unreachable for non-editors.** The only follow
   control (`_tag_follow_bell.html`) renders solely in the table view, and the
   chips⇄table toggle is wrapped in `{% if user.is_editor %}`. A non-editor is
   shipped 735 hidden table rows containing follow buttons they cannot reveal,
   and the chips view they can see is 878 inert `<span>`s.

## Decisions, and who made them

All owner rulings, taken during the 2026-08-12 brainstorm.

- **The picker leaves Preferences entirely.** *"At this point I won't suggest
  using preferences to follow someone new since the amount of tags really
  warrants a dedicated page for that."*
- **`/tags` becomes the follow surface.** It is already `require_user`, already
  in the pinned Home/Discover/Tags nav, and already has the hierarchy, the
  search box and a working (if unreachable) follow control.
- **Split pills, each half independently followable.** A character and her
  seiyuu render as one `.mchip` — the shipped concert-page shape — and clicking
  a half follows that half's tag. The owner's words: *"instead of having the
  bell half, display the seiyuu on the other side… If you click on the
  character half, only that half turns green and they only follow the
  character, vice versa."*
- **Split pills wherever a character appears**, including group rows.
- **Subunit de-dup, `/tags` only** — see below.
- **Empty parent rows render silently**; no per-group folds; search is how you
  reach a name in a large group.
- **`/following` is its own page, not a filter on `/tags`.** The owner's
  reasoning, which beat the alternative: a chip means *follow* on one surface
  and *configure* on the other, and one page cannot mean both.
- **Characters are ordinary tags.** The Characters section is deleted;
  *"individual characters should be treated as an individual artist."*
- **The table view is deleted from `/tags`** and is not built for `/following`.
- **Editor mode**: a `Follow ⇄ Edit` switch, editor-only.
- **The preset default governs future follows; a separate explicit action fills
  existing blanks**, never overwrites a set preset, and reports what it skipped.

### The one reversal, and why it is sound

The 2026-08-01 character/seiyuu/subunit spec ruled **"repetition kept"**: a
subunit's members also appear in the parent group's cluster. The alternative was
rejected because *"it makes the parent cluster stop being a truthful lineup, and
what it displays would depend on which OTHER tags happen to be attached, so two
shows with identical lineups would render differently."*

That reasoning is about a **concert bill** — variable, per-show, dependent on
what an editor attached. `/tags` shows **catalogue structure**, which is fixed:
there is no "attached", nothing varies between two views, and no lineup is being
asserted. The objection does not transfer, so the rule is reversed **for `/tags`
only**. The concert page is untouched and keeps repetition.

What it buys, measured:

| | before | after |
|---|---|---|
| Member chips in group rows | 485 | **343** |
| SideM parent row | 49 | **0** |
| Shiny Colors parent row | 28 | **0** |
| Cinderella Girls parent row | 99 | **99** |
| Parent rows rendering empty | 0 | **6** |

**It does not solve the width problem it was aimed at.** The two largest groups
have no subunits at all, so they are untouched. It is worth doing on its own
merits — a subunit member belongs to the subunit — and the 99-member row is
handled by search instead, deliberately.

## The three surfaces

| Surface | Owns | A chip click means |
|---|---|---|
| `/tags` | the catalogue — everything you *could* follow | follow / unfollow (per half on a split pill) |
| `/following` (new) | your subscriptions — what you *do* follow | open that tag's config dialog |
| Preferences → Following | a count, the standing default, skipped events | — |

`/following` is reachable from **both** Preferences' "Manage →" and a link on
`/tags`. It takes no nav slot: the header stays Home / Discover / Tags.

## `/tags` — the catalogue

**Chips become follow controls for everyone.** Each is a real `<form>` posting
to `/subscriptions` or `/subscriptions/{id}/delete` with `next=/tags`, exactly
as `_tag_follow_bell.html` already does — so it works with JavaScript off. A
followed chip carries the ok wash and a tick.

**Split pills.** Where a character has a `voiced_by_tag_id`, she and her seiyuu
render as one `.mchip` in two halves, each half its own form. Where she has
none, a plain chip — the concert page's conditional-merge rule, unchanged. Four
states exist and all are reachable: neither followed, character only, seiyuu
only, both.

This distinction is already real in the data model and has had nowhere to be
expressed. Invariant 3: attaching 今井麻美 pulls in no characters, *"because she
also appears as herself at events with no im@s connection."* Following the
character and following the performer are different subscriptions; this is the
first surface where a user can act on that.

**Sections after this change:** franchises and their groups (with subunit
de-dup), venues by region, performers with no group. **The Characters section is
deleted** — every one of the 318 characters is a member of at least one group
(measured: zero exceptions), so once group rows carry split pills that section
renders nothing new. A character with no group would fall into "performers with
no group" like any artist, which is what "treated as an individual artist"
means.

**The table view is deleted.** Its markup, `#tag-table-wrap`, and the
`Chips ⇄ Table` half of the view toggle all go.

> **TRAP: `.tagtable` is shared.** Six other templates use that class —
> `admin_broadcast`, `admin_deliveries`, `admin_discoveries`,
> `admin_fetch_domains`, `admin_quiet_ladders`, `rehearsal`. **The CSS rule must
> stay.** Only the `/tags` markup goes. Deleting the rule silently flattens six
> admin pages.

Editors lose no information: the per-tag edit dialog already shows events,
followers, members and upcoming — the table was a second rendering of numbers
the dialog carries.

**Editor mode.** `Follow ⇄ Edit` joins the existing editor-only `.viewbar`,
same `aria-pressed` vocabulary, and — matching the toggle it replaces —
**does not persist**, so a forgotten mode expires on reload. In Edit, a chip
opens its tag dialog as today; chips render dashed-accent and **drop their
follow ticks**, because a chip showing "following" while its click opens an
editor is lying about what it does. An `.edgecard` strip states the mode.
Non-editors see no toggle, no strip, and no mode.

## `/following` — the new page

Lists the viewer's subscriptions as **plain chips** — one chip per subscription,
grouped by franchise. Not split pills: this page lists what you follow, and a
subscription is one tag.

A chip states its own deviation from the standing default: a different preset
shows that preset's name, notifications-off shows a muted 🔕. Everything plain
obeys the default. Scanning forty chips, only the exceptions draw the eye.

**Clicking a chip opens its config dialog**, holding the three things a
subscription has:

- **Reminder preset** — a select over the user's presets, plus "none". Writes
  `TagSubscription.preset_id`.
- **Notifications** — writes `TagSubscription.notify`.
- **Unfollow** — deletes the subscription.

Plus a context line (event counts, and the character↔seiyuu link where one
exists) so the decision can be made without leaving the dialog.

Search filters what you follow. No table view.

## Preferences → Following

Reduced to fixed height regardless of how many tags are followed:

- the count, with **"Manage →"** to `/following`
- the **standing default**: which preset new follows get, and whether they
  notify
- the existing **skipped events** list (concert-level opt-outs — this is the
  visible half of invariant 8's overrides and has no home on a tag catalogue)

> **Correction, 2026-08-13**: this originally said the standing default
> covers both which preset new follows get AND whether they notify. Phase 4
> shipped only the preset half. `ReminderPreset.is_default` is a per-PRESET
> flag that already existed and phase 4 widens rather than adds to, which is
> how the whole four-phase rework shipped with zero migrations; a standing
> NOTIFY default has no equivalent column to widen, and the owner chose not
> to add a `User` column for it (2026-08-13) rather than let the fill-vs-add
> question reopen. Notify stays a per-tag setting, written one at a time
> through `/following`'s dialog. The ordering below is unchanged — only this
> claim is corrected here, rather than silently rewritten.

The picker, the per-tag `.subrow`s and their toggles all go.

### The standing default, and the retroactive fill — two things, not one

The owner's wording covers both, and conflating them would produce a setting
that silently rewrites data on save. They are separate:

**1. The standing default** (a setting). Which preset new follows get, and
whether they notify. Governs **future follows only**. Changing it never touches
an existing subscription — a setting that rewrites rows when you change it is
the surprise this design must not ship.

**2. "Apply my default preset to all followed tags"** (an explicit action, a
button beside the setting). Writes the default into every subscription whose
`preset_id` is **NULL**, and leaves every subscription that already carries its
own preset **exactly as it is**. It then **reports what it did**: how many were
filled, and how many were left alone because they had their own.

The report is the point, not a courtesy. Without it the action is indis-
tinguishable from one that overwrote everything, and the user has no way to
tell which happened.

This is the same shape as the catalogue tag import, which is a good sign it is
right — CLAUDE.md: *"a blank on the DB side is a FILL applied automatically
(writing into emptiness cannot lose anything)… two differing values are a
CONFLICT somebody resolves."* Same rule: fill blanks silently, never clobber a
deliberate choice, report what was left alone.

## Search

Now load-bearing, because there are no folds and a 99-member group is reached
only by searching.

- **`data-name` carries all three names** — `name`, `name_en`, `name_zh` —
  lowercased and joined. This fixes the 681-of-735 gap and is a prerequisite for
  everything else, not a nicety.
- **One `data-name` per split pill**, on the pill, carrying both tags' names.
  Never one per half: `filterChips` hides the elements it matches, so a
  per-half attribute means a seiyuu search hides the character half and renders
  half a pill.
- **`filterChips` hides containers that empty.** After filtering, a group row or
  section with no visible `[data-name]` descendant hides itself. Without this a
  search returns the whole page skeleton with one chip in it.

> **Why this cannot be CSS.** `:empty` does not match these elements: template
> indentation puts whitespace text nodes inside them. This codebase already
> learned it on the concert page — see the `.chiprow:empty` comment in
> `concert_detail.html`. It must be JS.

## Data model and queries

**No migration.** `TagSubscription` already carries `preset_id` as a nullable FK
to a specific preset; today's Auto-apply boolean merely links the user's default
or clears it (`routes/preferences.py`: *"a preset either IS or ISN'T linked"*).
The dialog's select writes the same column. Per-tag presets are being exposed,
not added.

**Invariant 8 is untouched.** Following stays derived, `tracked_concert_ids`
remains the single derivation, and every subscription write still calls
`reinstate_user_rules`. No second derivation may appear on either new surface.

**The N+1 goes.** `/tags` and `/preferences` each build
`{g.id: await group_members(session, g.id) for g in groups}` — 65 queries each.
Replace with one batched query returning members grouped by group id.
`/preferences` stops needing it at all once the picker leaves.

## Testing

- Every new page gets a logged-in GET render test (a missing one shipped a 500
  once).
- **Follow/unfollow works with JavaScript disabled** on `/tags` — assert the
  forms and their `next` fields, not just that chips render.
- **Search matches all three names.** Assert a tag is findable by its `name_en`
  when `name` is Japanese — the mutation being that `data-name` silently
  reverts to `t.name`.
- **A split pill carries exactly one `data-name`, containing both names.**
- **Editor mode is editor-only**: a non-editor render contains no toggle and no
  strip.
- **The retroactive fill skips subscriptions that already have a preset** — the
  assertion that matters is the skip, not the fill, and the reported counts must
  be asserted too, since an unreported skip is indistinguishable from an
  overwrite.
- **Changing the standing default writes no subscription rows.** The mutation:
  a well-meaning implementation that "keeps things in sync" on save.
- **`.tagtable` still exists in `style.css`** after the `/tags` markup is
  removed, because six admin pages depend on it.

## Suggested sequencing

This is larger than one sitting, and the pieces have a real dependency order.
Recorded here so the plan does not have to rediscover it:

1. **The search fix and the N+1**, first and alone. Both are live bugs on
   surfaces that exist today, both are independently shippable, and everything
   below assumes search works — with no folds, search is the only way to reach a
   name in a 99-member group.
2. **`/tags` becomes the follow surface**: chips as forms, split pills, subunit
   de-dup, Characters section deleted, table removed, editor mode. Largest
   piece; self-contained.
3. **`/following` and its dialog.** Depends on nothing in step 2 except the link
   between them.
4. **Preferences reduction**, last — this step removes the per-tag
   notify/preset toggles from Preferences, and nothing replaces them until
   this phase's `/following` dialog exists, so it must not land before that
   dialog does.

   > **Correction, 2026-08-12**: this originally read *"it is the step that
   > removes the only working follow path, so it must not land before `/tags`
   > replaces it."* Phase 2 shipped `/tags` as a working follow surface,
   > which retired that reason. The ordering is unchanged — only the reason
   > is corrected here, rather than silently rewritten.

Step 4 landing early would leave Preferences users with no way to set a
per-tag notify/preset default until `/following`'s dialog exists.

## Out of scope

- The concert page's subunit rendering — repetition stays there, deliberately.
- Discover's tag filters.
- Merging tags, which the app still cannot do.
- WISHLIST #17 (the demos being one grammar-migration behind the app) — the
  demos will need frames for these surfaces, but that is that entry's pass.

## Open items

None. Q1–Q4 from the brainstorm were all answered: `/following` is reachable
from both surfaces; the Characters section is deleted; the standing default
fills without overwriting and reports what it skipped; the table view is removed
from both pages.
