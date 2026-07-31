# Tag import: fills, and conflicts you resolve

Date: 2026-07-31. Status: **implemented (2026-07-31)**, three code tasks on
branch `tag-import-conflicts`; no migration. Deviations at the foot. A follow-on to the
catalogue round-trip (`2026-07-30-catalogue-round-trip-design.md`), which
shipped and deployed the same day. Not a WISHLIST entry -- it came out of a
question the owner asked about that build.

## Why

The importer that shipped is a **restore** tool, not a **sync** tool: it matches
on handle and, if the tag exists, skips it whole. That was the right first
answer -- non-destructive, idempotent, and a stale file can never revert an edit
made since the export.

The limit surfaced immediately. All 79 artist tags in the live catalogue have an
empty `eventernote_url`, and the Eventernote-discovery entry (now WISHLIST #1)
needs them populated. Fill them anywhere other than production and there is no
way to carry them across: every one of those tags already exists by handle, so
an import skips all 79 and the field never moves.

The first proposal was a fill-blanks-only mode. The owner asked for something
better instead: **show the disagreements and let a person choose.** That
subsumes fill-blanks -- a blank on one side is not a disagreement, so it just
happens -- and it turns the importer into a real sync tool without the
stale-file danger, because nothing is overwritten that you did not look at.

## The model

Per field, four cases. Only the last one asks anything:

| DB | File | Result |
| --- | --- | --- |
| blank | value | **fill** -- applied automatically, listed so it is visible |
| value | blank | nothing |
| same | same | nothing |
| **differs** | **differs** | **conflict** -- the operator chooses |

"Blank" means NULL or a string that is empty once stripped. `Tag` has no
meaningful empty-string values -- every writer normalises `"" -> None` -- but
the differ must not depend on that holding forever.

Two fields are special:

- **`handle`** cannot conflict. It is the match key.
- **`kind`** is compared but **NOT choosable**. A handle whose kind disagrees
  means something is badly wrong -- a venue arriving as an artist -- and
  flipping it could orphan a leg whose `venue_tag_id` points at it. It surfaces
  as a loud warning and that tag is skipped ENTIRELY, fills included.

The **eleven** comparable fields: `name`, `name_en`, `name_zh`, `parent`,
`region`, `city`, `city_en`, `city_zh`, `address`, `location_url`,
`eventernote_url`. Members are handled separately below. `kind` is compared but
never offered; `handle` is the key and cannot differ.

## Membership

Members are a SET, so the diff runs in two directions, and the directions are
not equally safe.

- **Additions** -- the file lists a member the group lacks. Additive; nothing is
  lost. Shown with its checkbox PRE-TICKED, so it happens by default but can be
  declined.
- **Removals** -- the group has a member the file does not list. **The only
  destructive operation in this feature.** Shown per member, NEVER pre-ticked.

The asymmetry with field fills is deliberate rather than an oversight: a fill
writes into emptiness and cannot lose anything, so it is reported but not
declinable. An addition creates a relationship that changes what a future attach
of that group expands to, which is worth being able to say no to.

How far a removal reaches is worth stating, because it looks scarier than it is:
`tag_members` is TAXONOMY, not concert attachment. Removing a member does not
un-attach anyone from an existing concert -- invariant 3 is explicit that
membership edits never rewrite existing concerts. What changes is what a FUTURE
attach of that group expands to.

`parent` is a single value, so it is an ordinary field, not a set.

## The flow

```
paste tags.yaml
   -> POST /admin/import/tags          analyse, render the plan
   -> operator chooses
   -> POST /admin/import/tags/apply    commit
```

**Stateless.** The file rides back to the apply step in a hidden field, exactly
as the concert import preview round-trips its values. Nothing server-side to
expire, leak, or clean up.

**The apply step RE-PARSES and RE-PLANS from that file**, then applies the
choices. This matters for more than tidiness: the browser only ever sends
`mine` or `theirs` per conflict, never a value. A forged form cannot inject
data that was not in the file. It also means a conflict that vanished between
preview and commit (because the DB changed) simply is not applied.

**A missing choice means KEEP MINE.** The safe default is the one that changes
nothing, so a truncated or half-submitted form cannot overwrite anything.

Bulk "keep all mine" / "take all theirs" controls sit at the top of the
conflict list, because a long list is otherwise a lot of clicking for what is
usually one decision repeated.

## This replaces the shipped behaviour of `POST /admin/import/tags`

Today that route imports immediately and reports. It will now render the plan
instead, and the import happens at `/apply`. That is a deliberate behaviour
change to a route deployed hours earlier; its existing tests change with it, and
they say so.

Creating missing tags is unchanged and needs no choice -- a new tag has nothing
to disagree with. Those still appear in the plan as `created`.

## Architecture

- **`domain/tags_diff.py`** (new, pure) -- the differ. `plan_tag_import(incoming:
  Sequence[ParsedTag], current: Sequence[TagExport]) -> ImportPlan`. Its own
  module rather than joining `tags_yaml.py`: that one is about the FORMAT
  (serialize/parse), this one is about COMPARISON, and a module with three jobs
  is how a file starts growing unwieldy.
  Reuses `TagExport` as the current-state carrier -- it already has exactly the
  right shape (handle, name, kind, the flat fields, parent, members), so no new
  type is invented for the DB side.
- **`db/service.py`** -- `current_tag_exports(session)` builds the `TagExport`
  list (already exists inside `catalogue_export_files`; extracted so both use
  it), and `apply_tag_import(session, plan, choices)` performs the writes.
  `import_tags` is refactored into these rather than kept alongside them: two
  functions that both write tags is the thing `create_tag_row` was extracted to
  avoid.
- **`web/routes/admin.py`** -- the two POSTs.
- **`admin_import_tags.html`** -- grows the plan view. English-only and not
  wrapped in `_()`, like every admin surface; only the Preferences link is
  translated.

Choices are encoded as form fields named from the handle and field:
`conflict__<handle>__<field>` = `mine` | `theirs`, and
`member__<handle>__<member_handle>` = `add` | `remove`. Handles are
`[a-z0-9_-]` by construction, so these names are safe without escaping -- but
the values are validated against the literal set, never trusted.

## Testing

**The differ, pure and exhaustive** -- each of the four cases per field, a
kind mismatch skipping the whole tag, a new tag, an unchanged tag, member
additions, member removals, and a parent that differs.

**Apply:**
- a conflict resolved `mine` leaves the DB value; `theirs` writes the file's.
- a MISSING choice leaves the DB value (the safe default).
- fills apply without any choice being present.
- a member removal happens ONLY when explicitly chosen; the default plan applies
  none.
- a kind mismatch writes nothing at all for that tag, fills included.
- applying twice is idempotent -- the second run finds no conflicts.

**Integrity:**
- a forged `conflict__x__name=<attacker value>` cannot inject: the only accepted
  values are `mine`/`theirs`, and the data comes from the re-parsed file.
- a choice naming a handle absent from the file is ignored.

**Route:** editor 403 on both halves; the plan page lists fills and conflicts;
apply reports what it did.

## Out of scope

- **Concerts.** This is the tags importer. Concerts still go one at a time
  through their own preview, and `import_commit` stays their only write path.
- **Creating tags the file does not mention.** Nothing is deleted, ever -- a tag
  in the DB and absent from the file is untouched and unmentioned.


## Deviations (recorded during implementation)

1. **`TagImportReport` gained an `unchanged` list**, which this spec did not
   anticipate. `skipped` previously meant "existing, left alone" and now means
   "REFUSED -- the kind disagrees". Without a separate list a no-op import would
   have reported nothing at all, and a single count would have conflated
   "nothing to do" with "I would not touch this".
2. **The plan's task boundary was wrong and two tasks became one commit.**
   Deleting `import_tags` broke the route that still called it -- 43 collection
   errors from one import. A task that cannot leave the suite green is not a
   task.
3. **The kind-mismatch warning quotes the kinds** rather than giving them
   articles. The first version f-stringed one and produced "a artist"; quoting
   sidesteps articles entirely and is more precise anyway.

## A test that lied, and the shape of the lie

A new test asserted `"alert(1)"` was absent from the apply response, to prove a
forged choice value could not be echoed. It failed -- because `base.html`
contains that exact string inside its own comment explaining invariant 7. The
test was matching the codebase DESCRIBING the attack, not suffering it.

It now asserts two things instead: the raw `<script>` tag is not echoed, and the
forged choice was not WRITTEN. The second is the property that actually matters
and the first version never checked it at all.

This is the second test in two days to pass or fail for a reason unrelated to
its claim -- the others were grepping DEFLATE-compressed zip bytes for a string,
and a determinism check whose sleep was shorter than the timestamp resolution it
was testing. The pattern is the same each time: an assertion that is a PROXY for
the property rather than the property.
