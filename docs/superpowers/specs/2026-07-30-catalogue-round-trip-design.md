# The catalogue round-trip: an admin export and a tags import

Date: 2026-07-30. Status: **designed, not implemented**. Sub-projects **B** (the
export) and **C** (the tags import) of the catalogue round-trip arc, in ONE spec
by deliberate decision -- see "Why these are one spec" below. Clears WISHLIST
**#1**. Depends on sub-project A, tag handles
(`2026-07-29-tag-handles-design.md`), which shipped 2026-07-30.

## Why these are one spec

The owner asked for the admin catalogue export and named the hole in the same
breath: *"the issue is I also want a way to import tags."* An export whose
importer is designed later is exactly what produced sub-project A -- the format
looked complete until something had to read it back, and then it turned out a
tag's identity did not exist. Designing both halves against each other is the
lesson, and it is why the serializer and parser for the tags vocabulary live in
one module (below) rather than mirroring each other across two.

## Owner decisions (2026-07-30)

1. **`event_id` round-trips.** A restore lands on the original URLs, and
   re-importing the same file into a populated database is a detectable
   collision rather than a silent second concert.
2. **An existing tag is SKIPPED, not updated.** Non-destructive and idempotent;
   an import can never revert an edit made since the export.
3. **Tags only in bulk.** Concerts keep going through the existing
   paste-a-draft preview, one at a time, with a human confirming. Bulk concert
   restore is not built and not logged as wanted.
4. **A parallel `handles` block, authoritative where present** -- rather than
   making every tag list a string-or-mapping union, or keeping two separate
   formats.

## B. The export

`GET /admin/export.zip`, `require_admin`, built at request time. Precedent for
streaming a zip inline is `GET /concerts/import/skill.zip`, whose comment
records the reasoning: a committed binary goes stale, and a few hundred KB of
YAML is not worth a thread hop.

```
tags.yaml                            every tag, with handles
concerts/hasunosora-6th-live.yaml    one draft per concert, named by event_id
concerts/...
RESTORE.txt                          the order to put it back in
```

**No user data, by construction rather than by filter.** The queries touch
`concerts`, `concert_days`, `rounds`, `round_qualifiers`, `tags` and
`tag_members` only -- never a JOIN to a user table -- and `created_by` is not
emitted. Nothing to leak beats a filter to get wrong. `users`, `web_sessions`,
`round_outcomes`, `concert_subscriptions`, `leg_opt_outs`, `reminder_rules`,
`reminder_queue`, `notifications` and `delivery_log` are all personal and none
is touched.

`RESTORE.txt` earns its place because a restore happens under stress, months
later, and the order is load-bearing: tags first, or every concert's tag
references dangle. It states that, names the two routes, and says that
re-importing a concert that still exists answers 409 on purpose.

Eager-load days, rounds, qualifiers and each leg's `venue_tag`. `ConcertDay.
venue_tag` is `lazy="raise"`, so a missed `selectinload` is a `MissingGreenlet`
500 rather than a slow export.

### `tags.yaml`

```yaml
tags:
  - handle: love-live
    name: ラブライブ！
    name_en: Love Live!
    kind: franchise
  - handle: hasunosora
    name: 蓮ノ空女学院スクールアイドルクラブ
    name_en: Hasunosora School Idol Club
    kind: group
    parent: love-live                          # a HANDLE
    members: [kozue-otomune, kaho-hinoshita]   # HANDLES
  - handle: k-arena-yokohama
    name: Kアリーナ横浜
    kind: venue
    region: Kanto
    city: 横浜
    city_en: Yokohama
    address: 神奈川県横浜市…
    location_url: https://maps.example/…
  - handle: kozue-otomune
    name: 乙宗梢
    kind: artist
    eventernote_url: https://www.eventernote.com/actors/…
```

Every `Tag` column except `id`, `created_by` and `created_at`. Empty fields are
omitted so the file stays readable. `parent` and `members` are handles, never
names -- the whole point of sub-project A, and what forces the importer into two
passes.

Tags are emitted in a deterministic order (kind, then handle) so two exports of
an unchanged catalogue are byte-identical. That is what makes the file diffable,
which is most of its value as a backup.

**The zip container needs pinning for that to hold.** `ZipFile.writestr` stamps
each entry with the current time when handed a bare filename, and zip timestamps
have two-second resolution -- so two exports seconds apart differ in bytes while
every file inside is identical. Measured, and worth recording because a
one-second test sleep lands in the same bucket and "proves" determinism that is
not there. Every entry is therefore written through an explicit
`ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))`, the conventional
reproducible-build epoch, which makes the whole archive byte-stable.

## The concert draft gains three optional keys

Optional so every agent-authored draft, and the skill example pinned to the
parser by test, keep working untouched.

```yaml
event_id: hasunosora-6th-live       # NEW -- preserves the URL
title: 蓮ノ空 6thライブ
series:
  artists: [乙宗梢, 佐藤有紀]         # unchanged: readable, what an agent writes
series_handles:                      # NEW -- exports add it, agents omit it
  franchises: [love-live]
  groups: [hasunosora]
  artists: [kozue-otomune, yuki-sato-liella]
performances:
  - label: Day 1
    venue: Kアリーナ横浜              # unchanged
    venue_handle: k-arena-yokohama   # NEW
```

**`event_id` goes through `validate_event_id`** -- the same check the edit page
uses, so format, reserved words (invariant 6) and uniqueness are enforced
identically rather than re-implemented. Absent, `generate_event_id` runs exactly
as today. Present and already taken, the answer is **409**, which is how
re-importing a file into a populated database announces itself instead of
quietly creating a duplicate.

**Handles win over names where present**, and the name is decoration. The two
CAN disagree -- an export taken before a rename -- so the rule must be stated
rather than left to the implementation. A disagreement is worth a warning in the
preview; silence is not.

**This is the fidelity fix.** Without `series_handles`, a concert with two
performers both written 佐藤有紀 re-imports through `match_tag_ids_by_name`,
which is documented first-tag-wins, and attaches to whichever the query listed
first. It gets an explicit test, because it is the failure the whole arc exists
to prevent.

**One cleanup.** The draft emits a `slug` key that the parser accepts and
ignores; it is `slugify(title)` and unrelated to `event_id`. Exports emit
`event_id` and stop emitting `slug`; the parser keeps tolerating `slug` so older
files still parse. Two near-identical keys with different meanings is not
something to leave in a restore file.

**What deliberately does not change:** `import_commit` remains the only concert
write path, the preview still renders for a human to confirm, and group tags
still attach with `expand=False` -- so a concert's pruned artist list round-trips
exactly rather than being re-expanded to the group's current membership
(invariant 3).

## C. The tags import

`POST /admin/import/tags`, admin-gated to match the export, taking pasted YAML
in a textarea, reusing `imports.MAX_DRAFT_CHARS` (200k) rather than inventing a
second cap. Paste
rather than upload for consistency with that route and to avoid new multipart
plumbing; an upload is a small addition later if pasting a restore file becomes
annoying.

**No preview, deliberately.** The concert path needs one because its commit
writes rich, ambiguous data a human should eyeball. Here the only possible
outcome is "tags that did not exist now do", so a **result report** afterwards
carries the same information for a fraction of the build: created N, skipped M,
and the warnings.

**Two passes, because `parent` and `members` are handles:**

1. Create every tag whose handle is absent from the DB. An existing handle is
   skipped ENTIRELY -- no field updates -- which keeps decision 2 pure.
2. Wire `parent` and `members`, resolving handles to ids, for tags **created in
   this run only**. Re-wiring an existing tag's membership would be an update
   under another name.

**Warnings over failures**, following `parse_draft`'s philosophy. Each of these
skips one thing and lets the rest of the import proceed:

| Condition | Outcome |
| --- | --- |
| no `handle` | skipped -- it cannot be identified |
| no `name` | skipped -- a tag cannot render without one |
| unknown `kind` | skipped |
| duplicate handle within the file | first wins, warning |
| handle that normalises differently (`slug_core`) | normalised, warning |
| `parent` absent from file and DB | created without a parent |
| `parent` that is not a FRANCHISE | created without a parent |
| `member` absent from file and DB | that membership dropped |
| a GROUP listed as a member | dropped -- groups do not nest |

Only "not parseable YAML", "not a mapping" or "no `tags:` key" stops the import.
One transaction, committed at the end, so a rejected file leaves nothing behind.

**Two invariants it must visibly respect.** Creating tags fires **no
notification** -- creation is not attachment (invariant 4), the same reason
`quick_create_tag` is silent. And importing memberships **touches no concert**:
expansion is an attach-time act (invariant 3), so a restored membership list
never rewrites an existing concert's performers.

## The module layout

- **`domain/tags_yaml.py`** (new, pure) -- `tags_to_yaml(tags)` AND
  `parse_tags(text)`, in ONE module. Splitting them is how the original hole
  opened; a format stays coherent only if both halves are edited together.
  `yaml.safe_load` only, and the same `_text()` guard `yaml_import.py` uses
  against list/dict values and alias bombs.
- **`domain/yaml_export.py`** -- gains `event_id`, `series_handles` and per-leg
  `venue_handle`; stops emitting `slug`.
- **`domain/yaml_import.py`** -- learns the same three keys, handle-wins, and
  keeps tolerating `slug`.
- **`db/service.py`** -- `catalogue_export_data(session)` gathers the rows;
  `import_tags(session, parsed)` does the two passes and returns the report.
  Business logic, so not in a route.
- **`web/routes/admin.py`** -- both routes. It already serves the admin surfaces
  production needs, and neither of these is flag-gated the way
  `routes/rehearsal.py` is.

**The shared tag writer finally lands here.** Three routes build `Tag(...)`
inline, which the tag-handles spec deliberately left alone because a speculative
extraction with one caller is not worth it. The importer is the second real
caller, and unlike the routes it supplies an EXPLICIT handle from the file
rather than generating one -- so it bypasses `assign_tag_slug` on purpose, and
expressing that distinction is the extracted writer's job.

## Testing

**The centrepiece is a true round-trip**: seed a catalogue, export it, drop
every tag, import `tags.yaml`, then assert each tag matches field-for-field
except `id`/`created_by`/`created_at` -- parents and memberships included. A
test that only checks "some tags exist" would pass on a badly lossy export.

- **Idempotence** -- import twice; the second run creates nothing, skips
  everything, and warns about nothing.
- **The ambiguity case** -- a concert with two performers both written 佐藤有紀
  re-imports attached to the RIGHT one via `series_handles`. The reason the arc
  exists.
- **Handles beat names** -- a draft whose `series_handles` and `series` disagree
  binds by handle and warns.
- **`event_id`** -- preserved on restore into an empty DB; **409** on re-import
  into a populated one; still auto-generated when the key is absent.
- **No user data** -- EXTRACT every entry and assert none contains a
  `created_by` key or a seeded username. Searching the raw zip bytes would pass
  vacuously: the entries are DEFLATE-compressed, so a literal string is not
  there to find even when it is in the data.
- **Determinism** -- two exports of an unchanged catalogue are byte-identical,
  with the two builds far enough apart to cross a two-second zip timestamp
  bucket (or the fixed `ZipInfo` epoch is untested and the assertion passes on
  luck).
- **Gates** -- an editor gets 403 on both routes, an admin gets through.
- **Every warning row above** gets a case; each asserts the rest of the import
  still landed.
- **Invariants** -- importing a membership leaves an existing concert's
  `concert_tags` untouched, and queues no `Notification`.

## Out of scope, deliberately

- **Bulk concert restore.** Decision 3. It would need the write path extracted
  out of the Form-based `import_commit` and would bypass the preview a human
  currently confirms -- the app's main guard against a bad import.
- **Updating existing tags from a file.** Decision 2. The import is additive.
- **Exporting or importing anything personal.** Not a filter to maintain; the
  queries simply never reach those tables.
- **A restore that reproduces `id` values.** FKs are internal; handles and
  `event_id` are the identities that matter, and forcing ids would fight the
  autoincrement for no gain.
