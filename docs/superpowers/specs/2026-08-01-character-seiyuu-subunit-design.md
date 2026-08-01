# Character tags, their seiyuu, and subunits

**Date:** 2026-08-01
**Status:** design agreed, not implemented

## The problem

Idolm@ster events are frequently credited to the CHARACTER, not the performer.
Eventernote carries both as separate actors -- 如月千早 is `/actors/…/89214`,
her seiyuu 今井麻美 is `/actors/…/316` -- and a show billed under the character
never appears on the seiyuu's page at all. A user following 今井麻美 therefore
misses it completely, which is this app's worst failure: a deadline nobody
learns about.

The same bills also credit SUBUNITS (竜宮小町 inside 765PRO ALLSTARS), and the
catalogue has no way to say one group sits inside another.

## What is being built

1. Characters become a real tag kind, with their own Eventernote URL.
2. A character records who voices her, and attaching the character attaches the
   seiyuu -- which is what makes following the seiyuu work.
3. A group may sit inside another group, for DISPLAY.
4. Two conditional display rules, both the same rule: **draw a relationship only
   when both of its ends are attached to this concert.**

## Decisions taken, and by whom

- **Merged chip shape: the split pill** (owner, from four mockups). One chip
  visibly made of two halves, each its own link. Chosen over the inline
  `如月千早（今井麻美）` form specifically because the merge is conditional: when
  only one end is present the chip is plain, and the split shape makes that
  difference read as meaningful rather than as inconsistent styling.
- **Subunit shape: indented rail, repetition kept** (owner, from four mockups).
  The alternative -- the subunit absorbing its members so no name appears twice
  -- was rejected: it makes the parent cluster stop being a truthful lineup, and
  what it displays would depend on which OTHER tags happen to be attached, so
  two shows with identical lineups would render differently.
- **A subunit with no parent attached renders exactly like a group today**
  (owner). No rail, no indent, no "Subunit" label.
- **Pruning a character prunes its seiyuu** (owner), with the refinement below.
- **Recast history is out of scope** (owner: recasting is rare in this fandom).
  Re-point one column when it happens.

## Data model

### `TagKind.CHARACTER`

A fifth kind beside franchise / artist / venue / group.

**Verified, not assumed:** `tags.kind` is a bare `VARCHAR(9)` with **no CHECK
constraint** (read off the live schema), so adding a value needs no constraint
surgery -- which matters, because `drop_constraint` against this table is the
trap CLAUDE.md's migration section is about. `"character"` is nine characters,
the same as `"franchise"`, so it fits the existing width.

### `voiced_by_tag_id`, NOT `parent_id`

The owner initially proposed hanging the character off `parent_id`. This design
uses a separate nullable self-FK (`ON DELETE SET NULL`) instead, for two
concrete reasons:

- `parent_id` means "the broader thing I belong to", and the Tags page renders
  its hierarchy from it. A seiyuu is not broader than a character, and
  characters nested under artists would read as if 今井麻美 were a category.
- 如月千早 genuinely belongs to idolm@ster. If `parent_id` is spent on the
  seiyuu she can never say so directly -- only transitively through a group, and
  a character in no group could not at all.

Re-pointing one column is also the whole of a recast, which is why no
validity-period model is needed.

### `parent_id` extended: GROUP -> GROUP, and CHARACTER -> FRANCHISE

Both are the SAME meaning as today, not new ones. 竜宮小町 belongs to 765PRO
ALLSTARS the way 765PRO ALLSTARS belongs to idolm@ster; and 如月千早 belongs to
idolm@ster in exactly the sense the column already carries.

`POST /tags` currently enforces two things that must both widen: *parent must be
a FRANCHISE*, and *only GROUP tags take a parent*. After this it is:

| child kind | permitted parent |
|---|---|
| GROUP | FRANCHISE, or GROUP (subunit) |
| CHARACTER | FRANCHISE |
| anything else | none |

Keeping CHARACTER -> FRANCHISE available is the second half of the argument for
not spending `parent_id` on the seiyuu -- a character in no group would
otherwise have no route to her franchise at all, and Discover's franchise
filtering reads that hierarchy.

**A cycle guard is required.** GROUP -> GROUP makes loops possible for the first
time; a group must not be its own ancestor. Nothing in the codebase walks
`parent_id` transitively today, so the guard belongs at the write boundary.

### Membership stays FLAT

A subunit's members are the CHARACTER tags directly, never the parent group.
`TagMember`'s "No nested groups" rule stands. This is what keeps
`tracked_concert_ids` untouched and what makes the owner's chosen Option A
repetition fall out naturally.

| relation | column | example |
|---|---|---|
| belongs to (existing) | `parent_id` | 765PRO ALLSTARS -> idolm@ster |
| subunit of (**extended**) | `parent_id` | 竜宮小町 -> 765PRO ALLSTARS |
| belongs to, character (**extended**) | `parent_id` | 如月千早 -> idolm@ster |
| voiced by (**new**) | `voiced_by_tag_id` | 如月千早 -> 今井麻美 |

## Attach-time behaviour

This is where the feature actually works, and it reuses invariant 3's proven
seam rather than inventing a second matching rule.

**Attaching a CHARACTER also attaches its seiyuu.** Materialised onto
`concert_tags` at attach time, never re-derived. Because `tracked_concert_ids`
matches materialised rows, a user following 今井麻美 is matched by a
如月千早-credited event with **zero change to subscription logic**.

**The reverse never happens.** Attaching 今井麻美 does not pull in her
characters -- she appears as herself at events with no im@s connection, and
auto-attaching 如月千早 to her solo live would be wrong. Deliberately
asymmetric.

**Expansion chains exactly one step further than today.** A GROUP's members may
be ARTIST tags (every Love Live group today) or CHARACTER tags (an im@s group)
or a mix, and nothing needs them to be uniform. Attaching the group materialises
those members; where they are characters, stopping there would leave the seiyuu
unattached and a group-credited show would miss every seiyuu follower. So:
attach a group's members, and for any character attached -- directly or via a
group -- also attach its seiyuu. An artist member is unaffected and the existing
behaviour is byte-for-byte unchanged for every group that has no character in
it.

This is a **fixed two-step, not recursion**, and it is not the nested-groups
rule returning. `group -> character -> seiyuu` terminates by construction
because a seiyuu is an ARTIST and expansion stops at artists.

**Everything newly attached goes through `handle_newly_tagged`** (invariant 4),
so a seiyuu's followers receive the new-event notice they are owed. Same
obligation `sync_concert_venue_tags` already carries, and it must be called
only once the concert's legs are written.

## Pruning

**Pruning a character detaches its seiyuu, unless another still-attached
character shares that seiyuu.** The refinement is load-bearing: a seiyuu can
voice two characters on one bill, and detaching her because one was pruned would
silently drop the other's performer. It is derivable at prune time with no new
data.

**Known edge, accepted rather than solved:** `concert_tags` does not record WHY
a tag was attached -- group expansion has had that blind spot since it shipped.
So a seiyuu who was attached deliberately (she performs as herself AND voices a
character on the same show) is removed when the character is pruned, and the
editor re-adds her. Building provenance tracking to fix this would touch every
attach path for a rare case.

## Display

**One rule, applied twice: draw a relationship only when both ends are attached
to this concert.**

- Character + its seiyuu -> one split pill, each half its own link.
- Subunit + its parent group -> indented rail beneath the parent cluster.
- Either alone -> renders exactly as today. A lone character is a plain chip; a
  lone subunit is an ordinary top-level group cluster.

The merge is **display only**. Both tags remain independently followable, both
keep their own pages, and the underlying `concert_tags` rows are unchanged.

`performer_clusters` grows from "one cluster per attached GROUP" to "clusters
ordered parent-first, carrying a depth". It must stay SERVICE-side and must not
touch `Tag.members` -- that is a lazy self-referential m2m and reaching it
during async template rendering raises `MissingGreenlet`, a 500 this project has
shipped once. Membership is already read in one batched query over
`tag_members`; the parent lookup must join it rather than add a query per group.

Nesting follows the DIRECT parent only: if A > B > C are all attached, C nests
under B and B under A. The cycle guard bounds the depth.

Other surfaces:
- **Discover tiles**: characters follow the artist rule -- character chips
  wherever artist chips would show.
- **Tags page**: a CHARACTER section, each row naming its seiyuu.

## Discovery: nearly free

A character tag carries its own `eventernote_url`, and the daily sweep already
walks every tag that has one. 如月千早's page is swept exactly like 今井麻美's
with **no change to discovery code at all** -- the payoff for making characters
real tags rather than aliases on the artist.

Better: leads are keyed on the Eventernote event id, so a show listed on both
the character's page and the seiyuu's yields ONE lead. The existing dedup covers
it unchanged.

## Out of scope

- **Recast history.** One column, re-pointed.
- **Provenance on `concert_tags`.** See the pruning edge above.
- **Nested membership.** Subunits contain characters directly, never their
  parent group. `TagMember`'s rule stands.
- **Automatic subunit attachment.** Attaching a parent group does not pull in
  its subunits; the editor attaches what the source credited.

## Testing notes

The rules most likely to be got silently wrong, and therefore the ones that need
tests asserting the property rather than a proxy:

- Following the SEIYUU matches a character-credited concert (the whole point).
- Attaching a group materialises members AND their seiyuu -- the chained step.
- Attaching a seiyuu does NOT attach her characters.
- Pruning a character detaches the seiyuu, and does NOT when a second attached
  character shares her.
- A subunit whose parent is not attached renders as a plain top-level cluster.
- A character whose seiyuu is not attached renders as a plain chip.
- The cycle guard refuses a group that would become its own ancestor.
- `performer_clusters` issues no extra query per group (pin the count, as the
  existing membership test does).
