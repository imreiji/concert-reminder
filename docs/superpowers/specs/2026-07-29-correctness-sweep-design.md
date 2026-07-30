# Correctness sweep: two quiet, permanent wrongs

Date: 2026-07-29. Status: **implemented (2026-07-29)**, two tasks on branch
`correctness-sweep`, off `main`. Clears WISHLIST Proposed #5 and #7.
Deviations from this spec are recorded at the foot.

Both entries were filed by reviews of the 2026-07-28 cleanup batch and
deferred out of it — #5 on risk (it changes a signature on the app's two
most important write paths), #7 because it needs an exactly-wrong English
title to fire. Neither is new. What they share, and why they are worth a
pass of their own rather than another deferral, is that both fail
**silently and permanently**: one sends a DM that cannot be recalled, the
other mints a URL that cannot be reached, and in both cases nothing
anywhere says so.

Root causes were re-verified against the code at `18441dd` before any of
this was written; both entries described the current tree accurately.

## A. The create and import paths still announce a born-dead concert (was #5)

The cleanup batch shipped the owner ruling that a tag attached to a dead
concert — one whose every leg is cancelled — notifies nobody and applies
no preset (`handle_newly_tagged`, `db/service.py`). It holds on
`edit_concert` and on both venue rollups. It does **not** hold on the
concert-tag half of create and import, and the reason is pure ordering.

`create_concert_row` (`web/routes/concerts.py:578`) calls
`handle_newly_tagged` immediately after flushing the `Concert`, before a
single `ConcertDay` exists. The predicate reads that state as a dateless
draft and exempts it — `all_legs_cancelled` is `bool(days) and all(...)`,
so no legs means not dead, and it *has* to, or every create would silence
itself. So it notifies. The legs flush a hundred lines later, and the
venue rollup that runs there suppresses correctly.

The result: a concert created or imported with its only leg submitted
cancelled (both routes accept `day_cancelled`) DMs every franchise, group
and artist follower a 🆕 "Apply here" for a show that is off, while every
VENUE follower on the *same request* is correctly skipped. There is no
un-send and no re-announce path — the wrong DM is permanent, and the
correct one, if the leg is later un-cancelled, never arrives.

### Fix

`create_concert_row` **returns** its `newly` list instead of consuming it;
`create_concert` and `import_commit` each call `handle_newly_tagged`
themselves, after their legs flush.

The new call site is placed to mirror `edit_concert` exactly: the
concert-level tag call first, then `sync_concert_venue_tags` and its own
call, then `sync_concert`. That ordering is already documented at
`concerts.py:1469` and its reasoning is what this fix borrows — the
predicate must be asked of the legs as they stand *after* the submit, not
as they arrived.

**Two calls, not one merged call.** Merging the concert-level `newly` with
`newly_venues` into a single `handle_newly_tagged` would marginally
improve one notice (a user following both an artist and the venue would
see both tag names instead of just the artist's, since the second call
skips them for already having rules) and save a query. Rejected anyway:
the three write paths — create, import, edit — are structurally identical
here, and the editor coherence pass and the cleanup batch have both been
paid for by that parity. A divergence worth this little is worth less than
the parity it spends.

`duplicate_concert` is deliberately untouched. It does not go through
`create_concert_row` — it builds its own `Concert` — and it creates no
legs at all, so its concert is a genuine dateless draft and the exemption
is correct there. It is the one create path where notifying a legless
concert is the right answer.

### Risk

This is the reason the entry was deferred, so it is worth stating what the
change does and does not touch. The signature change is confined to two
callers (verified: nothing else in `src/` or `tests/` calls
`create_concert_row`). Atomicity is unaffected — the call simply moves
later inside the same transaction, and every path still commits once at
the end. The dateless-draft exemption inside `all_legs_cancelled` is
untouched; this fix removes the *need* for it on these two paths without
removing the exemption, which `duplicate_concert` still relies on.

## B. `generate_event_id` never checks the reserved ids (was #7)

Invariant 6 reserves `"new"` and `"import"` so a concert can never collide
with `/concerts/new` and `/concerts/import`. `validate_event_id` enforces
it — but it guards the value an editor *types*, and the app's other
producer of ids does not go through it. `generate_event_id`
(`web/routes/concerts.py:148`), used by `import_commit` and by
`POST /concerts/{event_id}/duplicate`, de-duplicates its slug against ids
already in the DB and stops there. A concert titled exactly "Import" or
"New" takes that id and is written with it.

What follows is quiet and permanent. Both routes owning those paths are
registered ahead of `/concerts/{event_id}` — deliberately, and documented
— so the concert's own page is unreachable for good while every list on
the site keeps linking to it. The edit page is no way back at first
glance either: it pre-fills the offending id, so saving anything at all
422s `event id 'import' is reserved` until the editor works out that the
field they never filled in is the problem.

### Fix

Treat a reserved id as taken in the uniqueness loop, so the suffix pass
mints `import-2`. One condition, plus a test per reserved word.

`candidate` is provably lowercase (it comes from `slugify`, which lowers),
so the membership test needs no `.lower()` — unlike `validate_event_id`,
which guards typed input and does need one. A comment records that
dependency rather than adding a redundant call.

Pre-existing rather than new: `title` could always be "Import", and the
`title_en` preference the cleanup batch shipped only widens the door (a
Japanese-titled concert previously slugged to `"concert"` and could not
reach either word).

## Tests

Route-level where the bug is route-level, function-level where the wishlist
entry already argued for it.

**A** (`tests/test_presets.py`, beside the existing dead-concert block):

1. `POST /concerts` with its only leg cancelled → no `Notification`, no
   `ReminderRule` for a notify+preset follower.
2. `POST /concerts/import/commit`, same shape, same assertions.
3. The regression halves, which matter more than the fix halves: a live
   create still notifies and still auto-applies; so does a create whose
   legs are *mixed* (one cancelled, one live — not dead).
4. `duplicate_concert` still notifies, pinning the exemption this fix
   deliberately leaves in place.

**B** (`tests/test_imports.py`, beside the existing
`generate_event_id` tests): a title slugging to exactly `import` and one to
exactly `new` each mint the `-2` suffix; a title merely *containing* the
word is unaffected.

## Deviations

None.
