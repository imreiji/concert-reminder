# Group the performer chips by group on the concert page

Date: 2026-07-27. Status: designed with the owner (two decisions recorded
below), pending implementation. Branch `performer-clusters`, off `main`.
WISHLIST Proposed #1 — the last of the owner's 2026-07-26 usage-feedback
batch still open.

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
