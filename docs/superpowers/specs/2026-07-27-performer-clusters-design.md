# Group the performer chips by group on the concert page

Date: 2026-07-27. Status: **implemented (2026-07-27)**, three tasks on
branch `performer-clusters`, off `main`. Designed with the owner (two
decisions recorded below, plus one ruling made mid-build — see
"Implementation deviations"). Was WISHLIST Proposed #1 — the last of the
owner's 2026-07-26 usage-feedback batch except the admin export.

## Problem

`concert_detail.html`'s Performing panel renders every attached GROUP chip
followed by every attached ARTIST chip in one flat row — "insanely
crowded" on a big lineup, in the owner's words. The concert already
carries the structure to fix it: the attached GROUP tags and the
`tag_members` association behind them.

This regroups the DISPLAY only. Invariant 3 is untouched by construction:
attaching a group materializes its members at that moment and editors
prune them, and that materialized set stays the truth. This reads it.

## Owner decisions (2026-07-27)

1. **A performer in several attached groups appears under EACH of them.**
   The repetition is information — she really is in both — and the
   clustering, not deduplication, is what fixes the wall. The alternative
   (once, under the most specific group) leaves the main group's cluster
   looking incomplete, which misleads exactly the reader who came to see
   that group.
2. **No folding.** The Performing panel is reference, not a to-do: you
   read it, you never act on it, so a click to see who is playing is
   friction without payoff. Labeled clusters are the whole fix.

## A. Derivation

The template cannot do this itself. `Tag.members` is a lazy
self-referential m2m and touching it during async template rendering is a
`MissingGreenlet` 500 — the failure this project has already shipped once.
So the grouping is derived service-side, in `db/service.py`:

```python
@dataclass(frozen=True)
class PerformerCluster:
    group: Tag | None            # None = the ungrouped trailer
    artists: tuple[Tag, ...]

async def performer_clusters(session, concert) -> list[PerformerCluster]
```

Inputs are the concert's already-eager-loaded `tags`: the attached GROUP
tags and the attached ARTIST tags. Membership comes from ONE query over
`tag_members` for the attached group ids — deliberately NOT the existing
`group_members(session, group_tag_id)` helper, which is per-group and
would be an N+1 on a franchise concert.

Rules:

- One cluster per attached GROUP tag, in the order the groups already
  render (`Concert.tags` is ordered by `Tag.name`), holding the attached
  ARTIST tags that are its members.
- An artist in several attached groups appears in each of their clusters
  (owner decision 1).
- A trailing cluster with `group=None` holds every attached artist that
  belongs to no attached group. Omitted when empty.
- A group with NO attached members still renders its label row with no
  chips. Dropping it would hide a tag that IS attached, and the empty row
  is itself informative — the group is on the bill but its line-up was
  never listed (or was pruned to nothing).
- Membership is read only for groups attached to THIS concert. A member
  whose group tag is absent stays in the trailer.

## B. Presentation

Each cluster is a label row plus its chips. The label reuses the existing
`.chip.grp` treatment so a group still looks like a group and still links
to its `eventernote_url` when it has one; artist chips are unchanged,
including their own eventernote links and the `nolink` title.

The panel header currently composes "N members, from the X group tags".
With group names now visible as cluster labels that sentence is
redundant, so the header simplifies to a **distinct** performer count —
distinct because summing cluster sizes double-counts anyone in two
groups. One msgid, plural-aware, replacing three composed ones.

New CSS classes (`.pcluster`, `.pclabel`) go in the main body of
`style.css`; any phone or tablet counterpart goes inside the existing
`@media (max-width: 700px)` / `701-1040px` sections — no new top-level
media query (`test_theme_and_tokens.py` pins the count at 6). Radius 3px,
existing tokens, both themes.

## C. What does not change

- Tag attachment, group expansion, pruning (invariant 3).
- The lineage line (`franchise · group`) above the panel.
- Tile display rules elsewhere, Discover, the editor's tag pickers.
- Venue/franchise chip rows on the same page.
- No schema change, no migration.

## Testing

- Service: one cluster per attached group in name order; an artist in two
  attached groups appears in both; ungrouped artists land in the trailer;
  the trailer is omitted when empty; a group with no attached members
  keeps its label row; a member whose group is not attached stays in the
  trailer; membership loads in ONE query (assert the statement count, so
  a future `group_members` loop cannot creep back in).
- Page: the render test IS the MissingGreenlet guard — a logged-in GET of
  a concert with groups must not touch `Tag.members` lazily. Assert the
  cluster labels render, that a two-group performer's chip appears twice,
  and that the header's count is distinct rather than the sum.
- i18n: the new count msgid filled in both catalogues with plurals
  intact; the three msgids it replaces removed if they become orphans.

## Implementation deviations (recorded)

1. **At ZERO the count disappears entirely** (owner ruling, 2026-07-27,
   made mid-build). Section B's "ONE plural-aware distinct count" read
   literally gives a groups-only bill — a GROUP attached with none of its
   members attached — the header "Performing — 0 performers". That is the
   opposite of the truth: such a bill is a line-up nobody has listed yet,
   not a concert with nobody on it, and the label rows the same section
   deliberately keeps already say so. The count is now simply absent in
   that state. **This is why no new msgid was needed for it** — a "line-up
   not listed" string was the alternative, and the absence carries the
   meaning without one.

2. **No new msgid was needed AT ALL.** Section B says "One msgid,
   plural-aware, replacing three composed ones." The replacing half held;
   the minting half did not. The header reuses the EXISTING plural pair
   `%(count)s performer` / `%(count)s performers`, already on the Tags page
   and already translated in both catalogues. Three composed msgids were
   retired instead of one being added: `member`/`members`,
   `from the %(names)s`, and `group tag`/`group tags`. They had to be
   deleted BY HAND from both `.po` files — pybabel only commented them out
   as `#~`. One knock-on: `msgid "performer"` was a merged entry serving
   both the Tags page's kind label and the old bare plural; losing the
   plural usage made pybabel rewrite it singular-only and stamp it
   `#, fuzzy`, which `test_i18n_catalogues.py` counts as untranslated. The
   flag was removed by hand; msgstrs unchanged. A side effect worth
   knowing: that one msgid pair now serves two semantically different
   counts (the Tags page's library total and this bill's head count), with
   no seam for a translator to tell them apart. Judged not worth a split.

3. **A member-less cluster emits no `.chiprow` at all.** Not a behaviour
   change, a spacing one, and it exists only because deviation 1 made that
   state renderable-and-tidy rather than blunt. An empty `<div
   class="chiprow">` still pays `.pclabel`'s bottom margin: measured at
   375px, 5.6px of dead space, which makes the gap after a member-less
   group 20px where every other cluster boundary is 14.4px. `.chiprow:empty`
   cannot reach it — the template's own indentation puts whitespace text
   nodes inside the div — so the row is not emitted. With it gone every
   boundary is a uniform 14.4px at 375/730/1200 in both themes.

4. **No new CSS beyond section B's two classes plus a rename.** Section B
   anticipated "any phone or tablet counterpart" going inside the existing
   media sections. None was needed: measured, the clusters wrap and stack
   correctly at 375 with nothing to override, so no new rule landed in
   either section and the top-level media-query count guard still sees 6.
   The old `.performers .chips` rule was renamed in place to `.chiprow`.

5. **`Round.label`-style locale handling was never in play here** and
   section A's `Tag` inputs are used as-is: the panel renders `loc(tag,
   "name")` at the template, not a copied string, so the `db/service.py`
   copy-site locale hazard does not apply to `PerformerCluster`. Recorded
   because the dataclass carries ORM objects rather than strings
   deliberately, and swapping them for pre-resolved names would introduce
   exactly that hazard.
