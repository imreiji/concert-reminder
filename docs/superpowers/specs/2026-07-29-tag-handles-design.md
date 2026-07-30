# Tag handles: a stable identity that is not the name

Date: 2026-07-29. Status: **designed, not implemented**. Sub-project **A** of
the catalogue round-trip arc (A here; B the admin export and C the tags import
follow in one joint spec, per the decomposition agreed below). Prerequisite for
C. Clears no WISHLIST entry directly -- it unblocks #1 and fixes two live
crashes found while designing it.

## How we got here

The owner asked for WISHLIST #1, the admin-only catalogue export, and
immediately named the hole in it: *"the issue is I also want a way to import
tags."*

That is the right objection. A YAML draft can **reference** tags but never
**define** them. It carries `series: {franchises, groups, artists}` plus a
per-leg `venue` -- bare names, resolved by `match_tag_ids_by_name`. Everything
that makes a tag a tag is unexpressible: `name_en`/`name_zh`, a group's
FRANCHISE `parent`, group **membership**, and a venue's
`region`/`city`(+variants)/`address`/`location_url`. Unmatched names become
per-name quick-create chips -- one tag at a time, metadata typed by hand. So an
export without a tags import is not a rebuild path; it is a pile of concerts
pointing at a taxonomy you must hand-rebuild first, in the right order, before
a single draft resolves.

Designing that importer ran straight into a question it could not answer:
**"do I already have this tag?"**

### Two live crashes (measured, not reasoned)

The app disagrees with itself about tag identity. `Tag.name` is `unique=True`
-- globally unique, case-sensitive. But `find_tag_by_name_and_kind`'s docstring
records an owner ruling that same-name-across-kinds is allowed: *"A second
`Aqours` GROUP is a real duplicate; an `Aqours` VENUE beside the `Aqours` GROUP
is allowed (resolved with the owner)."* That ruling was never implemented. Both
rules are switched on, and all three create routes (`create_tag`,
`quick_create_venue`, `quick_create_tag`) use the kind-scoped check.

Probed against a real metadata-built DB:

| Starting state | Action | Result today |
| --- | --- | --- |
| `Aqours` GROUP exists | create `Aqours` VENUE | route's 409 check passes (kind-scoped), then `UNIQUE constraint failed: tags.name` -> unhandled `IntegrityError` -> **500**, typed input lost |
| `Aqours` GROUP exists | create `aqours` VENUE | insert **succeeds** (DB UNIQUE is case-sensitive, route check is case-insensitive). From then on `find_tag_by_name` raises **`MultipleResultsFound`** -> 500 on any path resolving that name |

The second is the worse one: it leaves the database in a state where a
previously working page fails, and nothing announces it.

### The requirement that killed the obvious fix

The first design here made uniqueness kind-scoped -- a unique index on
`(lower(name), kind)` -- and it was verified end to end against legacy DDL. The
owner then supplied the requirement that invalidates it:

> "Uniqueness should only be applied within its kind, and even then we may run
> into situations like two performers having the same name."

Two ARTIST tags may legitimately share a name. A kind-scoped unique index would
have silently blocked the second Yuki Sato. Which means:

**A tag's name is not its identity, and no amount of scoping makes it one.**
Every name-match in the app is therefore a guess, and an importer cannot be
built on a guess.

Concerts already solved this: a concert is not identified by its title but by
`event_id`, an editor-chosen stable handle (invariant 6). Tags have no
equivalent. That absence is the actual gap, and this sub-project fills it.

### Owner decisions (2026-07-29)

1. **Uniqueness applies within a kind at most -- and not even reliably there**,
   because two performers may share a name. Name uniqueness is therefore
   dropped entirely rather than narrowed.
2. **Tags get a handle**, the same shape concerts have. Chosen over
   disambiguating inside the display name ("Yuki Sato (Aqours)"), which solves
   a data-modelling problem by typing around it forever, and over
   human-resolved ambiguity at import time, which would make an unattended
   dev-DB restore impossible.

## Design

### 1. `Tag.slug`

A new column: short, stable, unique across all tags, ASCII.

```
yuki-sato        yuki-sato-aqours        zepp-haneda        love-live
```

- **Stored form always satisfies** `^[a-z0-9][a-z0-9_-]{0,99}$`. ASCII by
  construction -- the handle is a machine identity; the *names* carry the
  language. A Japanese-only tag gets an ASCII handle and loses nothing, because
  nothing displays the handle except where it disambiguates (see 5).
- **Editor input is normalised, not validated.** A submitted handle goes
  through the same `slug_core` helper generation uses, so `" Yuki Sato "`
  becomes `yuki-sato` rather than being rejected on whitespace and case the
  editor cannot see. A 422 is reserved for input that normalises to `""` (all
  non-ASCII, or empty) -- the one case where there is nothing to store and
  nothing to correct on the editor's behalf.
- **Auto-generated at create** so no editor ever has to think about one.
- **Editable afterwards** on the Tags page, because a real collision wants a
  meaningful handle (`yuki-sato-voice` vs `yuki-sato-band`). Editor-owned once
  set, exactly like `event_id`.
- **`NOT NULL` and `UNIQUE`.** Every tag has exactly one, and the database says
  so rather than the application hoping so.
- **Normalised on write, so uniqueness needs no expression index.** Editor
  input passes through the same slug helper as generation, which lowercases;
  a plain `UNIQUE(slug)` is then sufficient. This deliberately avoids the
  `lower()` route: SQLite's `lower()` and `COLLATE NOCASE` are ASCII-only
  without ICU, the same class of trap CLAUDE.md documents for `trim()`/U+3000,
  and normalising at the boundary sidesteps the question instead of encoding a
  half-truth in the schema.

**NOT in any URL.** `/tags/{tag_id}` stays on the numeric id. This is an
identity, not an address. Concerts use `event_id` for both; tags need only the
former, and moving tag URLs onto slugs is a separate job with its own
link-breakage question. Nothing in this sub-project reads a slug from a path.

### 2. Generating a handle

`slugify` (`domain/yaml_export.py`) ends in `return slug or "concert"` -- a
concert-specific fallback that cannot be reused here, and whose return value
cannot be distinguished from a tag legitimately named "Concert".

Split it, without changing concert behaviour:

```python
def slug_core(text: str) -> str:      # may return ""
    ...
def slugify(title: str) -> str:       # unchanged for concerts
    return slug_core(title) or "concert"
```

A tag's handle is then the first of these that is non-empty, de-duplicated with
a numeric suffix (`-2`, `-3`) exactly as `generate_event_id` does:

1. `slug_core(name_en)`
2. `slug_core(name)`
3. `f"{kind.value}-{id}"` -- e.g. `artist-42`

Step 3 exists because `slug_core` strips everything outside `[a-z0-9]`, so a
Japanese-only name yields `""`. `artist-42` is deliberately an honest
placeholder rather than a guess: it is unique, stable, and obviously
improvable. Note it needs the row's `id`, so it is assigned after the flush.

`name_en` is preferred because the trilingual rule already makes it mandatory
at every tag create boundary (`require_variants(..., mandatory=True)` in
`create_tag`), so it is reliably there for new tags; `name` is the fallback for
the older rows that predate that rule.

### 3. Name uniqueness is removed

`Tag.name` loses `unique=True`. No replacement constraint of any kind.

Both crashes die here -- there is no longer a constraint to violate, and no
name-based lookup that can find two rows and raise.

Two functions must change, because both are single-result lookups over a column
that is no longer unique:

- **`find_tag_by_name` is DELETED.** Its only caller is `edit_tag`'s rename
  collision check (`routes/tags.py:321`), which this design removes. Leaving a
  name-only single-result lookup in place would be leaving a function that
  raises `MultipleResultsFound` by construction.
- **`find_tag_by_name_and_kind` becomes `find_tags_by_name_and_kind`**,
  returning `list[Tag]`. Its callers:
  - `create_tag`, `quick_create_venue`, `quick_create_tag`
    (`routes/tags.py:131/196/266`) -- stop blocking; see 4.
  - the rehearsal seed (`db/service.py:5837`) -- takes the first match, or
    creates. It wants "a tag called this", not "the tag called this".
  - `tests/test_rehearsal.py:731` -- same adjustment.

### 4. A duplicate name warns instead of blocking

The three create routes currently answer 409 on a same-name-same-kind
duplicate. That 409 moves to the **slug**, which is the thing that is actually
unique. A duplicate *name* is now legal and is handled client-side, before
submit, using data the page already holds:

> There's already an artist called **Yuki Sato**. Is this a different person?
> [Create anyway] [Select the existing one]

This extends a pattern rather than inventing one: `create_tag`'s docstring
already records that "same name across kinds is allowed and the dialog warns
about it client-side."

Two consequences to implement deliberately:

- **`quick_create_tag`'s 409 contract changes.** It currently returns the
  existing tag's `{message, id, name}` so the dialog can offer one-click
  select-existing. That affordance is *better* served by the pre-submit
  warning, which needs no round trip -- so it moves there, and the route's
  kind-scoped 409 goes away. Its tests change with it.
- **`edit_tag` loses its rename collision check entirely** (names may now
  collide), and gains a slug collision check answering 409. This rewrites
  `tests/test_tags.py:762`, which pins rename collision as "name-only (global
  across kinds) and unchanged" -- a deliberate reversal, not an accident.

`require_variants("Tag name", ..., mandatory=True)` is untouched: a tag name is
still mandatory in all three languages. That rule is about completeness, not
uniqueness.

### 5. Where a handle becomes visible

Pickers render a name and nothing else, so two same-kind tags sharing a name
would render two identical, unusable chips.

**Where and only where a same-kind name collision exists**, the chip shows its
handle beneath the name. No collision, no handle, no new visual noise on the
overwhelming majority of chips.

`tag_picker_context` gains a parallel `tag_slugs = {t.id: t.slug}` map rather
than restructuring the existing `tag_names` (`{id: name}`), which several
templates' inline scripts already read -- a parallel map has a far smaller
blast radius than changing a contract in place. Both go through `| tojson`,
never `| safe`, and the context value stays a raw Python object (invariant 7).

The Tags page's edit dialog shows the handle as an editable field. The create
form does not: it is auto-generated, and offering it up front invites bikeshedding
over a value that does not matter until it collides.

### 6. Migration

One revision. `tags` is one of the two legacy tables carrying **anonymous**
constraints -- CLAUDE.md names *"an unnamed `UNIQUE (name)`"* specifically --
so this is exactly the class of migration that "passes locally and dies on the
server", which this project has shipped once.

Phases, in order:

1. **Add `slug`** as nullable. It cannot be `NOT NULL` yet; the values do not
   exist.
2. **Backfill** every row by the rules in section 2, in Python (not SQL): the
   `name_en`/`name` preference, `slug_core`, the `{kind}-{id}` fallback and the
   numeric-suffix de-duplication are all Python-side logic, and SQLite's
   ASCII-only `lower()`/`trim()` cannot be trusted with the Japanese data this
   table is full of.
3. **Drop the anonymous `UNIQUE (name)`** inside
   `batch_alter_table("tags", naming_convention=NAMING_CONVENTION)` as
   `drop_constraint("uq_tags_name", type_="unique")`. **The
   `naming_convention` argument is load-bearing**: without it, reflection
   cannot name `sqlite_autoindex_tags_1` and the migration dies with
   `ValueError: No such constraint`.
4. **Make `slug` `NOT NULL` and add `UNIQUE(slug)`.**

Phases 3 and 4 are both table rebuilds. Two rebuilds of a small taxonomy table
is an acceptable price for each phase doing one comprehensible thing.

**Phase 3 was verified before this spec was written**, and it is the only
phase that needed it. A probe against legacy-shaped DDL (anonymous
`UNIQUE (name)`) confirmed that `drop_constraint` inside `batch_alter_table`
with `naming_convention` does drop `sqlite_autoindex_tags_1` rather than
raising `No such constraint`. Phases 1, 2 and 4 are ordinary work.

Two things that probe ALSO established are no longer load-bearing and should
not be cited as support: it verified a unique index on `(lower(name), kind)`,
which was the earlier kind-scoped design this spec replaces, and it verified
that such an expression index survives `Base.metadata.create_all`. Normalising
handles on write removed the need for any expression index, so `UNIQUE(slug)`
is an ordinary constraint. Recorded because a spec that quietly inherits its
predecessor's evidence is how a design ends up resting on a test of something
else.

**Downgrade** drops `slug` and restores `UNIQUE (name)`. It will fail if the
new freedom has been used, because two rows sharing a name genuinely cannot go
back. That is correct, and the revision should say so in a comment rather than
imply reversibility.

**Deploy ritual: the standard one** -- `deploy.md`'s stop -> back up -> migrate
-> start path for a rebuild. Explicitly **not** the reversed order
`ce43bfcfcae3` needed: nothing the old code SELECTs is being dropped, so the
running process stays valid until it is stopped.

### 7. Test fixture

`tests/test_migration_legacy_anonymous_constraints.py` hand-writes real server
DDL, but its `tags` table is frozen at an older column set (no `name_en`,
`city`, `address`, ...). This migration needs its own legacy-shaped DDL at the
**current** column set, still carrying the anonymous `UNIQUE (name)`. A
metadata-built fixture cannot see this class of bug at all -- that is the whole
point of that file.

## Testing

**The two crashes, as regressions** (these are the reason this ships):
- Create a VENUE named exactly like an existing GROUP -> 303, both tags exist.
  Previously a 500.
- Create a VENUE named like an existing GROUP in different case -> 303, and a
  subsequent name lookup does not raise.

**The new requirement:**
- Two ARTIST tags with the identical name coexist, with distinct handles.

**Handles:**
- Auto-generated from `name_en`; falls back to `name`; falls back to
  `{kind}-{id}` for a Japanese-only name (asserting it is NOT `concert`, which
  is what naive `slugify` reuse would produce).
- Collision appends `-2`, `-3`.
- Editing a handle to one already taken -> 409.
- A submitted handle with spaces/uppercase (`" Yuki Sato "`) is NORMALISED to
  `yuki-sato` and saved, not rejected; a handle that normalises to `""` (e.g.
  all-Japanese input) -> 422.
- `slug_core` returns `""` for a Japanese-only string while `slugify` still
  returns `"concert"` -- the split is behaviour-preserving for concerts.

**Migration:**
- Legacy-shaped fixture at current columns: revision runs, `slug` is `NOT
  NULL` and unique, the anonymous `UNIQUE (name)` is gone, every pre-existing
  row has a distinct handle.
- Every existing GET-render test still passes -- the picker change touches
  shared partials.

## Out of scope, deliberately

- **The export (B) and the tags import (C).** One joint spec, because
  designing an export whose importer comes later is precisely what produced
  this hole. Whether an import that matches a handle *updates* the existing
  tag or leaves it alone is a real decision, and it belongs there.
- **Tag URLs on slugs.** `/tags/{tag_id}` is unchanged.
- **Extracting a shared service-layer tag writer.** Three routes build
  `Tag(...)` inline in `routes/tags.py`, which violates the rule that business
  logic lives in `db/service.py`, and it wants fixing. Its second real consumer
  is the importer in C -- doing it there means one extraction with two callers
  instead of a speculative one now.
- **Unicode-aware case folding.** Handles are ASCII by construction, so the
  question does not arise for the unique column.
