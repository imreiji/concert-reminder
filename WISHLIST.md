# WISHLIST.md

Every potential feature raised in roadmap and UX discussions, ordered by
user impact (highest first). Maintained per CLAUDE.md's "Feature wishlist,
and the two files a shipped feature updates": fully re-evaluated and
re-ranked every time a feature ships, in the PR that ships it. `README.md`
is the other of the two -- one user-facing line per shipped feature -- and
it is the one that rots silently, so update it in the same breath as this
file. Each entry notes impact and effort so re-ranking has a basis. Shipped
and rejected ideas move to the bottom sections instead of being deleted.

Two 2026-07-21 builds landed without having been Proposed entries here --
the mobile parity retrofit (bottom tab bar, FAB, swipeable board, filter
sheet, bottom-sheet dialogs) and the signed-out redirect with its
return-to-page login -- so neither moved up from Proposed. Both are logged
in Shipped anyway, and the revision passes below reconsider every entry
they touch. The same is true of the 2026-07-22 venue-to-tags build (phase 1):
not a Proposed entry, logged in Shipped, and the entries it changed are
re-reviewed in place -- it also ADDED three entries below, which is the more
important half of its revision pass.

The 2026-07-22 trilingual-concert-page arc then landed the whole run of entries
that phase-1 pass had added, plus the phases between them: the import-preview
venue picker (old #1), leg/round label localization (phase 2), the round-label
phrase library (phase 3, old #4), all-three-languages-or-none enforcement
(phase 4), and dropping the legacy free-text venue columns (phase 5, which
retired the discover-guard bug of old #13 for free). All are logged in Shipped
below, and the removals triggered the full re-rank this section now reflects:
four entries left Proposed, and #14 (the RoundKind observation) rose above the
plumbing entries as the highest-impact thing still standing after the
user-facing arc shipped.

The 2026-07-23 agent-driven import build (a paste-a-YAML-draft seam feeding the
existing import preview, the YAML export made two-way, the add-concert skill) is
the latest, and like the builds above it was not a Proposed entry, so nothing
moved up FROM Proposed. It is logged in Shipped, and its revision pass ADDED two
entries -- Eventernote actor-page discovery (#2 at the time, cheap now that the
skill and draft seam exist) and in-app LLM extraction behind the same seam (#9
at the time, deferred on budget). The full re-rank it triggered found every
existing entry unchanged in substance: the shipped seam neither obsoletes nor
reorders any of them, so the former #2-#11 were pushed down only by the two
insertions (never on merit). Minute-level offsets was re-reviewed explicitly
and untouched by this build. The one live cross-reference the renumber would
have invalidated -- the sign-in-bounce entry's pointer at the demo-parity batch
and the Discover head -- was corrected in place.

The second 2026-07-24 evening pass ships the tablet band the same day it
became #1: measured against the real app (seeded dev DB + iframe harness,
per the measure-don't-reason rule), spec'd and demo'd (PR #94), then
implemented (PR #95) -- one bounded 701-1040px section, compact header,
swipeable board, filter-sheet takeover with the .fsheet/.layout coupling
moved to 1040/1041 and the bottom-sheet overlay re-anchored to the phone
boundary. A production visual bug found during measurement shipped
separately (PR #93, the global [hidden] override). Entries below
renumbered 1-14; the sign-in-bounce cross-reference bumped again.

The third 2026-07-24 pass ships inline tag creation (PR #96) hours after
the tablet band: per-name quick-create chips in the import preview, a
kind-aware popup (kind pre-selected from the draft bucket, parent
franchise for groups), the picker join, and the 409 select-existing
courtesy. Live click-through verified against a seeded dev server before
the PR. Entries below renumbered 1-13; event_id slugs rises to #1 by
removal.

The 2026-07-23 evening revision pass is the largest re-rank since the split:
the owner set next-day priorities, adding four entries that now lead the list
(#1 i18n calibration -- deliberately first, since corrected wording feeds the
two design brainstorms; #2 the editor-pages coherence pass, which ABSORBS the
former "Editor page parity with the demo" entry; #3 a real tablet layout for
701-1040px; #4 inline tag creation for unmatched import tags), plus two
assistant-raised entries (#5 event_id slugs from title_en, #6 the
agent-import review-debt batch). Every pre-existing entry was pushed down by
insertion, not demoted on merit. The same pass also caught a bookkeeping
debt: the cache-bust entry had shipped on 2026-07-22 via PR #84
(`static_url` + per-file content hash) but was never moved -- it is in
Shipped now, a day late.

The 2026-07-24 pass ships the first entry of that batch: the calibration
review came back (762 rows, a full pass plus a later zh-only overlay) and
PR #88 applied 307 ja / 344 zh corrections, so the entry moved to Shipped
and the two design brainstorms it fed rise to #1/#2. The same review
proposed 132 ENGLISH source fixes -- msgid changes, i.e. source-code
edits, not catalogue edits -- which were extracted verbatim to
`docs/i18n-english-source-fixes-2026-07-24.csv` and inserted as the new
#3 rather than applied blind, so every entry from #4 down keeps its
number. Two stale rank cross-references in older entries were made
name-based in the same pass.

Later the same day the rest of that arc shipped: PR #89 (a ja round-2
agent proofread -- 8 correctness fixes, logged inside the calibration
Shipped entry) and then the English-source fixes themselves as PR #90,
so the "new #3" lasted hours: all 132 applied at the source layer,
msgids re-keyed in both catalogues with reviewed msgstrs preserved, and
the touched "(s)" strings converted to real plurals per that entry's
rider. One review row was REJECTED: the visible add-concert ->
add-event rename was conditional on renaming the shipped skill itself,
which keeps its name. Entries below renumbered 4-16; the
sign-in-bounce entry's demo-parity/Discover-head pointer was bumped in
place.

The 2026-07-24 evening pass ships #1 itself: the editor coherence pass went
brainstorm -> spec -> reconciled demos (PR #91) -> implementation (PR #92)
in one day, including its sentence-builder added scope and two riders the
build surfaced (translated offset labels, a standing placeholder-integrity
hygiene test). The tablet band rises to #1 by pure removal; every entry
shifts up one, and the sign-in-bounce cross-reference was bumped in place
once more.

The 2026-07-26 owner usage-feedback batch is the first sourced from living
with the app day to day rather than from a build review: six pain points,
landing as five entries that now lead the list (#1 per-leg outcome truth --
two of the six complaints share that root -- #2 the "Coming up" row budget,
which ABSORBS the former collapse-per-anchor entry and adds the
many-rounds-per-concert dimension, #3 board-card ladder collapse, #4
grouping performer chips, #5 the admin catalogue export). Every
pre-existing entry was pushed down by insertion, not demoted on merit;
entries renumbered 6-17. Design questions the batch left open (per-day win
capture, the ladder's collapsed shape, multi-group artists, export
re-importability) are recorded inside the entries pending owner answers.

The 2026-07-27 pass ships #1 of that batch the day after it was filed:
per-leg outcome truth, nine tasks and two review waves, answering the open
design question in its favour (per-day capture, progressive on both
surfaces). Entries renumbered 1-16. The re-rank found one thing worth
saying and nothing worth moving: de-crowding "Coming up" (now #1 by
removal) got CHEAPER, since the covered-round suppression that just
shipped already deletes some of the rows that entry was going to collapse
-- but cheaper is not higher-impact, its impact reading is unchanged, and
it already sat directly behind the entry that shipped, so it rises by
removal alone. Everything below it was pushed up one by the same removal,
never demoted or promoted on merit.

The second 2026-07-27 pass ships the new #1 the same day it inherited the
spot: de-crowding "Coming up", four tasks, spec and plan first. Home's
deadline list is per-concert blocks now -- one lead row chosen by the same
`_wants_you` rule the concert page's "Next for you" uses, the rest behind a
"+N more rounds" fold, and 6 of 10 concerts visible with a page-level
"+N more events" fold. Two owner rulings closed it out (the fold says
"events", and member rows keep a hairline between them), and the
measurement pass found a pre-existing tablet-band bug worth fixing on the
spot. Entries renumbered 1-15. The re-rank moves nothing on merit: board-card
ladder collapse rises to #1 by removal and is cheaper for it -- the fold
vocabulary now exists and is proven, and that entry can reuse it -- but its
open question was never the mechanism, so its impact reading is unchanged.

The third 2026-07-27 pass ships that #1 the day after it inherited the spot,
and the owner answered its open UX question the way the entry could not:
the board CAPS its ladder at two rungs plus an inert count line and never
expands, because uniform card height is the whole point of a board. The same
branch carried a second, unlisted piece -- the concert page's per-leg fold,
raised by the owner on 2026-07-26 after per-leg outcomes turned a 3-leg
x 6-round concert into eighteen rows -- so it is logged in Shipped as its
own entry rather than backfilled into Proposed, exactly as the mobile
retrofit and the signed-out redirect were. The arc also paid off the query
debt the spec had been carrying since the "Coming up" build amplified it
(Home 42 statements -> 19); no Proposed entry ever tracked that, so it is
recorded inside the board-ladder Shipped entry rather than moved from
anywhere. Entries renumbered 1-14; performer-chip grouping rises to #1 by
pure removal, unchanged in substance, and the sign-in-bounce entry's
demo-parity/Discover-head pointer was bumped in place once more.

The ladder-declutter branch's own final review (2026-07-27) then ADDED two
entries without shipping anything, which is the rarer kind of pass: both are
pre-existing defects that build surfaced rather than caused, so neither is a
regression to fix on the branch. The cancelled-leg-only concert enters at #3 --
above the event_id slugs it displaces -- because it is wrong in a way the user
can act on (an irreversible APPLIED press on a concert that is not happening)
while a meaningless slug is only ugly, and it is the more visible of the two
now that the per-leg fold empties such a leg's body. The opt-out fold reset
enters at #5, below the slugs, as a place-losing annoyance in one flow. Every
entry from the old #3 down was pushed down by insertion, never demoted on
merit; entries renumbered 1-16 and the sign-in-bounce entry's
demo-parity/Discover-head pointer was bumped in place once more. The branch's
third defect -- a declined round taking a board card's "what's next" slot --
was FIXED in the same review wave rather than filed, so it is deliberately
absent from Proposed and recorded inside the board-ladder Shipped entry.

The fourth 2026-07-27 pass ships #1 the day after it inherited the spot, and
with it the LAST of the owner's 2026-07-26 usage-feedback batch except the
admin catalogue export: five entries filed on 2026-07-26, four shipped
inside two days. Performer chips are per-group clusters now, and the entry's
three open decisions were all answered by the owner rather than by the
build -- an artist in two attached groups appears under BOTH (the repetition
is information), big clusters do NOT fold (the panel is reference, not a
to-do), and clustering keys off tags actually attached, which was already
the entry's own leaning. A fourth question the entry could not have
anticipated came up mid-build and the owner ruled on that too: a group
attached with none of its members shows its label row and NO count at all,
rather than "0 performers". Entries renumbered 1-15. The re-rank moves
nothing on merit and, unusually, found nothing made cheaper either: the
admin export rises to #1 by pure removal and is the same size it was, since
it touches the catalogue tables and a zip route, nothing this build went
near. Worth recording for whoever takes it: it is now the only entry left
that the owner personally asked for, so the list is back to
assistant-raised and review-raised items behind it. The sign-in-bounce
entry's demo-parity/Discover-head pointer was bumped in place once more.

The fifth 2026-07-27 pass ships #2 rather than #1 -- the only entry the owner
personally asked for sits at the top and is not what came next, which is worth
saying out loud so the ordering does not read as broken. #2 (a fully cancelled
concert still asking you to act) was picked because it is a correctness bug
with an irreversible press behind it and the admin export is a new feature; the
export keeps #1 and is unchanged in size. The entry's one open question was put
to the owner and answered: a dead concert keeps its board card ONLY when the
reader has standing on it, badged and never in *Open now*, and leaves the board
entirely otherwise. It named three surfaces; the build touched nine, six of
them found by review, and one of those (`/setup`) turned out to be genuinely
broken rather than merely stale -- offering an APPLIED, which cannot be taken
back, on a concert that is not happening. The planner was the one addition made
deliberately at spec time, on the reasoning that a DM saying "apply now" about
a dead show is the worst instance of the lie the entry describes. Entries
renumbered 1-14. Nothing re-ranked on merit and nothing got cheaper: this build
lived in the planner, the board and the capture surfaces, and the remaining
entries go nowhere near them. Two were re-read against what shipped and both
stand unchanged -- #5 (opt-out snapping the folds shut, #3 at the time) is a
sibling defect on the same page but purely an htmx fold-state problem, and #8
(minute-level offsets) is if anything slightly less pressing, since a dead
concert now plans no reminders at any offset at all. The sign-in-bounce
entry's demo-parity/Discover-head pointer was bumped in place once more.

The dead-concerts branch's own final review then FILED three entries rather
than shipping a sixth pass: two of them (#3, the unfollow dialog's two live
branches claiming an opt-out deletes a won mark, and #4, `handle_newly_tagged`
still DMing a fully-cancelled concert as a new event) are the same
"stop-claiming-what-is-not-true" family the branch just shipped, found while
fixing its own new copy; #6 is an owner-eyeball question about how a dead board
card marks its rungs. The three Important findings from that review were fixed
on the branch itself and are recorded in its Shipped entry. Nothing already on
the list re-ranked on merit -- the insertions push the former #3-#14 down to
#5-#17 by position only -- and the sign-in-bounce entry's demo-parity/
Discover-head pointer was bumped again. Worth noting for the ordering: #3 and
#4 both sit above the entries they displace on truthfulness grounds (a false
sentence at a decision point, and a DM about a show that is not happening) but
below #2, which affects every Japanese-titled import rather than a narrow
state.

The 2026-07-28 cleanup batch is the biggest single clearing of this list so
far and the first pass whose whole purpose was to clear it: FIVE Proposed
entries (#2 slugs, #3 the unfollow dialog, #4 the dead-concert tag DM, #5
the fold reset, #7 the importer-debt batch) shipped on one branch in four
tasks, and a sixth (#6, per-rung marking on a dead board card) was CLOSED
BY DECISION rather than built -- it was filed as an owner eyeball and the
eyeball said leave it, so it moves to Rejected with the reason. None of the
five was ranked highly enough to lead the list on its own; they were batched
precisely because each is small and none was ever going to win a
prioritisation on merit, which is the argument for doing that kind of work
in one pass or not at all. They are logged as ONE Shipped entry for the
reason given there.

The re-rank that follows moves nothing on merit. #1, the admin catalogue
export -- still the only entry the owner personally asked for -- is
untouched and stays #1; everything below it rises by pure removal and is
renumbered 1-13. Nothing got cheaper either: this batch lived in slug
generation, one dialog's copy, the tag pipeline and a client-side fold
listener, and no remaining entry goes near any of them. Two were re-read
against what shipped and both stand: #2 (minute-level offsets) is unchanged,
and #6 (the cosmetic `RoundKind` members) is if anything slightly better
supported, since §C's work threaded another consumer through
`all_legs_cancelled` without a single kind-specific branch. The batch also
ADDED two entries, both found by its own reviews and both inserted on impact
rather than appended: #5, the create and import paths still announcing a
born-dead concert (the structural twin of the `edit_concert` defect §C
fixed, found by the final review and RECORDED rather than fixed -- see the
entry, and deviation 6 of the spec, for why a signature change on the two
main write paths did not belong at the end of a cleanup branch), and #7,
`generate_event_id` never checking the reserved ids (found by Task 1's
review while shipping the slug preference directly above it). Between them
they push the former #5-#11 down by position only, never on merit. One
correction of the record belongs here rather
than in the Shipped entry: item (d) of the old #7, the `preferences.html`
backslash action, did not exist. It was struck, not fixed -- see below. The
sign-in-bounce entry's demo-parity/Discover-head pointer was bumped in place
once more.

The second 2026-07-28 pass ships the delivery feed, and it is the first build
in a while that came from a direct owner ask rather than from this list: the
owner asked for three things in one session -- a rehearsal harness (A), an
admin feed of what the scheduler actually delivered (B), and a targeted
broadcast so an incident can be remedied (C) -- and sequenced B and C ahead of
A, on the reasoning that an undetected bad delivery on production costs more
than a missing test harness. B is what shipped, so nothing moved up FROM
Proposed; it is logged in Shipped like the mobile retrofit and the signed-out
redirect before it. Its revision pass ADDED the other two as entries, which is
the more important half: **C enters at #1** and **A at #3**. C leads because an
incident you can see but cannot fix is half an answer, and B just built the
thing that makes C addressable (`delivery_log` knows who received what). A sits
at #3 on a borrowed argument rather than its own user impact, which is nil: it
is the rehearsal path for #1, and #1 is a mass-DM route. A's spec and the
owner's sequencing genuinely disagree about which of the two comes first, and
that disagreement is RECORDED on both entries rather than settled by
renumbering -- it is an owner call.

The rest of the re-rank moves nothing on merit. The admin catalogue export --
still the only pre-existing entry the owner personally asked for -- is pushed
from #1 to #2 by insertion alone, and everything below it slides by two;
entries renumbered 1-15. One entry did get MORE valuable without moving: A's
spec found a second use for that export, since a catalogue-only copy is the
clean way to seed a local dev DB (it contains no personal data by
construction, unlike the wholesale production copy the spec talks whoever
comes next out of). That is developer value, and this list ranks by user
impact, so the note is recorded on the entry and the rank is left alone --
though it does settle the entry's one open question in favour of the
re-importable YAML shape, because a seed you cannot load back is not a seed.
Nothing else got cheaper: this build lived in the scheduler tick, a new table
and an admin page, and no remaining entry goes near any of them. Two were
re-read against what shipped and both stand -- minute-level offsets (#4) is
untouched, and the born-dead-concert announcement (#7) is unaffected, though
worth noting that `delivery_log` is now the thing that would let someone SEE
that defect fire in production rather than infer it. Two cross-references were
bumped in place: the born-dead entry's "below #4" pointer at the round-label
suggestions, and the sign-in-bounce entry's demo-parity/Discover-head pointer,
for the umpteenth time.

The third 2026-07-28 pass ships #1 the same day the pass above filed it, which
is the shortest gap on this list: the targeted admin broadcast, sub-project C,
went from a Proposed entry with no spec to seven shipped tasks between one
revision pass and the next. It closes the arc B opened -- an incident an admin
can SEE is now an incident an admin can ANSWER -- and it leaves the rehearsal
harness (A) as the only unbuilt piece of the three the owner asked for in one
session.

The re-rank moves nothing on merit; entries are renumbered 1-14 by pure
removal. One entry changed without moving, and it is the note worth carrying
forward: **A's argument stopped being anticipatory.** It sat at #3 on a
borrowed claim -- it is the rehearsal path for #1, and #1 is a mass-DM route --
and #1 has now shipped, so the disagreement recorded on both entries (A's spec
wanting the harness first; the owner sequencing B and C ahead of it) is settled
in fact rather than by decision. The mass-DM route is live and has never been
exercised outside the test suite, which is the strongest case this list has
ever carried for promoting a developer-facing entry over a user-facing one. It
is deliberately not acted on: the list orders by user impact and A's is still
nil, and the owner already made this call once. A rises to #2 by removal, with
its closing paragraph rewritten to describe the state instead of the plan.
Nothing else got cheaper -- this build lived in the notifications outbox, a new
audit table and an admin page, and no remaining entry goes near any of them.
The admin catalogue export keeps #1 and is unchanged in size, still the only
entry the owner personally asked for. Two were re-read against what shipped and
both stand: the born-dead-concert announcement (#6) is untouched, since a
broadcast is a way to apologise for a wrong DM and not a way to prevent one,
and minute-level offsets (#3) goes nowhere near any of this. Two
cross-references were bumped in place -- the born-dead entry's round-label
pointer and the sign-in-bounce entry's demo-parity/Discover-head pointer, for
the umpteenth time -- and, unusually, one line in Shipped was CORRECTED rather
than bumped: B's account of `UNREPORTED_NOTE_KINDS` listing `admin_broadcast`
up front described a decision C then reversed, and leaving it would have made
this file contradict the code.

The fourth 2026-07-28 pass ships the last of the three sub-projects the owner
asked for in one session: **A**, the local rehearsal harness, filed at #2 and
built the day after C. It is the only one of the three with no user-facing case
at all, and the arc it closes began as "let me test the whole flow on prod" --
B answered detection, C answered remedy, and A is the piece that means neither
of them has to be rehearsed on live users. Its own spec had already talked
itself out of the original premise: the first draft designed a
`Concert.rehearsal` column, three global query filters and a rehearsal-only tag
convention, an apparatus whose whole purpose was to make a fake concert
harmless inside a shared production catalogue, and the rewrite deleted every
one of them for a single config flag once it noticed a second Discord
application closes the gap for free.

The re-rank moves nothing on merit, and entries are renumbered 1-12 by TWO
removals rather than one. The second is a bookkeeping debt this pass found
rather than a feature that shipped: the Python-pinning entry has been done
since 2026-07-22. `.python-version` is in the repo at 3.14, `uv sync` honours
it in dev, in CI and on the server, and a Shipped entry two days younger than
the commit even annotates the pinning entry with a reproduction -- so it sat in
Proposed for six days describing a gap that was already closed. It is in
Shipped now, dated to the commit rather than to today. The cache-bust entry set
the precedent by being a day late; this one is worse, and it is worth saying
plainly, because an entry describing a solved problem is the one kind of wrong
this file can be that costs somebody real work.

One entry gained without moving. The admin catalogue export -- #1, still the
only entry the owner personally asked for -- now has a live cross-reference
from `docs/local-dev-bot.md`, whose "do not copy the production database"
section points at it as the clean alternative. That is the second use the
delivery-feed pass recorded for it, now written where the operator will
actually meet it. Its rank is unchanged for the reason given last time: this
list orders by user impact, and a dev-seeding path is developer value. Nothing
else got cheaper -- this build lived in a gated router, one service section and
a docs file, and no remaining entry goes near any of them. Two were re-read
against what shipped and both stand: minute-level offsets (#2) is untouched,
and the born-dead-concert announcement (#5) is unchanged in substance, though
the harness is now where someone would reproduce it before fixing it (seed,
cancel the only leg, watch the DM go out anyway). Two cross-references were
bumped in place -- the sign-in-bounce entry's demo-parity/Discover-head
pointer, for the umpteenth time -- and the export entry's "(#2)" pointer at the
harness spec was made name-based, since what it pointed at is no longer a
number.

The 2026-07-29 pass ships the two entries the 2026-07-28 batch's reviews
filed and then deferred: the born-dead-concert announcement (#5) and the
reserved-id slug (#7). They were built as one sweep because they share the
property that ranked both of them above tidier work -- each fails silently
and PERMANENTLY. One sends a 🆕 "Apply here" DM for a show that is off, with
no un-send and no re-announce; the other mints a URL that no route can ever
serve while every list on the site keeps linking to it. Neither leaves a
trace anywhere for anybody to notice. Root causes were re-verified against
the tree before a line was written, and both entries described it accurately
-- worth recording, given this file's Python-pinning embarrassment five
entries up.

The re-rank moves nothing on merit and nothing got cheaper: the sweep lived
in two route functions and touched no model, no template and no catalogue.
Entries renumbered 1-10 by the two removals; the admin catalogue export keeps
#1 for the third pass running, still the only entry the owner personally
asked for. Two were re-read against what shipped and both stand. The
`RoundKind` observation (now #5) rises two places by pure removal and is
newly the top code-health entry, which is worth watching rather than acting
on: its whole argument for staying logged is that churning a persisted enum
buys nothing visible, and rising by removal is not new evidence. Minute-level
offsets (#2) is untouched. One cross-reference was made name-based instead of
bumped -- the sign-in-bounce entry's demo-parity/Discover-head pointer, which
five consecutive passes have now renumbered, each a chance to get it wrong
for no gain; the same treatment the export entry's harness pointer got last
pass, for the same reason.

A second bookkeeping debt is cleared in the same pass, and it is the same
kind the Python-pinning entry was: the 2026-07-28 error-pages build (PR #109)
shipped real 403/404/422/500 pages and an admin index in Preferences, and was
logged NOWHERE -- no Shipped entry here, no spec, no plan, the only build of
that week to skip all three. It is in Shipped now, dated to its commit rather
than to today. It was never a Proposed entry, so nothing moved up from
Proposed on its account.

The 2026-07-30 pass ships no Proposed entry at all, which is unusual enough to
explain. Tag handles came out of DESIGNING #1, the admin catalogue export: the
owner asked for the export and immediately named the hole in it, and the answer
turned out to be a prerequisite that entry never knew it had. It is logged in
Shipped with the two crashes it closed on the way.

The re-rank moves nothing, and no entry is renumbered, because nothing left
Proposed. What DID change is what #1 costs and what it can promise. A tag now
has an identity that is not its name, so the export can key on it and an import
can answer "do I already have this tag?" without guessing -- which is the whole
reason a tags file was unloadable before. #1's own text already argued for the
re-importable YAML shape over a read-only dump; that argument is now backed by
something rather than merely preferred, and the entry is annotated in place
instead of re-ranked, since a dev-seeding path is still developer value and this
list orders by user impact.

Two entries were re-read against what shipped and both stand. Minute-level
offsets (#2) goes nowhere near any of this. Eventernote actor-page discovery
(#3) gets quietly cheaper for the same reason #1 does -- a sweep that finds
forty new performers has to decide which are already in the catalogue, and it can
now be told rather than guess -- but not cheaper enough to move it. The
`RoundKind` observation (#5) is untouched.

One rider for whoever picks up #1: the export must carry each tag's HANDLE, and
the import must match on it. Matching on names would reintroduce exactly the
ambiguity this build removed, and it would do so silently.

The 2026-07-31 pass ships #1, the admin catalogue export -- the only entry the
owner ever personally asked for, and the last one standing from the 2026-07-26
usage batch. It shipped together with the tags import, in ONE spec, because an
export whose importer is designed later is precisely what produced the tag-handles
detour: the format looked complete until something had to read it back, and only
then did it turn out a tag had no identity to key on. Two entries left Proposed
across the arc (this one, plus nothing else -- tag handles was never a Proposed
entry), and the rest renumber 1-9 by that single removal, on position rather than
merit.

Two entries were re-read against what shipped, and one genuinely moved on merit.
**Eventernote actor-page discovery rises to #1.** A sweep that walks a followed
artist's page has always had to answer "is this concert already in the
catalogue?", and its sibling question "is this performer already a tag?" was a
guess until this week -- names repeat, `match_tag_ids_by_name` is first-tag-wins,
and there was no identity to compare. There is now, and the draft seam it would
emit into carries handles. That is the second time this arc has made an
unrelated entry cheaper without touching its code, and this time it is enough to
re-rank. Minute-level offsets, previously #2, was untouched by any of it and is
displaced on position only, which is worth saying plainly because it has now
been displaced four passes running without ever being judged less valuable.

The round-trip also leaves one thing DELIBERATELY unbuilt, recorded here so it
is a decision rather than an oversight: bulk concert restore. The export writes
one draft per concert and the tags import handles the taxonomy, but concerts
still go back one at a time through the paste-a-draft preview. That keeps
`import_commit` the only concert write path with a human confirming each one,
which is the app's main guard against a bad import. If a catalogue ever grows
past the point where that is tolerable, the work is: extract the write path out
of the Form-based route, and decide what replaces the preview.

The 2026-07-31 pass ships no Proposed entry and moves none, which is worth a
line rather than silence. Tag-import conflict resolution came out of a question
the owner asked about the round-trip that had shipped hours earlier -- "does it
compare contents and add what's missing?" -- and the honest answer was no, which
turned out to matter: the restore-only importer could not carry the 79 empty
`eventernote_url` values that #1 needs.

So the re-rank is a no-op by design, but #1 got materially cheaper for the
second time in two days. Eventernote actor-page discovery has to populate an
actor id per performer, and until today there was no way to move those values
from wherever they were gathered into the live catalogue. There is now, and it
is the fills half of this feature -- no decision required, because a blank
cannot lose anything. The entry stands at #1 unchanged in rank and smaller in
cost.

The third 2026-07-31 pass ships #1, Eventernote actor-page discovery, two days
after the catalogue round-trip promoted it there on merit and one day after the
tag-import conflicts pass made it cheaper for the second time. It is the third
consecutive pass whose subject was made buildable by the one before it: tag
handles gave a performer an identity, the export/import round-trip gave the 79
empty `eventernote_url` values a way into the live catalogue, and this walks
them. Nine tasks on branch `eventernote-discovery`, migrations `48cd59cae5d7`
and `052f924bbcb0`.

What shipped is narrower than the entry described in one respect and wider in
another, and both are worth recording. Narrower: it does NOT emit a YAML draft.
The entry assumed discovery would produce a paste-ready draft through
`POST /concerts/import/draft`, and the spec talked itself out of that on a fact
about the source -- Eventernote carries no ticket information at all, so a
scrape can never produce ROUNDS, which are the entire point of this app. So it
produces a LEAD ("this performance exists and you are not tracking it") plus a
paste-ready agent PROMPT, and the judgment half (grouping legs into one concert,
finding the official ticket page, extracting rounds) stays with the add-concert
skill exactly as it already worked. Wider: it is scheduled from v1 at the
owner's request, with a review surface and a per-leg source-id column, rather
than the on-demand walk the entry imagined.

The pass also ADDED two entries at the bottom, #9 and #10, and the reason they
are here at all is worth more than their rank: both were found by the build's own
reviews, deferred as minors, and lived only in the branch's SDD ledger, which is
deleted when the branch finishes. Neither is a defect the build caused. Both are
admin-only, so both rank below every user-facing entry on this list -- but "known
and written down badly" is how the Python-pinning embarrassment five entries up
happened in reverse, and a finding that survives only in a file scheduled for
deletion is not written down at all.

The re-rank otherwise moves nothing on merit and entries renumber 1-8 by that
single removal. Minute-level offsets returns to #1 by pure removal, having now been
displaced five passes running without once being judged less valuable, and is
untouched in substance. Nothing got cheaper: this build lived in a new domain
parser, a shared fetch, the scheduler tick, two new tables and an admin page,
and no remaining entry goes near any of them.

**In-app LLM extraction (#5) is explicitly NOT changed by this**, and it is
worth saying so because the two look adjacent and are not. That entry is about
EXTRACTION -- turning an event page into a filled draft -- and is blocked on API
budget. This was about DISCOVERY -- finding out an event exists at all -- and
involved no LLM at any point, which was a hard constraint rather than a
preference, since the deploy has no API access. Shipping this neither unblocks
that entry nor reduces its cost; if anything it sharpens the case for it, since
discovery now produces a steady stream of leads whose drafts a human or an agent
still has to author. Its rank is unchanged apart from the renumber.

Two things the build left DELIBERATELY unbuilt, recorded so they are decisions
rather than oversights and not re-derived later. Dismissing a lead does NOT
backfill `eventernote_event_id` onto the existing leg it duplicated, so the
exact-match branch gains coverage only through the import path, over time.
And existing legs were not backfilled at all -- for a while many leads will
duplicate concerts already held, which is what the "you may already have this"
date-and-venue HINT is for. Revisit either if the hints turn out to be frequent
enough to be annoying.

The 2026-07-31 evening pass follows hours later, on the manual sweep button (PR
#117) and one entry closed by measurement. Neither was a Proposed entry, so
nothing moved up FROM Proposed, and both are logged in Shipped -- but the pass
they trigger produced the largest re-rank since discovery itself. It ADDS the
scrape-to-agent workflow at **#1**, raised by the owner in the same breath as the
button, and that is a merit ranking rather than an insertion: discovery now
manufactures leads on demand, and a lead that still costs a full manual research
session has not saved anybody anything. Minute-level offsets is displaced to #2
for that reason and no other -- it is unchanged in substance, and its own
condition (FCFS sales) is still fired.

The same pass CLOSED old #10, the `/admin/discoveries` row-height question, by
doing what it asked and putting the seeded page in a real viewport. It is in
Shipped because the measurement is the valuable part: the hint banner was NOT
the cause. Every row was wrapping its date mid-token because automatic table
layout left the column 83px, and fixing that improved rows the entry never
mentioned. Recorded as the standing argument against settling a layout question
on paper -- and against the follow-up trap, since the first verification of the
FIX was itself a proxy (a `<td>` stretches to its row height, so measuring the
cell reported the title column and not the date).

Old #9 (nothing caps the discovery review path) is the one entry the button
genuinely touches, and it is re-reviewed rather than re-ranked. Its impact is
unchanged -- still admin-only, still nil by this list's ordering -- but it is now
reachable ON DEMAND rather than only on the daily schedule, so the largest-page
case can be summoned deliberately instead of waited for. That makes it easier to
diagnose, not more urgent. It stays at #10 (renumbered from #9). Every other
entry was reviewed and is unchanged in substance; none goes near a sweep, a
lead, or that page.

The 2026-08-01 pass ships character tags, their seiyuu and subunits, on branch
`character-seiyuu-tags` (migration `bb9780f0ad82`, eleven tasks). It was never a
Proposed entry -- it came out of the owner noticing that an idolm@ster bill
credits 如月千早 and never mentions 今井麻美, so a user following the performer
missed the show entirely -- so nothing moved up FROM Proposed and it is logged
in Shipped like the mobile retrofit, the signed-out redirect and the delivery
feed before it.

The revision pass it triggers is the largest since discovery, and almost all of
it is INSERTION rather than movement. Two entries are added, both because the
build left work it deliberately did not do rather than because anyone raised
them: **the im@s reformat enters at #1** and **a character bucket in the concert
draft vocabulary at #4**. The reformat leads because what shipped is INERT until
it runs -- there are no CHARACTER tags in the live catalogue, so every rule
above is currently a rule about nothing -- and an operation that switches a
whole feature on outranks one that shortens a workflow. That displaces the
scrape-to-agent workflow to #2, on merit and not by insertion, which is worth
saying plainly since it was ranked #1 on merit only yesterday: it is unchanged
in substance and still the thing that makes discovery pay off, but it should be
written AFTER the reformat, because an im@s lead's whole difficulty is the
character handling the reformat creates.

**The `triage-leads` entry (#2) genuinely CHANGES**, and it is the only
pre-existing entry that does. An im@s lead now has a step it did not have: the
draft must name the CHARACTER, not the seiyuu, and the concert draft vocabulary
has no character bucket to name her in (see #4), so the skill has to say "tick
her on the preview" until that ships. It also inherits a fact worth writing into
it: a character tag carries its own `eventernote_url` and the daily sweep walks
it unchanged, so 如月千早's page produces leads exactly as 今井麻美's does, and
the event-id dedup already collapses a show listed on both pages into ONE lead.

Everything else is unaffected and was re-read against what shipped. #3
(minute-level offsets) is untouched -- this build went nowhere near `PresetItem`
or the sentence builders -- and is displaced by insertion for the SIXTH pass
running without once being judged less valuable. #5 (franchise-aware round-label
suggestions), #6 (the cosmetic `RoundKind` members), #7 (PWA) and #11 (naming the
sign-in bounce destination) are untouched in every respect. #8 (in-app LLM
extraction) is unchanged and still blocked on budget, though the draft-vocabulary
gap at #4 is now a shared prerequisite: whatever produces a draft, it cannot
name a character until that bucket exists. #12 (nothing caps the discovery review
path) is re-read and NOT re-ranked: characters are sweepable tags, so a
reformatted im@s catalogue adds actor pages to the sweep and leads to that
uncapped page -- marginally worse, still admin-only, still nil by this list's
ordering.

**#9 (minor demo-parity cosmetics) GREW, in exactly the way it has grown twice
before.** The split pill (`.mchip`) and the subunit rail (`.pcluster.sub` /
`.grow2.sub`) are new components with no frame in any demo, and CLAUDE.md's own
rule says a deliberate design move should update the demo so it stays the
reference. Four pill mockups were built and shown to the owner during design,
but they lived in the spec discussion, not in `dekimasen-demo.html`, so the
design source of truth does not carry the shape that won. Same resolution as the
`.signin-note` and the error pages: fold it into that entry's single polish
pass, not its own task. Its rank is unchanged.

The 2026-08-01 pass ships the entry it filed the same morning: the im@s
catalogue reformat, which is an OPERATION and not a commit -- an agent
researched the roster, the app's own serializer authored the file, and the
owner applied it through the tag-import conflict UI. Thirteen 学園アイドルマスター
characters exist now, each with a `voiced_by` link to the seiyuu who was
previously the group's member, and the group's membership was swapped by
ticking thirteen removals by hand.

**It closed the entry WHOLE, which nobody expected when it was filed.** The
entry was written as though it faced the whole im@s franchise -- 765PRO,
Cinderella Girls, Million Live, SideM, Shiny Colors -- and a survey of the
live catalogue found exactly ONE im@s group in it: 学園アイドルマスター. The rest
of the franchise has never been catalogued, so there is nothing left to
reformat, and any group added later is authored with character members from
the start rather than migrated. What read as a medium-effort multi-group
migration was a single group, done in one import.

Two things it closes on the way past. The transitional hole the character
build documented and deliberately left unfixed -- a derived seiyuu who is
ALSO an artist member of an attached group gets re-ticked by the picker's
`autoArtists()`, so unticking her character does not remove her -- is GONE
for this group, exactly as predicted, because its members are characters
now and she is no longer one. And six of the thirteen characters carry
their own `eventernote_url`, so they joined the daily sweep immediately;
the other seven are genuinely unregistered on Eventernote and contribute
nothing until that changes.

The same pass caught a bookkeeping debt, found by checking the code before
building against it. **The character bucket in the draft vocabulary had already
shipped** -- inside the character-tags branch, a task after the review that filed
it -- so it led the morning's list as an open gap while its own three tests were
green on main. It is in Shipped now, and its removal fixes something that
mattered more than the rank: the `triage-leads` entry carried a bullet saying a
draft cannot name a character and the skill must therefore end with "tick her on
the preview", which was false when written down. That bullet now says what the
skill actually inherits. The paragraph ABOVE this one still points at "#4" for
the bucket, because it is a dated record of that pass and is left as written --
the entry it names is in Shipped, not missing.

The re-rank moves nothing on merit. `triage-leads` rises to #1 by removal
and its stated prerequisite is now satisfied -- there are character tags
for a lead to resolve to -- so the caveat inside it changes from "blocked"
to its own honest one: it is worth writing after a few real sweeps, since
its value lives in the specifics of what production leads actually look
like. Entries renumbered 1-10 across both removals.

**A second pass the same day.** Branch `dismissal-reason` shipped the one
thing #1 (below) had just finished naming as NOT this skill's work: a
`dismiss_reason` column, so triaging the 443-lead backlog stops evaporating
the moment each lead is waved off. Nothing here is removed from Proposed --
that bullet was a paragraph inside #1, not a numbered entry of its own -- so
this pass only rewrites that bullet to point at what shipped and files the
new Shipped entry below. No renumbering.


**The 2026-08-02 scope ruling (owner).** Only two of the taxonomy's seven lead
classes get catalogued: **ticketed concerts/tours** and **radio/talk/番組イベント**.
Everything else is a dismissal. This is the largest simplification the discovery
arc has had, and it lands on #1 rather than on any entry of its own: the classify
pass stops being "sort into seven buckets and weigh each" and becomes a binary
keep-or-dismiss with the reason recorded. Roughly a third of the 443 leads
survive it, and the title-stem collapse takes that third to something on the
order of fifty productions.

Two entries are FILED by the ruling rather than shipped: the classes now out of
scope (#8) and the A/B cast gap (#9), which is descoped by consequence -- it
exists only inside stage runs, and ミュージカル信長 is the sole production in all
443 leads that has it. Free public appearances go to Rejected instead of
Proposed: no ticket exists, so there is no deadline to remind anyone about, and
that is not a gap to be closed later.


**The 2026-08-02 triage build** ships #1 in three phases and empties the entry
that led this list all day. Phase 1 takes a pasted prune list and dismisses in
bulk (plan first, apply second, four buckets shown); phase 2 takes many concert
drafts in ONE paste and walks them one reviewed preview at a time; phase 3 is
the skill that produces both files. Entries renumbered 1-11.

The re-rank moves nothing on merit. What is worth recording is that the two
entries the scope ruling filed the previous day -- the out-of-scope classes and
the A/B cast gap -- are unchanged by this build and stay where the ruling put
them: the skill dismisses those classes, which is exactly what the ruling asked
for, and dismissing them is not the same as supporting them later.


**A second 2026-08-02 build**, hours after the triage arc: goods-sale rounds and
the item-requirement link (branch `goods-sale-rounds`, migration
`f846bca262ad`, ten tasks). The owner asked for both in one breath -- a merch
window should stop masquerading as a General sale, and a 最速先行 should be able
to say the serial code comes from that CD -- so it was never a Proposed entry
and nothing moved up FROM Proposed. It is logged in Shipped like the character
build, the delivery feed and the mobile retrofit before it.

**The re-rank moves nothing on merit, and this pass verified that rather than
assuming it**: the build lives in the round model, the three editor surfaces,
the concert page and the DM embed, and no Proposed entry goes near any of them.
Two entries change anyway, neither in rank. **#3 (the cosmetic `RoundKind`
members) is corrected**: it counted nine of ten and there are now ten of eleven,
which is arithmetic rather than judgment -- but the correction is worth the edit
because the entry's own prediction was under test. It exists to stop someone
adding a kind in the belief that a kind means something, and the design spec for
this one cites it by name to say the opposite, so the entry did its job and the
table it proposes simply got a row longer. **#6 (minor demo-parity cosmetics)
GREW for the fourth time**: the round card's "Requires item from" select and the
concert page's Requires / Needed-for lines are new components with no frame in
any demo, and CLAUDE.md's rule says a deliberate design move should update the
demo so it stays the reference. Same resolution as the split pill and the
`.signin-note` -- fold it into that entry's single pass, not its own task.

Everything else was re-read against what shipped and is unchanged. #7 (the event
classes outside concerts and talk shows) is the one that reads as adjacent and
is not: a `goods_sale` ROUND is a deadline on a concert somebody already tracks,
while the dismissed release-events class is a standalone 発売記念 / お渡し会 with
no ticket and no lottery. The new kind does not reopen it, and the spec says so
explicitly so nobody reads the label as a change of scope. #5 (in-app LLM
extraction) is unchanged and still budget-blocked, though the draft vocabulary it
would emit into gained one optional key. #1, #2, #4, #8, #9, #10 and #11 are
untouched in every respect.


**The 2026-08-03 calendar-discovery build** is the third in three days that was
never a Proposed entry, and it came from an owner CONCERN rather than an owner
request: partway through planning the ~90-tag im@s/LL character-and-seiyuu
expansion he asked what that would do to the daily sweep, and the honest answer
was "hundreds of extra third-party fetches a day". Investigating a cheaper source
found one, so the sweep learned to read public `.ics` feeds and characters left
the daily rotation. Logged in Shipped like the goods-sale, triage and mobile
builds before it; nothing moved up FROM Proposed.

**Its revision pass ADDS two entries and re-reads a third, which is the whole of
the movement.** New #11 (the calendar roster's blind spots) and new #13 (nothing
notices a feed going quiet) are both consequences of what shipped -- gaps the
build MEASURED and chose not to close, which is exactly the kind of thing this
file exists to keep out of a report nobody reads again. The former #11 (nothing
caps the discovery review path) becomes #12 by that insertion, never on merit,
and it got the re-read its subject matter demanded: **calendar leads add volume
to the unbounded page, and the entry is unchanged anyway** -- see the note added
inside it, which records the arithmetic rather than leaving the next reader to
redo it. Nothing else in Proposed goes near feeds, the sweep or the discovery
surfaces; #1-#10 were re-read and are untouched in every respect. The pass also
cleared a bookkeeping debt it did not create: the two cross-references between
the out-of-scope event classes and the A/B cast gap had pointed one entry too
low since an earlier renumber, and are name-based now, the same fix the
sign-in-bounce pointer got after being bumped five times. And #11 was drafted
low-medium and re-rated `low` on review, which is what places it BELOW the `low`
band above rather than inside it: the higher rating rested on "a user could miss
a deadline", while the entry's own argument is that a missed round is not a
missed concert -- so its position is a decision, not a slip.

**And the operation this build was reprioritized in front of is now unblocked.**
The 765PRO / Shiny Colors / Love Live tag expansion waits on nothing: the load
objection that stopped it is answered, the sweep no longer grows with the
character count, and the expansion is CATALOGUE work through the tags import,
not code. It is deliberately not an entry here -- this file tracks features, and
that is the owner's next named operation, done through surfaces that already
shipped.

The 2026-08-03 capture pass ADDS two owner-reported defects without shipping
anything, the day the first calendar-fed triage ran end to end (264 dismissals,
an eight-draft import batch). Both were found by the owner using the app, not
by a build's review. The onboarding skip enters at #1 on the correctness
precedent (a first contact silently served wrong outranks every feature); the
venue-dialog backdrop enters at #8 beside the polish family, unranked-in-anger
because its symptom is not yet described. Every entry from the old #1 down
shifts by insertion, never on merit.

**The 2026-08-03 fix pass empties both of them the same day**, on one branch
(`fix-onboarding-skip-and-dialog-drag`, three tasks, migration `aba3e97e4467`).
They were batched for the reason the 2026-07-28 cleanup batch was: each is
small, and neither was ever going to win a prioritisation against the other.
The onboarding fix shipped exactly as its entry prescribed -- worth saying,
given this file's Python-pinning embarrassment -- while the dialog entry was
prescribed WRONG and knew it: it was filed unranked-in-anger pending one
sentence from the owner, and that sentence ("dragging from inside of dialog to
outside closes it", desktop) re-routed the diagnosis off the backdrop CSS the
entry suspected and onto the close handler, where it was settled by code
reading and git history with no viewport at all. Both Shipped entries below
record their own halves of that.

The re-rank moves nothing on merit, and entries renumber 1-13 by the two
removals. **Minute-level reminder offsets returns to #1 by pure removal**, one
pass after the capture entry displaced it -- the shortest displacement it has
had, and the record is kept straight here for the same reason the previous five
were: it has never once been judged less valuable, only moved. It is untouched
in substance, and nothing else got cheaper either -- this pass lived in one
column, the OAuth callback's redirect decision and two template `<script>`
blocks, and no remaining entry goes near any of them. Two were re-read against
what shipped and both stand: #6 (minor demo-parity cosmetics) did NOT grow, for
the first time in four passes, because a build whose second half is a deletion
adds no component a demo owes a frame for; and #12 (nothing caps the discovery
review path) goes nowhere near either fix. No cross-reference needed bumping --
every live pointer in Proposed is name-based already, and the numeric ones
inside the minute-level entry are dated records of earlier passes, left as
written.

The 2026-08-04 capture pass ADDS one owner-raised entry the day after the fix
pass above, and it enters at **#1 on merit**: the owner, living with the app,
named the per-round `.ics` download flow as the pain ("adding each event with
a new calendar event file just sucks -- let's make it a subscription link").
Filing followed the file's own discipline: the tree was checked first (a
personal subscription feed already EXISTS, so the naive entry would have
described a solved problem -- the Python-pinning failure mode), and the owner
was then asked which of four candidate gaps actually bites. He ticked ALL
FOUR, which is what makes this one entry rather than a cosmetic relink: the
feed is undiscovered, its content is wrong for him, its token flow is
friction, and the download buttons should be replaced outright. Every entry
from the former #1 down shifts by insertion, never on merit -- and
minute-level offsets is displaced for the SEVENTH time, one day after its
shortest-ever return; the running record continues in its entry.

The second 2026-08-04 capture pass, hours after the first, ADDS an
owner-reported defect and enters it at **#1 on the correctness precedent** --
the same one that ranked the onboarding skip and the dead-concert entry before
it, sharpened here by an irreversible press: Home offers APPLIED, which
`record_round_outcome` will not take back, on a round whose only leg the
reader already said they are skipping. The diagnosis was run BEFORE filing
(root cause verified against the tree, three surfaces named, the single-leg
shape confirmed by the owner), so the entry is a work order rather than a
symptom; the owner chose filing over a same-day fix. The calendar-story entry
is displaced to #2 after hours at #1 -- by insertion on the precedent, not by
any reassessment of its merit -- and minute-level offsets takes its EIGHTH
displacement, to #3, without ever once being judged less valuable; the record
continues in its entry.

**The 2026-08-04 fix pass empties that entry the same night it was filed**, on
one branch (`leg-opt-out-surfaces`, seven tasks, data migration
`db750444962a`). It shipped as the entry prescribed -- the entry's own sentence
was "the rule to apply is the one invariant 8 already states, applied
uniformly", and the build's whole shape is two shared helpers every surface
consumes -- and it settled the one thing the entry deliberately left open (the
board), by test rather than by argument: a fully-opted-out round leaves the
live card, which mirrors cancellation exactly. The sweep clause's two
check-don't-necessarily-change surfaces came back UNCHANGED with reasons, which
is a result and is recorded in the Shipped entry rather than left for the next
reader to re-derive.

The re-rank moves nothing on merit, and entries renumber 1-14 by the one
removal. This build lived in read-surface filters and one data migration, so
**nothing else in Proposed got cheaper, changed shape, or moved on its own
account** -- every remaining entry was re-read against what shipped and stands
as written. Two contacts are worth naming rather than implying. **The
calendar-story entry rises to #1 by pure removal**, and it inherits something
concrete: the old #1's closing paragraph made "the feed must never carry an
opted-out leg" a constraint flowing DOWN onto it, and that constraint is now
enforced at the QUEUE (`sync_rule` plans no day rows for an opted-out leg), so
whatever content the feed grows into gets it for free -- the note is added
inside the entry. And **minute-level offsets returns to #2 by pure removal**,
the day after its eighth displacement; the running record continues in its
entry, unchanged in substance as it has been through all nine moves.

**The 2026-08-04 calendar pass empties the #1 entry the same day it took the
top spot** -- the SECOND same-day #1 ship this file has recorded in a single
day, and both are in Shipped below. The morning's owner-raised calendar entry
went to #1 on merit, was displaced hours later by the opt-out defect, inherited
#1 again that night when the defect shipped, and shipped itself the next
morning on one branch (`calendar-feed-story`, six tasks, spec
`docs/superpowers/specs/2026-08-04-calendar-feed-story-design.md`). Filed and
emptied inside a day, twice over: the file has not seen a day like it, and the
useful lesson is not the speed but the shape both entries shared -- a diagnosis
run before filing, so each entry arrived as a work order and the design
discussion only had to settle rulings.

It shipped as the entry decomposed itself, and **all four confirmed gaps came
back with an answer rather than a partial**. Discoverability: a "📅 Calendar"
dialog on the concert page, where calendar intent actually occurs, minting for
a user who has never held a token and returning them to the concert they were
reading. Content: the landscape ruling -- shows plus the deadlines that still
need you, derived from standing rather than from reminder rules, which is what
stops a sparse preset reading as a broken calendar. Token flow: `webcal://` and
a copy button through one shared partial, entirely WITHIN invariant 5's
shown-once shape, exactly as the entry prescribed ("that shape is NOT the thing
to fix -- the friction to remove is around it"). And the buttons: deleted,
route and builder with them, per the entry's own ruling. The one branch the
entry flagged also fired and was handled on purpose: its inherited "the feed
must never carry an opted-out leg" constraint held only while the feed read
`reminder_queue`, and this build stopped sourcing from the queue -- so the
opt-out filter was re-applied at the new derivation through the same shared
helpers, which is precisely the "re-applied on purpose" case that paragraph
named.

The re-rank moves nothing on merit, and entries renumber 1-13 by the one
removal. The build lived in one service function, one domain map, three
templates and a deletion, so every remaining entry was re-read against it and
stands as written. Two contacts are worth naming. **Minute-level offsets
returns to #1 by pure removal** -- its fourth move in a single day, every one
of them on 2026-08-04, and this build sharpens rather than weakens it: an FCFS
round's OPENS moment is now on the calendar itself, which is the entry's own
strongest case made visible; the record continues in its entry. And **the PWA
entry gains one annotation, no rank change**: a subscribable feed is this app's
second surface that works with the site closed, which is prior art its
push-notification argument should now cite rather than a substitute for it.

**The 2026-08-04 late pass ADDS the crawler-trap capture at #1, hours after
the calendar ship, on the day's third precedent: an outage outranks every
feature.** Production was down for roughly half a day (first pool exhaustion
11:49 UTC, recovery ~23:30) and the diagnosis was run live before filing, so
the entry below is a work order with the incident's evidence inside it. The
CURE is already deployed -- a Cloudflare Managed Challenge rule -- but it
lives entirely outside this repository, which is exactly why the entry
exists: the code-side hardening is what survives a dashboard wipe, and a
guard nobody can see from the tree is a guard that gets lost. Every entry
from the former #1 down shifts by insertion, never on merit; minute-level
offsets takes the displacement below, its fifth move of this one day.

**The 2026-08-04 hardening pass empties that entry the same night it was
filed** -- the THIRD same-day #1 ship in this file's short run of them -- on
one branch (`crawler-trap-hardening`, four tasks, no spec: the diagnosis had
been run live during the outage, so the entry arrived as a work order and the
build had only to execute it). All three code-side layers shipped in the
entry's own cheapest-first order, and the one question it deliberately left
open came back with an answer rather than a guess: the robots directive's
shape. `?` is not a metacharacter under either the 1994 grammar or RFC 9309,
and both match a `Disallow` value as a literal prefix against path-plus-query,
so `Disallow: /discover?` blocks the whole combinatorial URL space while the
bare catalogue page stays crawlable -- and requires no wildcard support of the
crawler at all. The entry's "explicitly NOT tracked" line held: no caching or
cheap-render path for anonymous filtered Discover was built, and it remains
the heavyweight remedy reserved for a challenge-passing crawler firing the
trap again.

The re-rank moves nothing on merit, and entries renumber 1-13 by the one
removal. The build lived in one template, one route and two runbook
paragraphs, so every remaining entry was re-read against it and stands as
written -- the one contact is the Discover-sort-in-the-content-head entry,
which now carries a rider naming the `rel="nofollow"` a relocation must
keep, exactly as its fsheet rider already does. **Minute-level offsets returns to #1 by pure
removal**: its twelfth move, and its SIXTH inside this single day. The running
record continues in its entry, where twelve moves have now produced twelve
identical verdicts.

The 2026-08-05 pass is the first sourced from a TRIAGE session rather than a
build or a review: clearing the 528-lead discovery backlog (222 dismissals
committed through the first agent-authored prune list, batch-1's five
researched drafts, 79 further dismissals ruled the same evening) surfaced the
gap the new #1 records -- nothing ever re-checks a tracked concert's round
ladder, and the session caught two live instances of the failure only by
accident. Filed at #1 on the correctness-family precedent; every other entry
is pushed down by insertion, never on merit, and minute-level offsets takes
its thirteenth displacement in the entry's own running record.

The second 2026-08-05 pass is the first in this file whose ship EMPTIES NO
ENTRY and yet reorders the list: AI triage phase 1 took the discovery-lead half
of the in-app LLM extraction entry (old #6, now #7) and left the import-page
half standing, so that entry is rewritten rather than moved to Shipped. Three
things changed and all three are worth stating. **The budget block lifted** --
the owner bought DeepSeek V4 Flash credits, which is the one condition that
entry had been waiting on since 2026-07-22, so it is actionable for the first
time; its rank is unchanged anyway (it moves 6 → 7 by the insertion below,
never on merit), because unblocking is not impact. **Phase 2 is filed as a new
entry at #3** (AI completion of a skeleton draft into a full draft, rounds
included) on the owner's own two-phase framing, with its one hard design
question -- how the official ticket page reaches the model -- recorded in the
phase-1 spec rather than re-derived later. And the pass found ONE genuine
interaction it would have been easy to miss: phase 1's drafts carry `rounds: []`
by construction, so each one is another tracked concert with an empty ladder,
which sharpens #1 (nothing re-checks a tracked concert for newly opened rounds)
instead of relieving it -- annotated there, rank held. #14 (nothing caps the
discovery review path) was re-read as the entry most likely to move on a
review-volume change and held: triage dismisses nothing itself, so the page is
the same size, and only the LIFETIME of a large backlog got shorter. Entries
3-14 renumber to 4-15 by the one insertion; nothing moved on merit, and
minute-level offsets holds #2 for once.

The 2026-08-06 second pass adds two entries and ships nothing, which is a first
for this file: both came out of a conversation rather than a build. One is the
phase-2 CALIBRATION press (new #1) -- the owner deferred it for time, and it is
filed at the top as a GATE rather than on impact, because it is the only item
here where minutes decide whether an already-merged build is worth anything.
The other is the reminder tick's three long jobs (new #7), raised when the
owner asked whether a more traditional infrastructure stack would help
performance or maintenance; the answer was no on both counts, and that entry
records what was ruled OUT as carefully as what it proposes, so the question
does not have to be re-litigated from scratch. Entries 1-5 renumber to 2-6 and
6-14 to 8-16 by the two insertions. Nothing moved on merit. Round-watch takes
its first displacement ever and minute-level offsets its fourteenth, both
recorded in their own entries.

**The 2026-08-11 pass empties the top TWO entries at once, by two different
mechanisms, and that is a first for this file.** Round watch (old #2, and #1 on
impact throughout its life) shipped on branch `round-watch` -- eight tasks,
spec `docs/superpowers/specs/2026-08-11-round-watch-design.md` -- and it ships
the CHEAPEST of the three shapes that entry recorded and only that one: an
admin worklist of catalogue concerts whose ladder holds no future deadline,
plus a digest DM when one newly goes quiet. The other two shapes (the
discovery-matcher round-gap flag, the scheduled re-fetch of each concert's own
official URL) are NOT built, remain wanted, and are re-filed as the successor
entry at #2, demoted from #1 on merit -- the original called making the failure
visible "most of the value", and that half is what shipped. Above it, old #1
(the phase-2 calibration press) is DELETED rather than moved: the press ran on
2026-08-09/10, and that entry's own closing instruction was that it comes off
the list "not into Shipped, since it ships nothing, but deleted with its
verdict folded into the phase-2 shipped entry". Its verdict -- the model's
judgment was fine and the CHECKER was not, a mutation harness proving 111 of
129 year-shifted claims -- and the nine commits the press produced on
`triage-calibration-fixes` (PR #146) are now in that entry. The one live cross-reference the deletion would have invalidated (#18
agent write endpoints, pointing at "the item that used to sit at Proposed #1")
was corrected in place, as were the two renumber-invalidated pointers at #17
and #18.

Everything below renumbers 1-19 by the two removals and the one insertion, and
**minute-level offsets returns to #1 on its fifteenth move** -- the first ever
to arrive by three mechanisms in one pass (a deletion, a ship, and a successor
ranked below it on merit); the running record continues in its entry. Five
entries carry genuine dated re-reads from this build and none of them moved:
the tick's long jobs (#6) gained a FOURTH job that is deliberately NOT a long
one, and the note says why so nobody re-derives it; the unbounded-admin-list
entry (#14) gained an exact twin, since `/admin/quiet-ladders` also emits its
copy block twice; the calendar-feed-silence entry (#15) got the largest effort
drop on the list, because round watch is a working instance of precisely its
shape, with the stamp, the backfill, the idempotency and the
`UNREPORTED_NOTE_KINDS` ruling all already settled in code; and the two
agent entries (#17, #18) gained one new producer and one newly-named write gap
respectively. The remaining twelve were re-read and stand as written, with the
reasoning in the revision-pass note at the end of Proposed.

**The second 2026-08-11 pass ships an entry that never had a number**, which is
a first for this file and worth recording as a pattern rather than as a
curiosity. Outcome correction was filed at the head of Proposed that morning
DELIBERATELY UNRANKED -- the entries below kept 1-19 while it was in flight,
because renumbering nineteen entries for an item already on a branch buys
nothing and costs cross-references, which this file's own history names as a
hazard. It shipped the same day on branch `outcome-correction` (five feature
commits, spec
`docs/superpowers/specs/2026-08-11-outcome-correction-design.md`), so **the
renumber that deferral owed resolves to a no-op**: the note leaves the head of
Proposed, nothing below it was ever displaced by it, and every entry keeps the
number it had. An item filed while it is already being built does not need one.
(Something else does move the tail of the list, and it is unrelated to the
deferral -- next paragraph.)

What the re-rank DOES move is one insertion, on merit and from this build's own
reviews. **Two narrow edges the build did not close enter at #14** -- a flat
`dk:won` press that can leave a round WON while it secures nothing, and a
terminal DM branch whose backtrack truthfully says "Cleared" while clearing
nothing -- ranked in the low band, above the admin-only discovery-cap entry on
the plain tiebreak that a user-facing wrong state outranks an admin
inconvenience, and no higher because both are RARE (the correctness sweep of
2026-07-29 deferred an entry on exactly that ground). Entries 14-19 renumber to
15-20 by that one insertion, never on merit; the two live pointers it
invalidated were made NAME-based rather than renumbered, so neither can break
again, and the 2026-08-11 round-watch revision note carries a bracketed
correction instead of being rewritten -- a dated note should record the numbers
its own pass left.

Everything else was re-read against what shipped and stands. **Minute-level
offsets HOLDS #1** rather than moving for a sixteenth time -- the first pass in
its long record where the thing that shipped was never ranked against it at all,
so there was no rank for it to inherit. One entry grew: minor
demo-parity cosmetics (#8) takes a SIXTH item, and unusually it is not a
demo-parity gap at all but a measured CSS defect this build reproduced in
already-shipped markup -- folded in there because it is one line in the same
single polish pass, not because it is the same kind of thing. The reasoning for
the other seventeen is in the revision-pass note at the end of Proposed.

**A fourth 2026-08-11 pass files an entry by DELETING a gap from another one.**
Working entry #8 turned its "Follow another tag" cosmetic into an owner ruling
that the whole Following surface wants reworking, so that gap left #8 without
being fixed and became an entry of its own at the head of Proposed — unranked,
for the reasons it gives. Two things worth recording as pattern. First, the
question that produced it was a genuine 50/50 (the demo's dialog is also what
the app's own picker convention demands; the shipped fold is the only shape
that survives JS being off), and putting a real fork to the owner is what
surfaced that neither answer was wanted. Second, **#8 shrank without shipping
anything** — five items left, all still demo-parity absences — which is the
first time this file has recorded that shape. Its rank is unchanged: it was
already ranked as polish, and losing a polish item does not move polish.

**A third 2026-08-11 pass ships one ITEM out of an entry rather than an entry**,
which is new for this file and is why it gets a note despite being one CSS
selector. #8's sixth item -- the `.danger` card frame also styling every
`.btn danger` -- shipped on its own branch hours after it was filed, leaving the
other five in place. Nothing renumbers: an entry that loses one of six folded
items does not move, and #8 keeps its rank, which the entry itself now explains.
The pass is recorded there rather than in a Shipped section entry, since what
shipped was never an entry. It also CORRECTED its own filing in two places (the
stretched element was the sibling, not the button; the offending margin was
already overridden at both of the card's real sites) -- both errors of a
description written from the stylesheet instead of from the page, which is the
measure-don't-reason rule catching a miss in the very entry that records
measured defects.

**The 2026-08-12 pass ships an entry that was half wrong about itself**, and
that -- not the polish -- is what it is worth recording for. Minor demo-parity
cosmetics (#8, this file's longest-lived entry, raised 2026-07-20 and grown six
times) shipped on branch `demo-parity`, nine tasks, spec
`docs/superpowers/specs/2026-08-11-demo-parity-design.md`. Before any of it was
built the owner replaced the entry's standing assumption -- *the demo is right
and the app should change* -- with **judge each on merit**. Of the eight items
the entry tracked, four turned out not to be what it said they were, and **two
reversed direction**: the Tags member list and the Setup pick tiles now change
the DEMO, not the app, and **both would have been regressions had the pass run as
filed** (an inert "+N more" on an edit dialog makes five of nine members
unreachable; `<button aria-pressed>` cannot submit with JS off, which the app's
hidden checkbox does). A third item was struck rather than built: it asked for a
demo frame for `.signin-note`, a class deleted three days after the item was
filed. The pattern to keep: **a cosmetic list is a set of claims about the app,
and claims decay** -- and the oldest entry decayed the most.

One insertion, on merit and out of the build's own deferrals: **the demos are one
grammar-migration behind the app enters at #17**. Their `.banner` is a pre-G2 top
concept-bar sharing a class name with the app's G2 callout, and two separate
tasks each had to add a compound rule re-declaring every property of the old one
just to stop it bleeding through. It is ranked in the nil-for-users band between
`db/core.py`'s shape and the agent-skills entry, with the case AGAINST ranking it
higher written into the entry itself: it is a documentation-quality problem with
no user-visible impact, and every instance of it was found and worked around
inside the task that met it.

The removal and the insertion cancel, so **the list is 1-20 again**: the former
#9-#17 renumber to #8-#16 by position alone, and #18-#20 keep the numbers they
had. Nothing moved on merit, and this build **removed nothing from any other
entry's path** -- it touched two demo files, one label, one dialog footer and one
CSS declaration on two selectors. Minute-level offsets holds #1 for the second
consecutive pass, its seventeenth. Two live pointers were corrected in place
rather than left to rot: the Following entry's reference to "the entry-#8
brainstorm" is name-based now, and the sign-in-bounce entry's long-standing
"ranked below the demo-parity cosmetics batch and the Discover-head entry" lost
half its comparison and says so instead of quietly meaning less than it did.

**The 2026-08-13 pass ships the entry that produced #17 above, closing the loop
this preamble has been tracking since 2026-08-11.** The unranked Following
entry, filed the same day it deleted a gap from #8, finished its fourth and
final phase and moved to Shipped -- and being unranked the whole time, it
takes NO number down with it; #1-#20 are untouched by the move itself, for
the first time in this file's history that a Shipped move causes zero
renumbering. One new entry is filed in its place: #21, a deferred sub-gap
(a lone character on `/tags` rendering as a plain chip, not a split pill)
that the Following entry had been carrying as a note since phase 2 and that
finishing the rework did not happen to fix. Two owner rulings came out of
building the last phase and are recorded in full in the Shipped entry and in
`docs/architecture.md`: the standing default covers preset only, not notify
(no `User` column, matching the rework's zero-migration run), and a
tag-specific preset now outranks the blanket default in `handle_newly_tagged`
-- reversing the earliest-wins rule after the retroactive fill made it
capable of silently outranking a hand-tuned preset. #16, #17 and #19 were read
closely, all three unchanged in rank and all three explained in place -- #19
on the closest precedent this file has, the 2026-08-11 pass that read it for
the same reason (a new per-user write) and found the same answer; the rest
were re-read and stand as written. Full reasoning at the tail of this
Proposed section, ahead of the older passes it now sits above.

## Proposed (highest impact first)

### 1. Minute-level reminder offsets

Impact: medium (raised from low) - effort: small. Raised: 2026-07-18
(domain-model review discussion). Re-ranked 2026-07-19.

`ReminderRule`/`PresetItem` already support `offset_days` + `offset_hours`
(0-23); there's no `offset_minutes`. The 60s scheduler tick already
delivers at ~1-minute granularity, so the gap is purely the data model +
form UI, not scheduling precision.

Re-ranked up because the condition the owner attached to it has now
fired: the entry said "revisit if/when FCFS-style rounds get their own
flag", and `RoundKind.FCFS_SALE` shipped on 2026-07-18. First-come-
first-served sales are exactly the case where "remind me 5 minutes before
it opens" beats "remind me 3 hours before", so this is no longer
speculative - it just has no user complaints behind it yet.

Reinforced, not re-ranked, by the 2026-07-20 onboarding build: the welcome
wizard's default-reminders step had to drop the demo's "30 minutes before"
fine-tune option for the same reason (`PresetItem` has no minutes column),
so the gap now visibly shows up in a second surface, not just FCFS sales.

Re-reviewed 2026-07-20 (i18n build): whichever "N minutes before" copy this
eventually ships (fine-tune option labels, sentence-style rule descriptions)
will need both catalogues filled in alongside the schema/form work -- one
more small addition to effort, not a reason to re-rank.

Displaced to #2 on 2026-07-22 by the venue-to-tags build's import-preview
regression, then returned to #1 the same day when that regression shipped as the
import per-leg venue picker (phase 1 follow-up, see Shipped). Nothing about this
entry changed; it is back on top because the thing that briefly outranked it is
done, and it is now the highest-impact user-facing gap left standing.

Re-reviewed 2026-07-23 (agent-import build): explicitly unchanged. The import
seam touches concert creation, not reminder offsets, so nothing here moved --
it stayed on top at the time, with the new Eventernote discovery entry placed
under it for the reason given there. (The same evening's owner-priority batch
then pushed both down by insertion -- position, not substance; the heading
carries the current rank.)

Displaced to #2 later the same day by the scrape-to-agent workflow, on merit and
not by insertion: that entry is what makes the feature which just shipped pay
off, while this one remains a proven-but-uncomplained-about gap.

Back at #1 on 2026-07-31 by pure removal, when Eventernote discovery -- the
entry that had passed it two days earlier -- shipped. Re-read against that build
and unchanged in every respect: discovery lived in a parser, a fetch, the
scheduler tick and an admin page, and touches neither `PresetItem` nor the
sentence builders. Worth saying plainly, since this entry has now been displaced
five passes running without once being judged less valuable.

Back at #1 again on 2026-08-03 by pure removal, one pass after the
onboarding-skip entry displaced it and one day after that entry was filed --
the shortest displacement in this entry's history. Re-read against what shipped
(one column on `User`, the OAuth callback's redirect decision, two template
`<script>` blocks) and untouched in every respect. The number keeps moving; the
reading has not changed once.

Displaced to #2 on 2026-08-04 by the calendar-feed entry, the seventh
displacement and the fastest yet -- one day at #1. By insertion on the new
entry's merit (owner usage pain, four gaps at once), never on this one's:
still untouched in substance, still never judged less valuable. One genuine
point of contact worth naming rather than implying: FCFS sales are this
entry's own strongest case, and a calendar rebuilt around the feed makes a
"5 minutes before it opens" reminder MORE visible when it exists, not less --
the two entries reinforce, they do not compete.

Displaced to #3 hours later the same day by the opt-out suppression defect --
the eighth displacement, and the first time this entry has moved twice in one
day. Same verdict as the previous seven: position, never substance.

Back to #2 the same night by pure removal, when that defect shipped -- three
moves in one day, and the shortest displacement in this entry's history by a
wide margin. Re-read against what shipped (read-surface filters and one data
migration; nothing near `PresetItem`, the offset form or the sentence
builders) and untouched in every respect. Ninth move, ninth time the reading
has not changed.

Back at #1 the next morning by pure removal, when the calendar-feed entry that
displaced it the day before shipped -- the TENTH move, the FOURTH inside a
single day, and the end of the seventh displacement, which outlasted the eighth
by a matter of hours. Re-read against what shipped and untouched in substance
as always, but this build is the first in the run to touch this entry's own
argument rather than merely its number: a no-outcome round's OPENS moment is
now an event on the user's calendar feed, so an FCFS sale's opening is
something a reader SEES without owning a reminder rule for it. That is the
reinforcement the seventh-displacement note above predicted, made concrete --
the visible moment is exactly the one a days-and-hours offset can only remind
you about too early, so the case for minutes is sharper than it was, and this
entry is the highest-impact user-facing gap still standing.

Displaced to #2 again late the same evening by the crawler-trap capture --
the eleventh move, the FIFTH inside this single day, and the first time the
thing that outranked it was an outage rather than a feature or a defect.
Same verdict as every move before it: position, never substance.

Back at #1 the same night by pure removal, when that capture shipped hours
after it was filed -- the TWELFTH move and the SIXTH inside one day, which is
the whole of this entry's 2026-08-04. Re-read against what shipped (link
attributes in one template, a `/robots.txt` route, two runbook paragraphs) and
untouched in every respect, as it has been through all twelve: position, never
substance.

Displaced to #2 on 2026-08-05 by the round-watch entry -- the THIRTEENTH
move, by insertion on the new entry's merit, and the second time the thing
that outranked it came from the correctness family rather than a feature: a
reminder fired at a slightly-wrong offset degrades, but a round the ladder
never learned about fires nothing at all, and only one of those failures is
silent. Same verdict as the twelve before it: position, never substance.

Held at #2 on 2026-08-06 by pure removal, when the phase-2 entry directly below
it shipped -- the first time in this entry's history that a removal did NOT
change its number, since everything that renumbered sat underneath it. Re-read against what shipped (a runner, a pure evidence checker, a
host policy and an admin approval page; nothing within reach of `PresetItem`,
the offset form or the sentence builders) and untouched in substance for the
fourteenth consecutive pass. One thing worth naming rather than implying,
because the two builds do touch: an AI-completed round can now carry an
`apply_opens_jst` the editor never typed, which means the moment an FCFS sale
opens is more often PRESENT in the data than it used to be -- and a moment
present in the data is precisely the one a days-and-hours-only offset can only
remind you about too early. That sharpens this entry's own strongest case
again, exactly as the calendar-feed build did, without changing its rank.

Displaced to #3 on 2026-08-06 by the two conversation-sourced entries inserted
above -- the FOURTEENTH move, and the first caused by entries that ship nothing
and deliver no feature at all. Same verdict as the thirteen before it.

**Back at #1 on 2026-08-11 -- the FIFTEENTH move, and the first ever to arrive
by three mechanisms at once.** The entry above it was DELETED (the calibration
gate ran, and it shipped nothing, so it left the list rather than moving to
Shipped); the entry above THAT shipped its cheapest shape; and the successor
carrying that entry's two unshipped shapes was ranked BELOW this one on merit
for the first time in their coexistence, because making the round-watch failure
visible was, by its own filing, most of its value. Re-read against what shipped
(one db feature module, one pure message builder, one admin page, one scheduler
block) and untouched in substance for the fifteenth consecutive pass: nothing
round watch built comes within reach of `PresetItem`, the offset form or the
sentence builders. Position, never substance -- fifteen moves, fifteen
identical verdicts.

**HELD at #1 later the same day by outcome correction, which is the first pass
to leave this entry alone by construction rather than by arithmetic**: that item
was filed unranked while it was already on a branch, so it never occupied a
place this entry could inherit or lose. Re-read against what shipped anyway (one
service writer, one route, one template macro parameter, three Discord buttons)
and untouched in substance for the sixteenth consecutive pass -- it deletes
`RoundOutcome` rows and re-plans through `reinstate_user_rules`, which is the
existing `sync_*` machinery unchanged, and `PresetItem` still has no minutes
column. Sixteen passes, sixteen identical verdicts.

**HELD at #1 again on 2026-08-12 by the demo-parity build, which is the first
pass where the entry that shipped was ranked BELOW this one and its replacement
was ranked below it too** -- a removal at #8 and an insertion at #17 cancel, so
this entry did not move and could not have. Re-read against what shipped (one
Preferences label and its two catalogue rows, one dialog footer moved out of its
form, one CSS declaration on two selectors, and two demo files) and untouched in
substance for the seventeenth consecutive pass. One point of contact worth
naming rather than implying, since it is the closest this build came: the label
that changed is Preferences' Auto-apply toggle, which sits in the same
`.subrow` a minutes-capable rule would eventually have to render in -- but the
change was one word of copy, not a control, and `PresetItem` still has no
minutes column. Seventeen passes, seventeen identical verdicts.

### 2. Round watch: the two shapes it did not ship

Impact: medium (down from high, on merit -- the failure is now VISIBLE, and the
entry that filed it called visibility "most of the value") - effort: one small,
one now-medium (down from large, 2026-08-13 -- see below). Raised: 2026-08-05;
the cheapest of its three shapes shipped 2026-08-11 and is in Shipped below;
the large shape's PHASE 1 shipped 2026-08-13, also in Shipped below. Phase 2 of
that shape and the small shape are both still open and are what remains here.

**Phase 1 of the large shape shipped 2026-08-13** (branch `round-poll`, spec
`docs/superpowers/specs/2026-08-13-round-poll-design.md`, Shipped entry below).
What exists now: a flag-gated (`ROUND_POLL_ENABLED`, off by default) daily pass
that re-reads each quiet concert's own official page with DeepSeek, reusing
AI triage's own phase 2 (`draft_completion`)'s `completion_prompt` and
`verify_rounds` rather than owning a second prompt or a second safety rule,
and records what it finds as `RoundProposal`
rows -- reviewable, read-only, at `/admin/quiet-ladders/proposals`. What does
NOT exist yet: nothing turns a proposal into a live `Round`. There is no draft
or review page beyond the read-only list, `RoundProposal.applied_at` is
written by nothing in the codebase, and a proposal nobody has looked at is
simply re-read and re-shown every day rather than accumulating toward
anything. That review-and-apply half is phase 2, unbuilt, and is the effort
downgrade above: the expensive parts -- the fetch policy, the host-approval
queue, the prompt, the data model -- already exist, so what is left is a
review UI and one write path, not a subsystem built from nothing. **The small
shape -- teaching the discovery matcher a round-gap dimension -- is untouched
by any of this** and remains exactly as unstarted as the day it was filed;
nothing about the poll reads or writes anything the matcher consumes.

The problem statement is the parent entry's and is not repeated here -- it is
in the Shipped entry, verbatim, with the 2026-08-05 evidence still attached.
What changed on 2026-08-11 is the DEFENCE. Until then, in that entry's own
words, "the only current defense is the owner remembering to re-check"; now
`/admin/quiet-ladders` lists every catalogue concert whose ladder holds no
future deadline, longest-unattended first, and a digest DM names each one
within a minute of it going quiet. The failure is still reachable -- it is
simply no longer silent -- and both shapes below are now automation of a
problem somebody can SEE rather than the only way to see it at all.

**Teach the discovery matcher the round dimension** (small). A calendar lead
naming a round its matched tracked concert LACKS should flag "round gap"
instead of only the date+venue hint. It covers feed-covered franchises only,
but the catch that filed the parent entry proves the signal is real and already
arriving: the 蓮ノ空 103期卒業公演's missing アップグレード rounds (1次 AND 2次)
surfaced SOLELY because fan-calendar leads happened to name them, in a session
that came close to pruning those leads unread. What it gained on 2026-08-11 is
a destination it did not have when it was filed -- and, more usefully, a reason
to exist beside a surface that might have looked like it obsoleted it. It does
not: the quiet-ladders predicate cannot see this case BY CONSTRUCTION. A
concert holding a future anchor and a missing round is not quiet, so it is
absent from that list however long its gap persists, and a round-gap flag is
the only proposed signal that catches it.

**A scheduled re-fetch of each concert's own official URL** (was large; now
medium -- phase 1 of it shipped 2026-08-13, what's below is what's LEFT). The
re-fetch pass this originally proposed is built: `app/round_poll.py`
(`ROUND_POLL_ENABLED`) consumes `quiet_ladder_rows` as its candidate list
exactly as this entry specified rather than a second scan of the catalogue,
reads under `ApprovedPublicHosts` and the `/admin/fetch-domains` queue exactly
as this entry required rather than trusting an editor-supplied host on its
own say-so, and its rounds are evidence-grounded through `verify_rounds`
exactly as this entry assumed. **What remains is the half this entry never
had to design because the re-fetch didn't exist yet: turning an accepted
`RoundProposal` into a live `Round`.** That needs a draft/review page (the
proposals list at `/admin/quiet-ladders/proposals` is read-only), a per-round
apply action that writes `RoundProposal.applied_at` and a real `Round` row in
one transaction, and the queue-sync call every round write already owes
(invariant 2) -- none of which exists. `docs/architecture.md`'s Round poll
entry records the traps phase 1 already found the hard way (the resume-order
re-sort, the dedupe key's minute truncation, the wall-clock budget); phase 2
inherits that data model and those traps rather than starting over.

**Ranked #2, down from #1, on merit rather than by removal**, and the criterion
is the parent entry's own. It was filed at #1 on the correctness-family
precedent with the explicit note that "unlike the crawler entry that briefly
sat here, no edge mitigation stands behind this one". One now does. That
mitigation is human-dependent -- it works when the owner reads the DM and walks
the list -- so this is a demotion and not a closure, which is what separates
medium from low. Minute-level offsets takes #1 above it on the tiebreak that it
delivers a whole user-visible capability, where what remains here is the
minority half of one: the parent entry itself judged that making the failure
visible was "most of the value".

### 3. Franchise-aware round-label suggestions

Impact: low-medium - effort: small, now that the phrase library exists. Raised:
2026-07-22 (owner, during the phase 2 design discussion, and deferred by him in
the same breath). Buildable as of 2026-07-22, when phase 3 shipped its
prerequisite.

Each franchise names its rounds its own way -- two franchises' campaigns share
almost no phrasing -- so a flat suggestion list is noisier than it needs to be.
The round-label phrase library (phase 3, now shipped -- see Shipped) is the
prerequisite this entry was waiting on, and with it in place ranking its
suggestions by how often a phrase appears on concerts sharing this concert's
FRANCHISE tag falls out nearly for free: the tag is already attached, the
phrases are already counted, and the ordering is one ORDER BY away. Rises now
that its dependency is done -- it is no longer pending, just unbuilt, and it is
the natural next extension of the library rather than a separate feature. The
one caution carried over from when it was deferred: whoever adds the franchise
dimension should check the phrase library's shipped schema stores enough to
count phrases per franchise tag, and extend it there rather than bolting a
second count on the side.

Re-read 2026-08-06 against AI triage phase 2, **rank unchanged** (it moved from
#4 to #3 by the removal above, not on merit), with one real contact recorded so
it is not rediscovered: the completion prompt asks for `label`/`label_en`/
`label_zh` as all three or none, and `import_commit` already calls
`record_round_label_phrase`, which records only a COMPLETE triple. So a
committed AI-completed round now FEEDS this entry's corpus, on the same terms a
hand-typed one does. That grows the data this entry proposes to rank by
franchise -- mildly in its favour -- and adds one caution: a phrase library
fed partly by a model is a library whose counts can be inflated by the model's
own phrasing habits rather than by real editorial usage, so whoever builds the
franchise ranking should look at what the counts actually contain before
trusting the ORDER BY.

### 4. Ten of eleven `RoundKind` members are purely cosmetic

Impact: low (code health, no user-visible change) - effort: medium. Raised:
2026-07-22 (surfaced during i18n phase 2 design and deliberately not acted on).
Counts updated 2026-08-02, when the eleventh member shipped.

Exactly one `RoundKind` member carries behaviour: `UPGRADE`, which drives the
eligibility gate, the suppression exemption, the auto-arm guards, the board
column rank and the capture gating (invariant 2). The other ten differ from
each other in a label string and an emoji and nothing else -- `LOTTERY`,
`FCFS_SALE` and `TOUR_PACKAGE` take identical paths through the planner, the
queue and every read surface. That is worth knowing before anyone adds a
further kind expecting it to mean something, and it is an argument for
collapsing the cosmetic ten into data (a label/emoji table) with `UPGRADE`
kept as the one real branch.

**The prediction in that last sentence was tested on 2026-08-02 and held.**
`GOODS_SALE` shipped as the eleventh member, and its design spec cites this
entry by name to say the new kind is deliberately cosmetic and adds zero
behaviour branches -- so the thing this entry exists to prevent (a kind added
in the belief that a kind means something) did not happen, which is the whole
return on having logged it. The only other change is arithmetic: the label/emoji
table this entry proposes would hold ten rows rather than nine. Rank unchanged,
for the reasons already given above.

Ranked here -- below the user-facing entries above, above the pure-plumbing
ones -- because it is the highest-impact item still standing once the trilingual
arc shipped its user-facing work, but acting on it changes a persisted enum for
zero user-visible benefit, and the taxonomy was corrected as recently as
2026-07-18, so the risk of churning it again outweighs the tidiness. Logged
rather than done, on purpose, so the observation is not rediscovered a third
time.

Re-read 2026-08-06 against AI triage phase 2, **rank unchanged**, and it grew a
THIRD consumer of the taxonomy that whoever collapses it must carry: the
completion prompt enumerates nine of the eleven kinds as literal strings
(`_COMPLETION_SYSTEM_PROMPT`, `domain/round_completion.py`), and its comment
records that this list must match the `RoundKind` values exactly because
`yaml_import._round_kind` silently defaults an unknown one to `other`. That is
new evidence FOR the label/emoji-table shape this entry proposes -- three
places now spell the same set -- and one new caution against a naive
data-driven refactor: this consumer is deliberately NOT the full set
(RESULT_ANNOUNCEMENT and PAYMENT_DEADLINE are withheld from the model on
purpose), so a table generated blindly from the enum would silently re-offer
them.

### 5. In-app LLM extraction on the import page

Impact: low-medium - effort: medium, and ACTIONABLE as of 2026-08-05 (the
budget block lifted). Raised and deliberately deferred 2026-07-22 (owner: no
budget for per-import API calls).

The paste-a-draft seam (`POST /concerts/import/draft`) is producer-agnostic by
design: an agent following the add-concert skill is today's producer, but a
server-side LLM step that turns a pasted event page (or free text) into the same
YAML draft would drop in behind the identical seam with no change to the preview
or to the `import_commit` write path. Deferred not for lack of a place to put it
-- the seam is exactly that place -- but on budget: the owner has no allowance
for a per-import API call, which is the whole reason the import path is
agent-side rather than server-side in the first place. Logged so the seam's
producer-agnosticism is recorded design intent rather than something
rediscovered later. Ranked here by its low-medium impact, above the pure-cosmetic
entries below it, but note it is NOT actionable until the budget question
changes -- the seam being ready does not make this buildable.

Re-reviewed 2026-07-31 (Eventernote discovery) and explicitly UNCHANGED, which
is worth stating because the two entries read as adjacent and are not. That
build was about DISCOVERY -- finding out an event exists -- and used no LLM at
any point, deliberately: the deploy has no API access, so "no LLM anywhere" was
a hard constraint on its design rather than a preference. This entry is about
EXTRACTION -- turning a page into a filled draft -- and is blocked on the same
budget it always was. Discovery neither unblocks it nor makes it cheaper; it
does sharpen the case, since there is now a steady stream of leads whose drafts
somebody still has to author by hand or by agent. Rank unchanged apart from the
renumber.

**The budget block LIFTED on 2026-08-05**, which is the sentence this entry
spent a fortnight waiting for: the owner bought DeepSeek V4 Flash credits
(~$0.088/M in, ~$0.176/M out), and AI triage phase 1 shipped that day and spent
them -- a `deepseek_api_key`, an `app/llm.py`, and a working precedent that an
LLM's output can cross this app's existing parser boundaries rather than earn a
new one. So the "NOT actionable until the budget question changes" rider above
is retired; what is left here is genuinely buildable work.

What is left is also SMALLER than the entry as written, because the owner chose
a different first target and phase 1 took the discovery-lead half with it. This
entry is now the IMPORT PAGE alone: paste an event page (or free text) into
`/concerts/import` and get the same YAML draft an agent would have written,
behind the identical `POST /concerts/import/draft` seam. The seam is unchanged
by phase 1 and needed no change to serve it -- phase 1 writes `PendingDraft`
rows through the batch path, which is the same queue and the same
`import_commit` write path this would feed. Two things phase 1 proved that this
build should simply reuse rather than re-decide: the model emits the app's own
draft vocabulary and dies at `parse_drafts` when it is wrong, and rounds are
stripped in code rather than trusted from a prompt (whether an import-page
extraction may emit rounds is exactly the question the phase-2 entry owns, and
this entry should NOT answer it independently).

Rank unchanged at low-medium impact apart from the renumber, and that is
deliberate: unblocking is not impact. It stays below the phase-2 entry because
it automates a step the owner already has an agent for, on a page he visits
deliberately, and below the user-facing entries above it for the reason it
always was.

Re-read 2026-08-06 against AI triage phase 2, and this is the one entry that
build genuinely MOVED -- up one place, to #5, above PWA. Both still read
low-medium impact, so the swap is on the effort tiebreak this file already uses
(it is the same tiebreak that once put phase 2 itself below minute-level
offsets), and phase 2 changed both halves of that tiebreak at once. **The one
design question this entry deferred to phase 2 is now ANSWERED**: "whether an
import-page extraction may emit rounds" was explicitly not this entry's to
decide, and the answer shipped -- yes, under evidence grounding, in code, with
every rejection reported. And the plumbing an import-page extraction would need
now exists and is proven in production shape: `domain/page_text.py` turns a
page into the one text a model reads and a checker searches, `app/llm.py` is
called from a web route (the paste fallback) as well as from the scheduler, and
`import_preview.html` already renders a model's proposals with their evidence.
What is left is genuinely the small half -- a prompt, a route, and the decision
about what a whole-draft extraction may claim beyond rounds. Impact is
unchanged and unblocking is still not impact; what moved is that this is now
the cheapest remaining item of its impact class rather than the dearest.

Re-read 2026-08-08 against the agent read API, **rank unchanged**, with one
real contact worth recording: this entry's design question was always "how
does a page become a draft", and the read API answers a DIFFERENT question
this entry never had to ("do I already have this concert"). The design doc
that shipped it names the same friction this feature would eventually hit --
"the agent cannot see the catalogue... re-proposes duplicates" -- and now
`GET /api/v1/concerts?q=` is a call away, whether an in-app extraction step
ends up reusing `api_concert_rows` directly or the agent-side skills reach
for it instead (see the new Proposed entry on teaching the skills to use
these endpoints). Whoever eventually builds this should check the catalogue
via that path before drafting rather than re-inventing a dedup query. Impact
and rank unchanged; the dedup half of the eventual build just got an answer.

### 6. Four long jobs share the reminder tick

Impact: low-medium -- no user-visible change today; it removes a false-alarm
source and a latent outage class - effort: medium. Raised: 2026-08-06 (owner
asked whether a more traditional infrastructure stack would help performance or
maintenance; the answer was no, with this as the single exception).

One asyncio loop runs discord.py, FastAPI and the 60-second scheduler tick.
Four long jobs now share that tick -- the Eventernote sweep, AI triage, AI
draft completion and, since 2026-08-13, the round poll (title updated from
"three" on that date; the revision-pass note at the end of Proposed says why
the round poll counts and round watch did not) -- each with a
240s wall clock checked only at the TOP of its loop, so each can overshoot by
one whole iteration. For completion that
iteration is worst-case ~151s (`COMPLETION_DELAY_SECONDS` 1 +
`FETCH_DEADLINE_SECONDS` 30 + `llm.LLM_TIMEOUT_SECONDS` 120), putting a run at
~390s against `heartbeat.MAX_AGE_SECONDS` of 180.

`heartbeat.beat()` per item is what keeps `/healthz` honest through that, and
the beat is honest -- the loop genuinely is alive. But the mechanism exists to
paper over the coupling rather than remove it, and every long job added since
has had to remember it. Reminder DELIVERY is not starved (it runs first in
`tick()`), so today's cost is the health signal plus a latent risk.

**The latent risk is real and nearly landed once.** Phase 2's fetch policy was
first written with a synchronous `socket.getaddrinfo`. On any other stack that
is merely a slow function; here it would have frozen Discord, the web app and
the scheduler together for the resolver timeout, up to fifteen times per press.
A review caught it and it now resolves off the loop -- but the class of bug
exists only because of the shared loop, and the next one may be less visible.

The fix is not a stack migration. A second asyncio task with its own budget, or
a second systemd unit, decouples the thing that is actually coupled.
**Sequencing matters and is worth recording before anyone starts:** splitting
into two PROCESSES is the first point where SQLite begins to matter, because
there would then be two writers. WAL plus `busy_timeout=5000` handles that at
this write volume, but it is the first place Postgres would earn its keep. The
order is split first, adopt Postgres only if the split makes writes contend --
not the other way round.

**What the same assessment ruled OUT, recorded so it is not re-proposed without
new evidence:** Postgres on its own (the dev DB is 299 KB; there is no database
performance problem and will not be for years), containers or orchestration,
and a separate queue with a broker. The one outage on record was crawler-driven
CPU exhaustion of the Lightsail burst credits, which a bigger stack would have
ABSORBED at permanent cost rather than prevented -- the actual fix was
`rel="nofollow"`, robots.txt and a Cloudflare Managed Challenge, and it was
free. Nor would any of it have prevented a single one of the dozen real defects
the phase-2 reviews caught: those were domain-logic and seam bugs, and what
caught them was the invariants in CLAUDE.md and the review discipline.

Ranked above PWA because it prevents a failure that has nearly happened, and
below in-app LLM extraction because that entry delivers a capability and this
one delivers none. One caveat on the whole assessment: it reasons from the code
and the single documented outage, not from production metrics -- no response
times, request volume or real database size were consulted, and latency creep
under ordinary traffic would change the picture.

Re-read 2026-08-11 against round watch, **rank unchanged**, and the reason is
worth stating because the headline reads like a hit on this entry and is not.
The tick gained a FOURTH job, and unlike the three above it, that one runs on
EVERY tick with no cadence clock at all. It is nonetheless not a fourth LONG
job, and the distinction is this entry's whole subject: the three named above
are long because each does third-party I/O in a loop (86 sequential fetches, or
15 fetch+LLM pairs at up to ~151s an iteration). Round watch does one local
`select(Concert)` with two `selectinload`s over ~157 rows plus a Python diff --
no network, no model call, no wall clock, and nothing that could approach
`heartbeat.MAX_AGE_SECONDS`. It therefore adds no overshoot risk and needs no
`beat()` inside itself. What it DOES add is one more caller that had to know
about the shared loop and the try/except/commit-per-block convention, which is
this entry's secondary complaint ("every long job added since has had to
remember it") reaching a SHORT job too. Held here as evidence for the entry,
not as a reason to move it.

### 7. PWA / installability

Impact: low-medium - effort: medium. Raised: 2026-07-21 (mobile-view
build).

The phone retrofit (tab bar, FAB, bottom sheets) makes the site read like
an app on a phone browser, but there is still no way to install it as one:
no manifest, no service worker, no "Add to Home Screen" affordance. A
manifest.json (name, icons, `display: standalone`, theme color matching
the token layer) plus a minimal service worker would let a phone user add
a real home-screen icon and launch into a browser-chrome-free window --
the natural next step after this build, not a prerequisite for it. Impact
stays low-medium rather than higher until it's paired with something a
plain browser tab can't do (push notifications would need this shipped
first, since web push requires a service worker; DM-notification parity
for phone users who don't want the Discord app open is the case that would
raise this). Effort is medium: the manifest and icons are small, but a
correct service worker (cache strategy, update flow, avoiding the classic
"stale offline shell" trap) is not.

Annotated 2026-08-04 (calendar-feed story), rank unchanged either way: the
subscription feed is now this app's SECOND surface that works with the site
closed -- the Discord DM was the first -- and a phone that has subscribed to it
already shows the user's shows and live deadlines in the OS calendar with no
tab open and no install. That neither raises this entry (a calendar is not a
notification, and none of the manifest/service-worker work got cheaper) nor
lowers it, but the push-notification argument above should now cite it as prior
art: the "DM-notification parity for phone users who don't want the Discord app
open" case has to clear a bar the feed already meets for anything the user can
read AHEAD of time, so what web push would actually buy is the interrupting
half -- the moment itself -- and that is the case this entry should be argued
on when someone picks it up.

Re-read 2026-08-06 against AI triage phase 2 and **displaced to #6** by the
import-extraction entry above -- on that entry's merit (its one blocking
question answered, its plumbing shipped), never on this one's, which is
untouched in substance: nothing in that build went near a manifest, a service
worker or web push, and none of the work this entry describes got cheaper. The
push-notification argument recorded above is also unchanged by it -- an
AI-completed round is a deadline that reaches the user through the SAME
channels as any other, so it raises the value of the interrupting half without
altering what web push would have to build.

### 8. The event classes outside concerts and talk shows

Impact: low (by owner ruling) - effort: varies sharply per class. Raised:
2026-08-02, filed by the scope ruling rather than proposed on merit.

`docs/discovery-lead-taxonomy-2026-08-01.md` sorted 443 real leads into seven
classes. Two are catalogued; these are the rest, kept here so that reopening one
is a decision rather than a rediscovery. They are NOT uniform, and lumping them
into one "support more event types" task would hide that:

- **Fan meetings and birthday events need NO code at all.** FC lotteries are the
  app's core case; 伊達さゆり Fan Meeting Tour is a two-city, four-leg concert in
  every respect that matters. This is purely a decision about what belongs in
  the catalogue, which is why it is in this entry rather than being work.
- **Festivals fit, awkwardly.** TIF, @JAM EXPO, ANIMAX MUSIX. The concert is the
  festival, not the artist, so one bill carries dozens of performers and the
  lead arrives via whichever one is followed. Ticketing is its own shape (day
  tickets, two-day passes). Catalogueable today; the tag-attachment question is
  genuinely different from every other class.
- **Stage runs need the A/B cast gap closed first** (the A/B casts entry
  directly below) and stress the leg
  count -- スクールアイドルミュージカル is thirteen performances across eight days.
  Every surface that renders legs was built against two-to-four.
- **Release events may not be expressible at all.** 発売記念 / お渡し会 / 特典会
  have no lottery and no deadline: you buy the product and the slot comes with
  it. Cataloguing them would need a purchase-window concept the app does not
  have and has never needed. This is the one where "we decided not to" and "we
  cannot" are close together.

Re-read 2026-08-06 against AI draft completion, **rank unchanged and for the
same reason it was filed**: this entry is a SCOPE decision, and no amount of
cheaper round research changes what belongs in the catalogue. One class does
move a little, and only in effort: festivals were called "catalogueable today"
with their own ticketing shape (day tickets, two-day passes), which is exactly
the kind of multi-round ladder that used to mean reading a long ticket page by
hand -- phase 2 makes that half cheap. The tag-attachment question, which is
this class's actual difficulty, is untouched. Release events stay the honest
"may not be expressible at all": a completion pass that finds no deadline
because there is none is not progress on them.

### 9. A/B casts have nowhere to live

Impact: low (descoped by consequence) - effort: small-to-medium, mostly design.
Raised: 2026-08-01 (taxonomy read); filed 2026-08-02 by the scope ruling.

ミュージカル信長 runs `9月19日17:30公演(A)` and `9月19日12:30公演(B)` -- same day,
same venue, different cast. Nothing in the schema carries that. The two would
become legs distinguished only by their free-text labels, which RENDERS fine and
breaks the thing that matters: `RoundOutcomeDay` is per performance, and here a
performance's identity is the CAST rather than the time, so a user holding an (A)
ticket cannot say which one they hold.

**Descoped, not solved.** It exists only inside stage runs, and stage runs are
out of scope -- ミュージカル信長 is the sole production among all 443 leads with a
cast split, which is also why this was worth checking rather than assuming.
Ranked low for exactly that reason and for no other: it is a real gap, and if
stage runs ever come back (the out-of-scope event classes entry directly above)
this is their prerequisite.

Do not let a triage skill paper over it with a label convention in the meantime.
A convention that encodes cast in free text would look like support and would
still leave the outcome unrecordable, which is worse than the honest gap.

Re-read 2026-08-06 against AI draft completion, **rank unchanged**, and the
warning in the paragraph above now has a second audience worth naming: a round's
`applies_to` binds to leg LABELS as free text, and phase 2's evidence checker
rejects a round naming a label the draft lacks. So a model is now a producer of
leg-label-shaped identity too, and a cast convention smuggled into a label would
be reproduced by it as readily as by a skill. Same verdict either way -- the gap
stays honest until stage runs come back into scope.

### 10. Discover sort in the content head, plus the catalogue-count note

Impact: low - effort: small. Raised: 2026-07-20 (demo-reconciliation
re-review).

The reconciliation gave Discover's sort and round-status controls the
demo's pill chrome but left them in the sidebar; the demo puts the sort
control in the content-column head. Also missing: the demo's per-filter
result note above the grid ("Love Live! - 64 concerts"). The page-level
catalogue count ("N concerts - M with a round still open") shipped in the
reconciliation, but not this filtered one. Deferred rather than done because
moving sort is a real DOM restructure and it is debatable whether the
sidebar is actually worse.

Re-reviewed 2026-07-20 (i18n build): the per-filter result note is new
user-visible copy with an embedded count, so whoever builds this owes both
catalogues an `ngettext`-shaped entry (singular/plural), not just an
English string, on top of the DOM work already scoped.

Re-reviewed 2026-07-21 (mobile-view build): the sidebar controls now also
render inside `.fsheet`, the phone filter sheet (`order: -1` above 760px,
a "Filters" summary/button below it, tracking `.layout`'s own 760px
collapse point) -- any future move of sort into the content head must
carry the fsheet's relocated copy along with it, not just the desktop
sidebar's, or the two surfaces drift.

Re-reviewed 2026-08-04 (crawler-trap hardening): Discover's sort links now
carry `rel="nofollow"` (part of the ten-site sweep the outage's crawl-trap
fix applied), so any future move of sort into the content head must keep
that attribute on the rebuilt link too -- the sweep test in
`tests/test_discover.py` will catch a drop, but it belongs here per this
file's own discipline of naming every contact a shipped entry makes.

Re-read 2026-08-06 against AI draft completion: **no contact at all, rank
unchanged.** That build touched the import/pending surfaces, the fetch guard
and an admin page, and never went near Discover, its sidebar, the filter sheet
or the catalogue counts. Recorded only because this file's discipline is that a
re-read leaves a mark whether or not it found anything.

### 11. Name the destination on the sign-in bounce

Impact: low - effort: small. Raised: 2026-07-21 (signed-out redirect build).

The note on Home when a visitor is bounced off a signed-in-only page reads
"Sign in to continue to the page you asked for." -- deliberately vague,
because the alternative considered at build time was interpolating the
`next` path into the sentence, and echoing an arbitrary attacker-suppliable
URL back into the page is a phishing-adjacent surface not worth opening for
a cosmetic gain. Naming it properly needs a path-to-label map (`/preferences`
-> "Preferences", `/setup` -> "First-time setup", a concert path -> that
concert's title), which is a real little feature: a lookup that degrades
gracefully for paths it doesn't know, plus both catalogues for every label.
Worth doing only if the vague sentence actually reads as confusing in use --
it is the kind of thing to leave until someone says "continue to *what*".

Ranked below the demo-parity cosmetics batch and the Discover-head entry
because those close several visible gaps each; this refines one sentence that
is already correct. (Named rather than numbered as of 2026-07-29: this
pointer has been bumped by renumbering in five separate passes, which is
five chances to get it wrong for no gain.) **Half that pointer went away on
2026-08-12** when the demo-parity cosmetics batch shipped; the ranking argument
now rests on the Discover-head entry alone, and it still holds -- that one still
closes several visible gaps to this one's single sentence. The batch is not
re-read as a comparison because it no longer exists to compare against.

Re-read 2026-08-06 against AI draft completion: **no contact, rank unchanged.**
Nothing in that build touches `safe_next`, the bounce copy or the landing page.
Worth one line anyway, since the entry's own reasoning is about echoing
attacker-suppliable text into a page: this build added a new class of
model-supplied free text to the app (quotes, rejection reasons), and all of it
renders through Jinja's escaping as ordinary content, never into an `on*`
handler or an inline script (invariant 7). Different surface, same rule,
already followed.

### 12. The calendar roster's blind spots

Impact: low - effort: one half is trivial, the other is a design change.
Raised: 2026-08-03, filed by the calendar-discovery build's own probe rather
than proposed on merit.

`include_prefixes` matches with `str.startswith`, and the live probe of all nine
feeds found two things that rule cannot reach. They are recorded together
because they are the same sentence -- "the roster sees fewer rounds than the
feeds carry" -- and apart because the fixes have nothing in common.

- **The LL-Fans MAIN calendar's ticket rows are simply not read.** The roster
  takes that feed as an EVENT source (`dates_are="event"`, performance
  prefixes), because that is what it mostly is -- but it also carries the
  series-wide rows the per-group subs cannot: フェス-scale campaigns belonging to
  no single group, plus a scatter of 先行 and 一般発売. A SECOND roster entry
  (`ll-main-tix`, the same URL, `dates_are="deadline"`, `TICKET_PREFIXES`)
  closes that with DATA ONLY -- no logic, no migration, no test beyond the
  roster's own. One feed read twice under two `dates_are` is not a hack; it is
  what per-feed date semantics buys you, and it is the cheapest coverage this
  app can currently add. The reason it did not ship inside the build is that
  its value is unmeasured: nobody has yet seen a real フェス lead go missing.
- **Promoter-named rounds are unreachable by prefix, and always will be.**
  Every ticket agency names its own round -- ll-liella alone produced 24
  distinct heads in twelve months (オフィシャル5次先行, Liella! CLUB先行,
  ファミリーマート先行, いち早プレリザーブ先行) and ll-musical's round rows are
  ALL promoter-named (イープラス / チケットぴあ / ローソンチケット). A name list rots
  the week a new agency appears, so `TICKET_PREFIXES` deliberately holds only
  the generic, stable Japanese ticketing terms. Fixing it properly means a
  `contains`/regex matcher, and that drags a real design change behind it: a
  per-ENTRY deadline flag, since a feed matched by content is no longer a feed
  whose every row means the same thing, and the per-FEED `dates_are` cannot
  express that. Logged, not built.

**The accepted argument for living with both is that a missed round is not a
missed concert**: a campaign is already a lead through its FIRST round (最速先行
/ 一般発売 are generic and caught), and triage verifies every round against the
official page anyway. Raise this if a real campaign is ever found that the
roster saw nothing of at all -- that is the failure this entry is watching for,
not "we saw three of its four rounds".

**Rated `low` rather than low-medium, which is why it sits below the `low`
entries above and not among them.** It was drafted low-medium on the strength of
"a user could miss a deadline", and that is the same claim the paragraph above
refutes: the campaign is already a lead, so what is lost is a second pointer to
something already visible, not the concert. A rating that contradicts its own
entry is worse than a cautious one, and this list orders by USER impact.

Re-read 2026-08-06 against AI draft completion, **rank unchanged, and its
accepted argument is now backed by shipped code rather than by an expectation.**
The paragraph above lives on "triage verifies every round against the official
page anyway" -- which was aspirational when it was written, since phase 1
stripped every round it was given. Phase 2 is that verification, and it is
stricter than the sentence promised: a round survives only when the model can
quote the official page for each timestamp and the app can find that quote
carrying that timestamp. So a campaign that reaches the catalogue through its
FIRST generic round now gets its remaining, promoter-named rounds filled from
the official page rather than from a feed prefix that will never match them.
That does not close either blind spot -- the roster still sees what it sees --
but it does mean the second half of this entry ("promoter-named rounds are
unreachable by prefix, and always will be") is now routed around rather than
merely tolerated, which is a further argument for leaving it at `low`.

### 13. Two narrow edges outcome correction left open

Impact: low (both are rare, and one is copy rather than state) - effort: small
for each, and they are independent. Raised: 2026-08-11 (outcome-correction
build, Task 5 review; both deferred as minors at the time rather than widened
into that branch).

Two places where the app states something about the reader's own record that is
not true. They are filed together because they share a root -- the flat
`WonButton`/`LostButton` pair and the terminal DM replies are ROUND-level
statements about a model that has been per-DAY since 2026-07-27 -- not because
one fix covers both.

**(a) A flat "Won" press can leave a round WON while it secures nothing.**
Pre-existing, and NOT introduced by outcome correction, which only put a guard
beside it: `dk:won` on a round whose day rows are all LOST writes round-level
WON with zero WON day rows, so `secured_day_ids_by_round` reports nothing
secured while the payment reminder arms off the round. The same family as the
`refuse_if_secured` guard that build added, one level down -- that guard refuses
to OVERWRITE a secured round, and this is a press that CREATES an unsecured one.
Narrow by construction: the flat pair is only ever composed onto single-leg
rounds, and only when the DM was built while the round was unanswered.

**(b) The one terminal reply where the backtrack truthfully does nothing.**
"Nothing left to record for this one." is also reached by opting OUT of every
leg. `clear_round_outcome` never touches `LegOptOut` -- correctly, since an
opt-out is not an outcome (invariant 8) -- so pressing "Change my answer" there
deletes nothing, replies "Cleared — you can record this round again", and the
round stays suppressed. Purely copy: the state is right and the sentence is
wrong. The honest reply names the opt-out and points at "Going again" on the
concert page, which is the real way back.

Ranked here rather than higher on RARITY, the same ground the 2026-07-29
correctness sweep used to defer an entry it agreed was real; ranked above the
admin-only entries below on the plain tiebreak that a user-facing wrong state
outranks an admin inconvenience. Neither is worth a branch of its own -- (b) in
particular is one string and one condition, and belongs in whatever next touches
`_progress_reply`.

### 14. Nothing caps the discovery review path

Impact: low (admin-only) - effort: small. Raised: 2026-07-31 (Eventernote
discovery, Task 7 review; deferred as a minor at the time).

`open_leads` has no LIMIT, `/admin/discoveries` renders every row it returns,
and `copy_text` is emitted TWICE per page -- once in a `data-copy` attribute for
the copy button and once in the visible `<pre>`. A first sweep produces a lead
for every future event of all 86 tags at once, most of them duplicating concerts
already held, so a few hundred rows is the expected first-day case rather than a
pathological one: roughly a 150KB admin page. Survivable, admin-only, and it
degrades rather than breaks -- but it is unbounded, and the page it happens on is
the one the DM's "+N more" line exists to send you to, which is precisely the
occasion when the backlog is largest.

The fix is not obvious enough to call small-and-done: a LIMIT plus paging is the
usual answer, but the page's whole job is BULK triage and the copy block is
supposed to cover what you can see, so paging the rows means deciding what the
block covers. Emitting the block once and reading it from the `<pre>` is the
cheap half and can be done independently.

Ranked here, below every user-facing entry above it, because this list orders by
USER impact and this one's is nil -- the same argument that kept the rehearsal
harness low until it was ranked on a borrowed claim. Raise it if the first
production sweep actually produces a page somebody has to fight.

Re-read 2026-08-03 against calendar discovery, which adds a second producer to
the page this entry is about, and **unchanged in rank** -- the arithmetic is
recorded here so nobody has to redo it. The new leads are BOUNDED in a way the
Eventernote ones are not: nine feeds, filtered to ticket and performance rows,
each capped by its own forward window, against 86 actor pages whose first sweep
emits every future event they list. Against a first-day case already measured in
the hundreds of rows, the feeds are a rounding error. What genuinely changed is
smaller and worth saying: the page's rows are no longer homogeneous (a calendar
row has no Eventernote link and may carry a `申込締切` date), so whatever paging
or truncation eventually lands here must not assume every row is the same shape
or that the copy block can be reconstructed from ids alone. The one HALF of the
fix this entry already calls cheap -- emitting `copy_text` once instead of twice
-- is unaffected by any of it and is still the thing to do first.

Re-read 2026-08-05 against AI triage phase 1, the build most likely to have
moved this entry, and **unchanged in rank** -- but the review-volume story it
was filed on has genuinely changed shape, so the reasoning is recorded here
rather than left to be redone. Triage does not shrink the page: it dismisses
nothing itself, so every lead it proposes to prune is still rendered until the
owner walks the plan/apply screen. What it changes is how LONG the largest
backlog lives -- the 528-lead state that made this entry's 150KB estimate real
is now a few button presses from resolved instead of an evening of agent work
-- and how much the copy block matters, since the block exists to feed an agent
and the classify half of that agent now runs server-side. The block is NOT
obsolete (calendar-sourced survivors have no Eventernote page to draft from,
and everything past the 25-draft cap still goes to the agent), so nothing here
argues for deleting it. Two smaller facts for whoever picks this up: the page
grew a status strip and a prefilled prune link, which are bounded and tiny
beside the row list, and the cheap half of the fix -- emit `copy_text` once --
is still untouched by all of it and is still first.

Re-read 2026-08-06 against AI draft completion, **rank unchanged**, and the
page this entry is about was not touched at all -- phase 2's surfaces are
`/concerts/import/pending`, the draft preview and `/admin/fetch-domains`. One
adjacency worth naming so nobody re-derives it: `/concerts/import/pending`
renders every open draft with no LIMIT either, exactly as `/admin/discoveries`
does, and the completion button now gives an admin a reason to sit on that page
repeatedly. Its ceiling is far lower (a batch is fifty-to-a-hundred drafts, and
committing or discarding one removes it, whereas a lead lingers until dismissed
through the plan/apply screen), so it does not deserve an entry of its own --
but if this one is ever picked up, the two pages want the same answer, and the
cheap half of the fix here has an exact twin there.

Re-read 2026-08-11 against round watch, **rank unchanged**, with one exact twin
added. `/admin/quiet-ladders` is a THIRD admin worklist with no LIMIT, and it
emits its copy block TWICE for the same reason `/admin/discoveries` does --
once in `data-copy` for the button, once in the visible `<pre>` -- so the cheap
half of the fix this entry has been naming since 2026-07-31 now has two twins
rather than one, and whoever finally does it should do all three in one pass.
The urgency does not move, and the arithmetic is recorded so nobody re-derives
it: this page's row count is bounded by the CATALOGUE (~157 productions, of
which only the quiet subset renders) and it DRAINS ITSELF -- the leg clause
stops matching the day after a show, so rows leave without anybody dismissing
them. That is the opposite of the discovery queue's unbounded growth, which is
the property that made this entry worth filing.

### 15. Nothing notices a calendar feed going quiet

Impact: nil for users, real for the catalogue - effort: small. Raised:
2026-08-03 (calendar-discovery build; the design doc listed per-feed health as
out of scope and this is that decision, written down where it can be revisited).

A feed that stops being maintained does not fail. It fetches 200, parses clean,
and simply stops carrying future rows -- so the sweep reports success forever
and the franchise it covered quietly goes dark. The precedent is not
hypothetical: imas-db's previous main event calendar shut down in 2025-03, which
is exactly why the design doc calls volunteer feeds a first-line source rather
than a guarantee. Today the only signal is the failed-fetch count in the digest
line, and silence is not a failure.

**There is already one live case to check.** `imas-tix` -- the im@s half of the
whole feature -- served ZERO future rows when it was probed on 2026-08-03, and
was kept anyway on liveness evidence rather than dropped: DTSTAMP was that day,
its newest LAST-MODIFIED was the day before, and July alone carried seven
entries. A DEADLINE calendar empties its own forward window by construction --
every entry is a date that passes -- so an empty morning is a lull, not rot, and
dropping it on a one-day sample would have deleted half the feature. That
reasoning is sound and it is also unfalsifiable by inspection, so it comes with a
date: **re-check around 2026-09-03. If it is still empty, that is rot, and the
entry to open is this one.** No ticket exists for the re-check anywhere else;
this paragraph is it.

The cheap version is a per-feed "last future row seen" line on
`/admin/discoveries` -- no alerting, no thresholds, just the number that makes
silence visible. Anything more (a stale-feed ops alert) is over-built for nine
feeds one person maintains.

One measurement to take at the same time, recorded here rather than in a report:
the nine feeds are parsed SYNCHRONOUSLY on the shared asyncio loop, and the
largest is 1.41 MB and grows weekly. `heartbeat.beat()` per feed keeps
`/healthz` honest about the FETCHES, but a pure-Python parse holds the loop for
its whole duration and nothing beats inside one. Fine at today's sizes and
measured as such; if the tick ever visibly blocks, the parse is the first place
to look and a thread offload is the obvious answer.

Re-read 2026-08-06 against AI draft completion, **rank unchanged**, with the
2026-09-03 `imas-tix` re-check still standing and still owned by this entry
alone. One genuine contact: the loop-blocking measurement recorded above has a
precedent now. That build's review found a synchronous `getaddrinfo` on the
shared loop and moved it to `asyncio.to_thread` under a total deadline
(`fetching._resolve_async`), so the "if the tick ever visibly blocks, a thread
offload is the obvious answer" line is no longer a hypothesis about this
codebase -- it is a pattern with a working instance to copy. Nothing else here
moves; a feed going quiet is still invisible.

(The former "`/admin/discoveries` row height wants a real viewport" entry
(2026-07-31) was closed the same day by measuring it -- see its Shipped entry.
The measurement moved the answer: the hint banner was not the cause.)

Re-read 2026-08-11 against round watch, **rank unchanged** (its impact is still
nil for users), and the 2026-09-03 `imas-tix` re-check still stands and is
still owned by this entry alone. The EFFORT, though, drops from "small" to
"copy the pass next door": round watch is a working, shipped instance of
exactly this entry's shape -- notice something has gone quiet, stamp when it
first did, DM the admins once. Every question this entry would have had to
answer from scratch is now settled in code with its reasoning written down
(`db/quiet_ladders.py`, and its entry in `docs/architecture.md`): a
system-owned "first observed quiet" stamp with a migration backfill, so the
first pass after deploy is silent instead of announcing the whole back
catalogue; a self-idempotent reconcile, which is what lets it run every tick
with no cadence clock; the stamps and the queued notice committed in ONE
transaction, which is what makes the notice exactly-once; silence as the normal
output; and the `UNREPORTED_NOTE_KINDS` question already ruled (a notice that
reports on a SOURCE rather than on deliveries is an ordinary notice and belongs
in `delivery_log` like any other). A per-feed "last future row seen" line is
still the right first version; what changed is that going further is no longer
over-built, because the machinery to copy already exists.

### 16. `db/core.py` is one mutually-recursive 4,000-line component

Impact: nil for users, real for anyone changing the reminder engine - effort:
large. Raised: 2026-08-07 (the service.py split; this is the part that
deliberately did NOT move, written down so the next person does not rediscover
why).

The 2026-08-07 pass took `db/service.py` from 8,063 lines to a 556-line facade
plus thirteen feature modules. `core.py` kept ~4,000 of those lines, and not
for lack of appetite: its nine sections -- queue sync, retrieval, the personal
board, the concert page's rounds-by-leg, Discover status, presets and
subscriptions, DM button actions, users, adapters -- form ONE strongly-connected
component in the call graph. That was measured with an AST pass, not estimated.
The thirteen modules that did move were acyclic, which is exactly why moving
them was safe to do mechanically.

So there is no cut through `core.py` that yields modules importing in one
direction, and any file-move split would buy smaller files at the price of
import cycles -- the specific fragility the facade exists to avoid, since a
cycle here surfaces or not depending on which module a process imports first.

Splitting it needs a DESIGN change rather than a reorganisation. The most
promising is the `reminder_queue` inversion (see the review that produced this
entry): stop materialising planned reminders, keep a `sent` ledger keyed on
(rule, round, day, anchor, fire_at) and compute due rows on read. That deletes
invariant 2's 21 `sync_*` call sites, and with them most of the mutual
recursion -- the suppression passes exist to filter CANDIDATES before planning
precisely because a materialised table has to stay honest. Worth doing on its
own merits; the file size is a symptom, not the reason.

Do not attempt this as a tidy-up. The engine is the product.

### 17. The demos are one grammar-migration behind the app

Impact: nil for users, real for anyone doing UI work against the design source
of truth - effort: medium, and it wants its own bounded pass rather than being
folded into whatever polish job trips over it next. Raised: 2026-08-12
(demo-parity build, deferred OUT of it deliberately -- a polish pass should not
become a demo migration).

`CLAUDE.md` and `docs/architecture.md` both name `docs/superpowers/demo/` the
**design source of truth**. It is one, and it is also stale in a specific,
nameable way: the app adopted the two-shape callout grammar (G2) on 2026-07-24
and the demos never did. They still carry pre-G2 shapes alongside the current
ones, and in one case they carry a pre-G2 shape under a post-G2 NAME.

**The instance that cost this branch twice.** Both `dekimasen-demo.html`
(line ~86) and `dekimasen-onboarding-demo.html` (line ~87) define a `.banner`
that is a **top concept-bar** -- the strip under the header explaining what the
mockup is. The app's `.banner` is the G2 needs-attention callout (wash ground,
full border) that absorbed the old `.callout(+warn)`, `.banner-warn` and
`.signin-note`. Same class name, entirely different component. Two separate
tasks on the demo-parity branch each needed the app's shape, and each had to add
a compound `.banner.warn` rule re-declaring **every property the concept-bar
rule sets**, purely to stop it bleeding through. That workaround holds only for
properties BOTH rules know about: a property added to the concept-bar rule
later, with no counterpart in the compound, leaks straight into the callout.
Both files now carry a comment saying exactly that -- which is a warning
standing where a fix belongs.

**Smaller instances of the same family**, every one found by tripping over it:

- the demo's `.memb` lacks the `align-items: center` that `style.css:1365` has
  (spotted by the split-pill task, left alone as out of scope);
- `.visually-hidden`, `.tiny`, `.dim` and `.edgecard` were each absent from one
  or both demo files, and were ported piecemeal by **four different tasks** of
  one nine-task branch -- four separate discoveries of the same underlying fact,
  which is the tell that the fact is structural and not a list of oversights.
  Two of the four are still one-sided today: `.visually-hidden` exists only in
  `dekimasen-demo.html` and `.edgecard` only in `dekimasen-onboarding-demo.html`,
  because each was added where a task happened to need it.

**What a real pass looks like:** rename the concept-bar to something that is not
a G2 class name, delete the compound workarounds, port the G2
`.banner`/`.edgecard` pair verbatim from `style.css` into both files, and diff
the demos' shared utility classes against the stylesheet ONCE rather than one
class at a time. That last step is what makes this a migration rather than a
fix, and it is why the effort reads medium rather than small.

**Ranked #17, and the honest case against ranking it higher is that this is a
documentation-quality problem with no user-visible impact whatever.** Nothing
renders wrong for anyone; each demo is self-consistent and looks right. It sits
above the agent-skills entry beneath it because that one is convenience for a
workflow that already works, while this one actively misleads -- a reader who
takes the demo at its word about `.banner` learns a component that does not
exist. It sits below `db/core.py` (#16) because a stale reference costs an
assumption a reader can overturn by opening `style.css`, whereas that entry's
shape constrains the engine, and the engine is the product. And it goes no
higher than this band because the cost, while real and now twice-paid, is
bounded and SELF-ANNOUNCING: every instance above was found by the task that
reached for the class, within minutes and before any of it reached the app.
That is the cheapest failure mode this file tracks, which is exactly why it
belongs down here rather than anywhere it would displace work someone is
waiting on.

**A worklist for whoever takes this**, gathered by the demo-parity build's final
two review passes and recorded here so it is not re-derived. None of it blocked
that merge; all of it is the same drift.

The one with actual parity consequences: **the editor frame's Tags fold BODY is
a branch behind its own hint.** The hint now says 15 performers while the body
enumerates 9 chips, with no Characters row and no 765PRO ALLSTARS / 竜宮小町
group chips -- on a demo whose concert frame directly above carries the full
roster. The app's picker demonstrably HAS that row
(`_tag_picker_fields.html:27`, "Characters (their voice actors are added for you
when you save)"), so its absence is a real gap on the very surface character
tags shipped through. The hint's SHAPE is not app-producible either:
`concert_edit.html:143` renders `tag_summary[:6] | join(' · ')` -- tag NAMES,
never "N performers · Kanto".

The rest are smaller and all pre-existing: the round-kind select offers a
five-item invented list where `LABEL_BY_ROUND_KIND` has eleven and names them
differently ("Lottery round", "Eligibility item sale", not "Lottery"); leg 2's
先行抽選 R2 shows a `—` pill where `leg_status_pill` falls through to
`status_pill(PAID)` -> "Secured", and leg 1's says "Paid" where
`_round_rows.html:29` renders "Secured"; a single `opens_at_utc` renders as
"Opens Wed 30 Jul" on one leg and "Won 18 Jul" on the other; the editor's time
labels say "Opens (JST)" where the app says "Apply opens (JST)"; and the demo's
tile off-state opacity is `.38/.62` against the app's `.4/.65`, with the demo's
rules unscoped where the app scopes under `.pick`.

Worth noting what this list is evidence FOR, since it is the argument for doing
the migration as one bounded pass rather than opportunistically: every item was
found by a reviewer checking a demo string against the app's source, and none of
them was found by anyone reading the demo. A reference nobody can check by
reading is exactly the failure this entry describes.

### 18. Teach `add-concert` / `triage-leads` to use the agent read API

Impact: nil for tracked users, real for the owner's own workflow -- effort:
small (skill-file prompt/instruction edits; no app code, the API already
exists). Raised: 2026-08-08, one of three follow-ups the agent read API's own
design doc recorded rather than scheduled.

The read API shipped the same day this entry was filed, and it closes the
loop for a PROGRAM, not yet for the two skills that actually run today. Both
`.claude/skills/add-concert/SKILL.md` and `.claude/skills/triage-leads/`
still work the way they did before the API existed: the owner pastes a DM
copy block or a discovery page into the conversation, the skill drafts YAML
from what it was handed, and the owner pastes the result back into
`/concerts/import`. Nothing about that changed on 2026-08-08 -- the API
shipped the SURFACE, not the two skills learning to call it. With a minted
token, `add-concert` could check `GET /api/v1/concerts?q=` and
`GET /api/v1/tags` before drafting, instead of guessing at whether a title or
a tag name already exists and leaving the owner to catch the duplicate; and
`triage-leads` could read `GET /api/v1/leads` directly rather than depending
on a DM's copy block or an `/admin/discoveries` paste. Deliberately out of
scope of the API build itself -- the design doc's own words: "the API ships
first and the skills follow, so the paste path keeps working throughout" --
and that sequencing reasoning still holds; this entry is the "follow"
half now that the surface exists to follow. Ranked near the bottom because
it changes nothing about what a tracked user ever sees; ranked at all
because it is the most directly actionable of the three follow-ups the design
doc named, and the cheapest -- no new endpoint, no new trust boundary, just
teaching two existing prompts to call four already-shipped GETs.

Re-read 2026-08-11 against round watch, **rank unchanged**, with one new input
worth naming. `/admin/quiet-ladders` emits a paste-ready copy block in the same
shape `/admin/discoveries` does, and it deliberately carries THE ROUNDS THE
CATALOGUE ALREADY HOLDS beside each concert -- without that, an agent handed
the list re-proposes rounds that are already there, which is the same class of
waste this entry exists to remove for tags and concerts. So these skills now
have a second producer feeding them, and the worklist is exactly the kind of
input that should arrive through the read API rather than a paste:
`GET /api/v1/concerts` already serves `next_anchor_at`, the same signal the
page's predicate reuses, so an agent can compute the worklist itself. That is
this entry's proposal, with a second concrete use.

### 19. Agent write endpoints

Impact: potentially high, longer-term -- it would close the loop the read
API opened -- but explicitly NOT ACTIONABLE today, and gated on evidence
this build was built to produce - effort: large. Raised: 2026-08-08, recorded
scope boundary from the agent read API's design doc ("What this deliberately
is not").

The read API stops an agent inventing duplicate concerts and tag names, and
lets it iterate on its own drafts without the owner relaying either half by
hand -- but a proposal can still become a concert only through
`import_commit`, which stays a human pressing a button on
`/concerts/import/pending`. That is not friction that leaked in; CLAUDE.md's
own words for the parallel case (`PendingDraft` rows) apply here without
edit: the owner's approval is the safety property the whole AI-triage build
(both completion phases) was constructed around. Reads shipped FIRST, and
deliberately, because they make the writes question DECIDABLE rather than a
guess: once an agent can see the catalogue, the leads queue and its own
drafts, the quality of what it proposes becomes something a human can
actually observe over real use -- and that observation is what would
eventually say whether committing unread is safe for any of it. There is no
evidence yet either way; this entry is logged as a recorded scope boundary,
not a task queued behind anything. It becomes actionable when the read API
(and the skills that start using it -- the "Teach `add-concert` /
`triage-leads` to use the agent read API" entry directly above, named rather
than numbered because this pointer has now been renumber-invalidated twice)
have been used long enough
that the owner has an opinion about the AGENT's judgment, the same way phase
2's calibration gate (which ran on 2026-08-09/10; the entry that carried
it shipped nothing and was deleted on 2026-08-11, its verdict folded into the
phase-2 Shipped entry) is what
decides whether draft-completion's judgment is trustworthy. Don't build this
speculatively ahead of that evidence.

Re-read 2026-08-11 against round watch, **still explicitly not actionable and
unchanged in rank**. It adds one more instance of the friction this entry
describes, and no evidence at all about the AGENT's judgment, which is the only
thing that would move it: a quiet-ladders row an agent researches still becomes
a round only by a human typing it into that concert's edit page. Worth naming
because it is a gap this entry does not currently propose closing -- the write
path it contemplates is `import_commit`, which CREATES concerts and cannot
amend one (import answers 409 for a concert that already exists, invariant 6),
so "let an agent add a round to a concert the catalogue already has" is a
different endpoint from anything discussed here.

### 20. An MCP server in front of the agent read API

Impact: low -- a convenience wrapper around a capability that already
exists, not a new capability - effort: small-medium, and explicitly not
worth building without a concrete friction driving it. Raised: 2026-08-08,
"Approach B" from the same 2026-08-08 discussion that produced the read API,
considered and rejected as the FIRST build.

An MCP server that calls `/api/v1/*` under the hood, so an agent (inside
Claude Code or elsewhere) reaches the catalogue/leads/drafts through MCP
tool calls rather than shelling out to `curl` with a bearer token. Explicitly
NOT foreclosed by choosing the plain HTTP API first -- the owner's own
reasoning, recorded in the design doc, was that an MCP server is "this plus
a wrapper, deployable separately, and usable only from an MCP client", which
is exactly why it was the wrong thing to build BEFORE the API it would wrap
existed. Building the wrapper first would have meant guessing at an
interface for a resource that didn't exist yet -- the same sequencing
argument that put reads before writes in the agent-write-endpoints entry
directly above (named rather than numbered, for the reason that entry's own
pointer now is). Worth a fresh look if the raw
HTTP ergonomics prove genuinely annoying inside Claude Code in practice;
nothing so far says they do, so this stays logged rather than built.

### 21. A lone character on `/tags` renders as a plain chip, not a split pill

Impact: nil today, real the day it stops being nil -- effort: not a one-word
fix. Raised: 2026-08-12, filed as a deferred note inside the Following-rework
entry while reviewing its phase 2; carried forward here as its own entry now
that entry has shipped and moved to Shipped (below) -- an open gap should not
quietly disappear along with the entry that was tracking it.

`/tags`' "Performers with no group" section calls `tag_chip` directly rather
than `member_chip`, so a character with no group renders as a plain chip and
her seiyuu -- if she has one -- is invisible on this page. The split-pill
treatment (one chip in two independently-followable halves, `character`/`cv`)
only fires inside a group row, where `member_chip` is what gets called.

**Zero live instances today, measured**: every one of the 318 CHARACTER tags
in the catalogue is a member of at least one group, so nothing is visibly
broken yet -- this is a gap in the code path, not an observed defect. It
becomes live the day a character is catalogued with no group at all, which
the `add-concert` skill or an editor could do at any time without knowing this
page has a blind spot for it.

**Not a one-word fix.** `member_chip` hard-codes `count=none`, because a group
row's members carry no per-tag concert count in that context; the ungrouped
section DOES show counts. Routing ungrouped characters through `member_chip`
as-is would silently drop them. Whoever picks this up needs a variant that
keeps the count `member_chip` currently omits, not a one-line call swap.

Ranked at the bottom, below the MCP server entry, on the same self-announcing
logic #17 uses: the failure mode is a missing UI element on a page an editor
is looking directly at, not a silent data or reminder-timing error, and it
costs nothing until the precondition (an ungrouped character) exists.

### 22. An ops check for a stale round poll

Impact: low (protects a pass that is `ROUND_POLL_ENABLED`-gated off by
default in production today, so its cost is zero until the day that flag
flips) - effort: small (one more `RegistryEntry` in `ops.py`'s `REGISTRY`,
the same shape `check_backup` already is). Raised: 2026-08-13, round poll
phase 1's own design note.

The empty-digest decision the poll makes rests entirely on one premise, in
`build_poll_digest`'s own docstring: "Its ABSENCE is the signal: no digest
means the pass did not complete, and with a suppressed quiet day a broken
pass and a quiet one would look identical." Nothing currently watches for
that absence. `ops.run_checks` has no entry comparing
`RoundPollState.last_run_at` against an age threshold, the
way `check_backup` already compares its own marker's age against
`BACKUP_MAX_AGE` with the same "process just started, give it a grace period"
allowance. The scheduler's OWN failure handling is not a substitute: it
re-stamps `RoundPollState` inside the tick's `except` block precisely so a
run that dies mid-poll still counts as "today's run" and does not retry every
60 seconds forever (see `scheduler/loop.py`'s round-poll block) -- but that
re-stamp is code running INSIDE the same process that might be the thing
failing. A crash between the flag check and the `try`, or the whole process
going down mid-run before either branch's commit lands, produces no re-stamp
and no digest, and nothing today would ever notice the stamp stopped moving.
Once this exists, a run that found, rejected and failed nothing on a given
day can safely stay quiet -- which is the whole point of the empty-digest
design -- because something else is now watching the one signal that design
depends on.

### 23. The test suite has outgrown its tooling

Impact: low-medium (no correctness or user-facing cost -- but it already
shapes how every build in this repo gets verified, which #6 above treats as
its own kind of impact for the same reason) - effort: medium (pytest-xdist
parallelisation, splitting into multiple invocations, or a background-run
convention are all real options; none has been picked). Raised: 2026-08-13,
during round poll's own task-6 documentation pass, which needed to run the
suite and could not.

`pytest -q --collect-only` counts 2,957 tests as of this build (2026-08-14).
A full `uv run pytest -q` run measures at roughly 650 seconds against this
harness's own foreground command ceiling of 600 seconds (600,000ms) -- so a
full verification pass can no longer complete inside a single foreground
Bash call, and every subagent that needs one has to background it and wait
for a completion notification instead of blocking on the result directly.
This is not hypothetical friction the way #20's MCP wrapper is, where "nothing
so far says" the friction it worries about is real -- it is a
constraint every task in this build already worked around, this one included
(task 6's own instructions say plainly: do not attempt the full suite in the
foreground, the owner confirms it separately). Ranked below #6 on the
distinction #6 itself draws between a LATENT production risk with one
documented near-miss and a present-day workflow cost with a known, if
inconvenient, workaround -- this is the latter. It will keep growing on its
own as the suite does; nothing about fixing it needs to wait for evidence the
way #20 does.

### 24. Round poll phase 2's three known gaps

Impact: nil today (phase 2 -- the apply flow these gaps live inside -- is
unbuilt, and phase 1 itself defaults off) - effort: small once phase 2 exists
(three targeted fixes against code that will already be there, not a design
change). Raised: 2026-08-13, round poll phase 1's own build; recorded here so
phase 2 does not rediscover them as bugs.

**A round whose CLOSING time alone moved is dropped silently.** `dedupe_key`
(`domain/round_proposals.py`) is built from the label and `opens_at_utc`
only. A concert reaches the poll's candidate list BECAUSE it is quiet, and
quiet requires every one of a live round's four anchor fields --
`opens_at_utc`, `closes_at_utc`, `results_at_utc`, `payment_deadline_at_utc`
-- to already be in the past (`next_anchor_at`, `db/core.py`). So a round
whose closing date alone was pushed forward produces the identical key to the
one already on file, reads as `skipped_held` ("the concert already holds
it"), and the moved deadline never reaches a reviewer.

**Nothing prunes a proposal whose round the owner later adds BY HAND.** A
`RoundProposal` upserted today and a `Round` typed in tomorrow through the
ordinary edit page are two unrelated rows -- nothing compares them -- so the
proposal sits in `pending_proposals` forever even once the concert holds
exactly what it proposed, and the concert has by then usually left the
candidate list entirely (it is no longer quiet), so no future poll run will
ever re-derive `skipped_held` for it either. The stale proposal is only
visible on `/admin/quiet-ladders/proposals`, never re-surfaced anywhere the
owner would think to look again.

**A doubled proposal in one LLM reply over-counts the new-vs-refreshed
tally.** `upsert_proposal`'s unique index on `(concert_id, dedupe_key)`
correctly resolves two identical candidates in one reply to ONE row -- the
second call's `SELECT` finds the row the first call just flushed. But
`_poll_one` increments `report.new_proposals`/`report.refreshed` once per
candidate in `fresh`, not once per resulting row, and the second call's
`proposal.first_seen_at == now` is still true (it was set moments ago by the
first call, in the same run), so it counts as new again. A model repeating
itself within one reply reports "2 new proposals" for a database that gained
one row.

**Revision-pass note (2026-08-13, round poll phase 1 -- full pass required by
CLAUDE.md's WISHLIST rule after every shipped feature):** one entry updated
in place (#2, above -- NOT moved to Shipped, since phase 2 of the large shape
and the small shape both remain open), three entries appended (#22-#24,
above), one Shipped entry added (below), no other entry re-ranked. The
twenty entries besides #2 were re-read against this build specifically, not
skimmed, and the shared answer is that a flag-gated daily pass reading
quiet concerts' own pages and writing nothing but review rows touches almost
nothing else on this list. Two got a closer look because they looked
adjacent. **#6 (three long jobs share the reminder tick)**: the round poll IS
a fourth job of the shape this entry means, unlike round watch's 2026-08-11
re-read, which found round watch was NOT one. Round watch is one local
`select` plus a Python diff; the round poll is up to fifteen sequential
fetch+LLM pairs against third-party pages with its own 240s wall clock and
its own `beat()` calls -- exactly the shape `discovery.py`/`triage.py`/
`draft_completion.py` already have, not the shape round watch was cleared of.
Four long jobs now share the tick where the entry was written against three;
rank held rather than raised, because the entry's own fix (a second task or
process with its own budget) addresses the SHAPE regardless of how many jobs
currently share it, and this build adds no new class of risk beyond one more
instance of the class already named. **#5 (in-app LLM extraction)**: the
round poll calls `app.llm.chat` and reuses `completion_prompt`, which is the
first other feature to touch the exact machinery #5 is about since
`draft_completion` itself. It neither unblocks nor obsoletes #5, which is
about the IMPORT page specifically; the round poll is a background pass with
no user-facing form, so it demonstrates the machinery generalises without
being evidence about #5's own shape. Judgement: nothing else moves. Recorded
explicitly rather than left implicit, per the instruction that a revision
pass leaving no trace is indistinguishable from one that never happened.

**Revision-pass note (2026-08-13, following rework phase 4 -- the final
phase, full pass required by CLAUDE.md's WISHLIST rule after every shipped
feature):** the unranked Following entry moves to Shipped (below) and takes
NO number with it -- it was filed unranked through all four phases precisely
so a still-open scope would never force a renumber, and finishing the scope
removes an unranked entry rather than resolving a numbered one, so #1-#20
keep the numbers they already had. One insertion, #21 above, carrying forward
the one piece of that entry's content that was NOT resolved by finishing it --
everything else in it either shipped (phases 1-4) or was superseded by what
shipped. Three existing entries were read closely because this build touches
ground they describe; the other seventeen (#1-#15, #18, #20) were re-read
against it specifically, not skimmed, and the shared answer is that phase 4 is
a Preferences template reduction plus two write paths (`POST
/presets/apply-to-following`, and the `subscribe`/`handle_newly_tagged`
changes from earlier tasks on this branch) -- it changed no round taxonomy, no
catalogue surface, no fetch path, no admin page and no calendar roster.

**#16 (`db/core.py`'s size)**: this build's only touch to `core.py` is the
ordering rewrite inside `handle_newly_tagged` (task 5's brief cites the
measured `[-1]` -> `[-3]` regression that forced it) -- a function the entry's
own text already places inside the presets/subscriptions section of the
strongly-connected component. It reads one more attribute
(`candidate.is_default`) per loop iteration and adds no new caller and no new
callee, so the shape the entry describes is unchanged; only the line count
inside an already-SCC function moved, the same verdict the round-watch pass
recorded here for a different function on 2026-08-11. `apply_default_to_following`
itself lives in `routes/preferences.py`, not `core.py`, and calls only the
pre-existing `get_default_preset` -- no new edge into the component at all.

**#17 (the demos are one grammar-migration behind the app)**: its own design
spec named it explicitly, in the Out-of-scope section written before phase 1
started -- *"the demos will need frames for these surfaces, but that is that
entry's pass"* -- so this is a direct, self-declared addition to its
worklist rather than something this pass had to go looking for. Three
surfaces now exist with no demo frame at all: `/tags` as a follow surface
(split pills, the Follow/Edit mode toggle), `/following` (plain chips, the
per-tag config dialog) and the reduced Preferences section. None of these
existed when #17 was filed on 2026-08-12, so its worklist -- gathered at the
time from the demo-parity build's own review passes -- could not have named
them. Rank unchanged: still a documentation-quality problem with no
user-visible impact, still self-announcing, and still below #16 for the
reason already given there.

**#19 (agent write endpoints)**: checked on the closest precedent this file
has for it -- the 2026-08-11 outcome-correction pass read #19 specifically
because that build added a per-user write (`RoundOutcome`) and drew the line
"a different animal from the catalogue writes that entry contemplates". This
build adds one more per-user write of the same shape: `POST
/presets/apply-to-following` (a user's own `TagSubscription` rows). `POST
/subscriptions/{id}/settings` (one subscription's preset/notify) is the same
shape but is not this build's -- it shipped in phase 3; this build only edits
its docstring. Read honestly against #19's actual subject, neither moves it,
and for a narrower reason than round watch got: round watch at least produced
new catalogue-ADJACENT observations (a quiet ladder) that a human still has to
type into the edit page, which the 2026-08-11 pass called "one more instance
of the friction this entry describes". Neither write is catalogue-adjacent at
all -- a preset link and a notify flag are per-user configuration
with no relationship to `import_commit` or anything an agent would propose
INTO the catalogue -- and neither route sits behind the agent read API in the
first place (`docs/agent-api.md`'s surface is GET-only; these are ordinary
session-authenticated web routes a human clicks). So this build contributes
no friction instance and no evidence about an agent's judgment either way,
which is the only thing #19 says would move it. Unchanged in rank.

The other seventeen entries stand as written -- none of them touch tags,
subscriptions, presets, the reminder queue's materialization or the scheduler
tick. #6 (three long jobs share the reminder tick) was checked specifically
for the last of those: `apply_default_to_following` and `subscribe` are
synchronous web routes, not tick jobs, and add no `beat()` obligation.
Judgement: nothing else moves. Recorded explicitly rather than left implicit,
per the instruction that a revision pass leaving no trace is indistinguishable
from one that never happened.

**Revision-pass note (2026-08-12, minor demo-parity cosmetics -- full pass
required by CLAUDE.md's WISHLIST rule after every shipped feature):** one removal
(#8, shipped), one insertion (#17, the demos' grammar drift), and no re-rank on
merit. The two cancel, so #18-#20 keep their numbers while the former #9-#17
slide to #8-#16 by position alone. The shared answer for everything else is that
this is the smallest build this file has recorded: two demo files, one Preferences
label with its two catalogue rows, one dialog footer moved out of its form, and
one CSS declaration on two selectors. It changed no reminder math, no round
taxonomy, no tag semantics, no catalogue surface, no fetch path, no admin page
and no data model, and it **removed nothing from any other entry's path** -- a
thing worth saying plainly, because a nine-task branch that clears nobody's way
is exactly what "polish" is supposed to mean and this file should be able to tell
the difference.

Three entries were read closely rather than skimmed, being the only ones this
build could plausibly reach.

**#10 (Discover sort in the content head)** is the one that genuinely moved in
meaning without moving in rank, and it is the most useful thing this pass
produced. It is the LAST surviving entry of the family the shipped one belonged
to: a demo-says-X, app-says-Y difference filed as an app debt. The owner's merit
ruling now governs it too -- the demo is the default answer, not the verdict --
and the entry had already half-anticipated that in its own filing ("debatable
whether the sidebar is actually worse"). Whoever picks it up should start by
deciding which surface is RIGHT rather than by restructuring the DOM. The new #17
sharpens the same point from the other side: a demo one grammar-migration behind
the app is a weaker authority than this entry assumed when it was written in
July. Rank unchanged -- low impact, small effort, and none of the work described
changed -- but its first step did.

**#11 (name the destination on the sign-in bounce)** lost half of its own ranking
comparison, corrected in place above: it was ranked below "the demo-parity
cosmetics batch and the Discover-head entry", and the first of those no longer
exists. The argument survives on the second alone. No contact with `safe_next`,
the bounce copy or the landing page -- and worth one line so it is not
re-derived, the `.lede h1` this build gave `text-wrap: balance` is the ERROR
pages' heading, not the bounce note's.

**#3 (franchise-aware round-label suggestions)** was checked because this build
made a msgid change, which is the only thing it has in common with an entry about
round-label copy. No contact: the string that changed is a Preferences toggle
label, the phrase library is untouched, and the one transferable lesson is
already in CLAUDE.md -- editing English copy means both `.po` files by hand.

The unranked Following entry at the head of Proposed stands exactly as filed and
stays unranked; this build is what removed its sibling gap from #8, which is the
event that created it, and its one numeric pointer was made name-based here so it
cannot rot again. The remaining sixteen (#1-#2, #4-#9, #12-#16, #18-#20) were
re-read against this build specifically and stand as written; #1 records its own
seventeenth consecutive unchanged verdict in its entry. Judgement: nothing else
moves. Recorded explicitly rather than left implicit, per the instruction that a
revision pass leaving no trace is indistinguishable from one that never happened.

**Revision-pass note (2026-08-11, outcome correction -- the second full pass
that day, required by CLAUDE.md's WISHLIST rule after every shipped feature):**
one insertion, no re-rank on merit, and one entry grown. The insertion is #14
(two narrow edges the build left), which pushes the former #14-#19 to #15-#20 by
position alone; the growth is #8, and it says in place why a CSS defect is
sitting in a demo-parity entry. Everything else was re-read against what
shipped, not skimmed, and the shared answer is that this build is small and
inward-facing: one service writer plus its private re-derivation helper, one
route, one macro parameter and one dialog on ONE page, and three Discord
buttons. It changed no reminder math, no round taxonomy, no tag semantics, no
catalogue surface, no fetch path and no admin page. Five entries were checked
more closely than the rest because they look adjacent. **#1 (minute-level
offsets)**: it re-plans through `reinstate_user_rules`, which is invariant 2's
existing machinery unchanged, and touches no offset column -- held at #1 and
recorded in its own entry. **#4 (the cosmetic `RoundKind` members)**: the
correction is kind-blind by construction (it deletes a `RoundOutcome`, and every
kind reaches one the same way), and UPGRADE rounds are the one kind with real
behaviour, which this build inherits through `capture_gates` rather than
re-deriving -- unchanged. **#8**: grew, above. **#17 (`db/core.py`'s size)**:
this ADDED ~90 lines to the file it describes, which is a small argument the
entry's own way, and none of them are a new cut -- `clear_round_outcome` sits
inside the same strongly-connected component for exactly the reason the entry
gives (it calls `reinstate_user_rules`, which is the queue-sync section), so the
shape is unchanged and only the line count moved. **#19 (agent write
endpoints)**: unchanged and still not actionable -- this is a per-USER write, on
that user's own record of their own lottery, which is a different animal from
the catalogue writes that entry contemplates and produces no evidence about an
agent's judgment either way. Judgement: nothing else moves. Recorded explicitly
rather than left implicit, per the instruction that a revision pass leaving no
trace is indistinguishable from one that never happened.

(The numbers in the two paragraphs above are the ones THAT pass left, kept
deliberately. The 2026-08-12 demo-parity pass then shipped #8 and inserted one
entry at #17, so against the list as it stands now read #14→#13, #17→#16 and
#19→#19; #8 is no longer a number at all -- it is the demo-parity cosmetics entry
in Shipped. Every number at or below #7 means the same entry it did then.)

**Revision-pass note (2026-08-11, full pass required by CLAUDE.md's WISHLIST
rule after every shipped feature):** the five entries round watch genuinely
touches carry their own dated re-reads above (#6 three long jobs, #14 the
unbounded admin lists, #15 the calendar-feed silence, #17 the skills, #18 agent
writes), and none of them moved. The other twelve (#1, #3-#5, #7-#13, #16) were
re-read against this build specifically rather than skimmed, and the shared
answer is that round watch touches nothing they describe: it added one db
feature module, one pure message builder, one admin route and template, and one
`try`/`except`/commit block in the tick. It changed no reminder math, no round
taxonomy, no mobile or demo-parity surface, no calendar roster and no
user-facing page at all. Two were checked more closely than the rest because
they LOOK adjacent and are not. **#5 (in-app LLM extraction)**: round watch
fetches nothing and calls no model -- it is deliberately the shape that needed
no trust decision -- so it neither unblocks nor obsoletes that entry; the one
contact is via #2 above, whose heavyweight shape would use exactly the
machinery #5 is about. **#16 (`db/core.py`'s size)**: this build did take one
function OUT of `core.py`'s orbit by promoting `_next_anchor_iso` to
`next_anchor_at`, but that is a rename plus a caller, not a cut -- the function
stays in `core.py` and the mutually-recursive component is the same shape it
was. If anything it is a small argument the other way, since the promotion's
whole point was that a SECOND copy of the signal outside `core.py` would drift.
Judgement: nothing else moves. Recorded explicitly rather than left implicit,
per the instruction that a revision pass leaving no trace is
indistinguishable from one that never happened.

(The numbers in the paragraph above are the ones THIS pass left, and are kept
that way deliberately -- a dated note should record the list it actually ranked.
The outcome-correction pass later the same day inserted one entry at #14, so
read #14→#15, #15→#16, #16→#17, #17→#18 and #18→#19 against the list as it stood
after it. Every number at or below #13 -- which was all the rest of them -- meant
the same entry it did then. **The 2026-08-12 demo-parity pass then shipped this
paragraph's #8 and inserted one entry at what is now #17**, so against the list
as it stands TODAY the chain above resolves to #14→#14, #15→#15, #16→#16, #17→#18
and #18→#19; this paragraph's #8 is in Shipped; its #9-#13 are #8-#12 now; and
its #1-#7 still mean what they did.)

**Revision-pass note (2026-08-08, full pass required by CLAUDE.md's
WISHLIST rule after every shipped feature):** every remaining entry (#3-#5,
#7-#17 above) was re-read against the agent read API specifically, not only
skimmed. None of them touch the catalogue, the discovery queue or
`PendingDraft` rows in a way the API's read-only surface changes -- they
range across reminder offsets, round-kind taxonomy, PWA installability, the
calendar feed roster, the sign-in bounce, and `db/core.py`'s own internal
shape, none of which this build touched. Judgement: nothing else moves.
Recorded explicitly rather than left implicit, per the instruction that a
revision pass leaving no trace is indistinguishable from one that never
happened.

(The former "Editor page parity with the demo" entry (2026-07-20) was
absorbed on 2026-07-23 into the editor-pages coherence pass --
everything it tracked (demo's nested-rounds structure vs shipped flat lists,
read-only summaries vs always-open inputs, the stale editor frame in
`dekimasen-demo.html` showing the dropped concert-level venue) is exactly
what that brainstorm re-litigates, and its "demo is now WRONG" venue-frame
debt carries over as a hard requirement there.)

(The former "Eventernote links on performer chips" entry was dropped in the
2026-07-19 revision pass: it already shipped inside the Tags page redesign,
which added `Tag.eventernote_url` and wired it onto the concert page's
performer chips - see its Shipped entry below.)

## Shipped

### Round poll, phase 1 of 2 (2026-08-13)

Branch `round-poll`, six tasks, spec
`docs/superpowers/specs/2026-08-13-round-poll-design.md`, plan
`docs/superpowers/plans/2026-08-13-round-poll-phase-1.md`, migration
`05ce13fac69a` (`round_proposals`, `round_poll_state`,
`concerts.ladder_polled_at_utc`). This is Proposed #2's LARGE shape, and only
phase 1 of it -- see #2 above, updated in place rather than moved here, for
what still does not exist.

**What it does.** A `ROUND_POLL_ENABLED`-gated daily pass (off by default)
consumes round watch's own `quiet_ladder_rows` as its candidate list, re-reads
each quiet concert's own official page under `ApprovedPublicHosts` and the
`/admin/fetch-domains` approval queue, and asks the SAME prompt and the SAME
evidence rule `draft_completion` (AI triage's own phase 2) already uses --
`completion_prompt` and `verify_rounds` -- to say what the ladder now holds.
What it finds is stored as a `RoundProposal`, never a `Round`: this pass's own
phase 1 puts nothing in front of a user beyond a read-only list at
`/admin/quiet-ladders/proposals`, and applying one onto a live concert is
phase 2, unbuilt.

**Three findings earned their own entry rather than a footnote,** and are
recorded in full in `docs/architecture.md`'s Round poll entry:

- **The plan that specified this pass claimed the resume cursor was free, and
  it was false.** `quiet_ladder_rows` sorts by `ladder_rechecked_at_utc`, the
  HUMAN's stamp, which the poll must never touch -- so consumed in that order
  the candidate sequence is byte-identical every run, and the moment the 240s
  wall-clock budget bites, the same head is re-read daily while the tail
  starves. The pass re-sorts the SAME candidate set by its own
  `ladder_polled_at_utc` before walking it, and stamps a concert whenever an
  attempt on it is SPENT -- success or failure alike -- because stamping only
  successes lets one permanently-broken page hold the head of the queue
  forever.
- **`dedupe_key` truncates to minute precision, and is keyed on the label AND
  the open time together.** Without truncation, the same real round produces
  two different keys depending on which side of the diff reads it (the
  proposed side always lands on `:00`; a hand-typed `Round.opens_at_utc` need
  not), and would be re-proposed forever. Keying on the label alone would
  instead swallow a moved date -- a round pushed from Sept 3 to Sept 10 would
  read as the SAME round already on file, and an owner who dismissed one date
  would never be shown the other.
- **The run is bounded by a WALL CLOCK, not a count, and AI triage's 511-lead
  classify failure does not transfer.** That failure put N items in ONE
  prompt and outgrew DeepSeek's output cap; this pass is one prompt PER
  CONCERT, so a bad page or a bad reply costs one concert, counted and named,
  never the run. What a count cannot bound here is time, since the run sits
  inline in the 60-second reminder tick.

**What it deliberately is not, recorded before code was written the way round
watch's own entry recorded its abstentions:** it writes no `Round` and reaches
no ordinary user at all (phase 1's whole surface is a read-only admin list),
and `run_round_poll` itself queues nothing -- the ONE notification the feature
produces is the admins' digest, queued by the scheduler block that owns the
transaction, never by the pass; it never
touches `ladder_rechecked_at_utc`, the OWNER's own stamp that orders
`/admin/quiet-ladders` (a machine writing it would silently mark that
worklist attended); and its digest DM is sent even on an empty run, the one
deliberate departure from the discovery and quiet-ladder digests, because
absence of a run report is the only thing that tells a broken pass apart from
a healthy quiet one -- WISHLIST #22 above exists because nothing yet watches
for that absence.

`docs/architecture.md` gained the Round poll entry cited throughout; `CLAUDE.md`'s
migration guidance was corrected in the same build -- the stale half of its
rule claimed autogenerate leaves an `import app.db.models` line to remove by
hand, and zero of the ~50 committed revisions contain that import (Alembic
renders a `TypeDecorator` as a fully-qualified dotted name and adds nothing to
`${imports}`), so there was never a line to remove.

### Following is due a rework: the four-phase build (2026-08-13)

Branches `following-rework-phase-1` (PR #151, nine commits), `-phase-2`
(PR #152, eleven commits), `-phase-3` (PR #153, fourteen commits) and
`-phase-4` (PR #154, ten commits), design `docs/superpowers/specs/2026-08-12-following-rework-design.md`,
plans `docs/superpowers/plans/2026-08-12-following-rework-phase-{1,2,3}.md` and
`docs/superpowers/plans/2026-08-13-following-rework-phase-4.md`. **No migration,
across any of the four phases** -- `TagSubscription.preset_id`/`.notify` and
`ReminderPreset.is_default` already existed and carried the entire redesign.
This entry was filed UNRANKED on 2026-08-11, the owner's words: he wants to
rework how following tags works, "since it's getting pretty big" -- nothing
more specified at filing, deliberately, since a scope that does not exist yet
cannot be honestly ranked.

**What "big" turned out to measure as, which the entry could not have known
when it was filed.** Preferences' Following section rendered every followed
tag as a `.subrow` with its own toggles, above a `<details>` fold holding the
WHOLE catalogue as chips: **878 chips**, of which 310 were one flat "Solo
artists" list, behind **65 `group_members` queries per render** -- an N+1, on
both `/preferences` and `/tags`. Two structural failures hid inside that
count. Search was broken for **681 of the 735 catalogue tags**: `data-name`
carried `t.name` (Japanese) while the chip displayed the localized name, so an
English viewer typing what they could see matched nothing. And following a
CHARACTER was impossible outright -- `preferences.py` never mentioned
`CHARACTER`, so all **318 character tags** (43% of the catalogue) could only
be followed as an unlabelled member chip inside a group, never on their own.
None of these four numbers were available when the entry was filed; they came
out of phase 1's brainstorm, reading the live catalogue export against the
templates.

**Phase 1 -- search and the N+1, alone and first.** Fixed both defects on the
EXISTING surfaces before touching anything about where following lives:
`data-name` now joins all three name columns (`name`/`name_en`/`name_zh`),
`filterChips` hides containers left holding no visible match instead of
showing the whole skeleton around one hit, and one batched `members_by_group`
query replaced the per-group loop at all four sites that had it
(`/tags`, `/preferences`, `/welcome`, the concert-editor tag picker).
Deliberately first and alone: everything downstream assumes search works,
since the redesign removes per-group folds and search becomes the only way to
reach a name in a 99-member group.

**Phase 2 -- `/tags` becomes the follow surface.** Every chip is now a real
`<form>` posting to `/subscriptions`, for everyone -- the only follow control
that existed before this, `_tag_follow_bell.html`, rendered solely inside the
editor-only table view, so a non-editor was shipped 735 hidden rows of follow
buttons they could not reveal. A character and her seiyuu render as one split
pill in two independently-followable halves (`character`/`cv`), the first
surface where the app's own `voiced_by_tag_id` distinction becomes something a
user can act on directly, rather than only something `attach_tag`'s expansion
reasons about. A subunit's members render under the subunit alone, `/tags`
ONLY -- the concert page keeps 2026-08-01's "repetition kept" ruling, because
that ruling is about a truthful BILL and a catalogue page asserts no lineup.
The redundant Characters section and the whole table view are deleted (six
admin pages' shared `.tagtable` CSS rule is kept; only the `/tags` markup
using it goes). Editors get edit-on-click back as an explicit `Follow ⇄ Edit`
mode instead of a second meaning riding the same click. **Two live
regressions rode along and were caught within the same build**: a follow
press 303ing back and re-rendering the whole directory measured at **923 ms
and 6.98 MB** on a live-shaped 735-tag seed, fixed by an htmx swap of the
pressed chip alone (**10.7 ms, 518 bytes** -- the same POST); and the unfollow
half, initially keyed by subscription id, left a stale duplicate chip pointing
at a deleted row (318 of 735 tags render more than once on this page), fixed
by keying unfollow on TAG instead, idempotently.

**Phase 3 -- `/following`, the subscriptions page.** `/tags` owns the
catalogue (everything you COULD follow); `/following` owns your subscriptions
(what you DO follow) -- the owner's reasoning for two pages rather than a
filter on one was that a chip cannot mean "follow" on one surface and
"configure" on the other. Plain chips, one per subscription, grouped by
franchise, each marked ONLY where it deviates from the viewer's defaults (a
muted 🔕 for notify off, a preset's name where it disagrees with the default,
nothing at all when it conforms) so scanning forty chips only the exceptions
draw the eye. Clicking a chip opens a dialog holding the three things a
subscription has: which preset it links, whether it notifies, and whether it
exists at all (Unfollow). No migration needed because `TagSubscription`
already carried both columns -- this phase EXPOSED per-tag presets, it did
not add them.

**Phase 4 -- the Preferences reduction, and the two owner rulings that came
out of building it.** Preferences' Following section drops the picker and the
per-tag `.subrow`s entirely, replaced by a fixed-height summary: a followed-tag
count with a "Manage →" link to `/following`, the standing default (read-only),
the retroactive-fill button, and the unchanged skipped-events list. Measured
in-browser at 0 and at 9 followed tags: **218.9px both times** -- the section
no longer grows with follow count, which was the entire point -- with a 3.1px
variance depending only on whether a default preset exists (the button is
taller than the pill it replaces).

Two owner rulings, both 2026-08-13, both load-bearing enough to belong in
`docs/architecture.md` (see the `db/` and `web/` entries) rather than only
here:

1. **The standing default covers PRESET only; the NOTIFY half was dropped**
   rather than add a `User` column for it. `ReminderPreset.is_default` is a
   per-preset flag with an obvious column to widen; a standing notify default
   has no equivalent, and the owner chose not to add one for a setting this
   narrow. This is the reason the whole four-phase rework shipped with zero
   migrations -- the one place a new column was genuinely tempting is the one
   place it was ruled out. The design spec's §Preferences said the standing
   default covers both; it now carries a dated correction rather than a
   silent edit, matching the format this same document already used once, for
   phase 4's ordering reason.
2. **A non-default, tag-specific preset now outranks the default in
   `handle_newly_tagged`**, reversing the previous earliest-created-wins rule.
   The retroactive fill (below) writes the default into the OLDEST blank
   subscriptions, so earliest-wins let a blanket default silently outrank a
   preset the reader had tuned by hand for one tag on a concert also matching
   a filled tag -- invariant 3 makes following both a group and one of its
   members the ordinary case, not an edge. Measured: reminder offsets moved
   from `-1` to `-3` on such a concert, with the tuned subscription's row
   byte-identical. This is SHARED code -- it changes which preset every
   user's future-matching concert gets, not only presses of the fill button.

**"Apply my default preset to all followed tags"** (`POST
/presets/apply-to-following`) is the retroactive half phase 2's `subscribe`
fix could not be: task 2 made a NEW follow inherit the default (before it,
`preset_id or None` meant every chip follow -- which sends no preset_id --
linked no preset at all, so the standing default never actually reached a
real follow), but that fixes nothing for follows already on the books, which
in production is all of them. The fill UPDATEs every subscription the caller
owns whose `preset_id IS NULL` and touches nothing else; the `is_(None)`
clause is the entire safety property, and because the failure mode of
dropping it is silent -- a blanket overwrite raises nothing and renders a
cheerful count -- the route reports TWO numbers, `filled` and `kept`, not
one. `preset_id = NULL` is OVERLOADED (it also means "auto-apply switched off
on purpose", the deleted `toggle_subscription_autoapply`'s and
`/subscriptions/{id}/settings`'s "none" option's write), so the fill silently
re-arms switches a reader had turned off; owner ruling, 2026-08-13: fill them
anyway, but the confirmation banner must say so plainly rather than add a
column to tell the two NULLs apart. Both `POST /subscriptions/{id}/notify` and
`.../auto-apply` are deleted outright with the markup that was their only
caller, not merely left unreachable -- `/subscriptions/{id}/settings` already
covers the same two fields less ambiguously.

**One deferred gap survives all four phases and is now its own entry, #21
above**: a character with no group renders as a plain chip on `/tags`, not a
split pill, because "Performers with no group" calls `tag_chip` rather than
`member_chip`. Zero live instances (every one of the 318 CHARACTER tags is in
a group today), which is why it was filed as a note rather than blocking any
phase, and why finishing the rework did not happen to fix it -- `/following`
renders plain chips by design and never went near that code path.

### Minor demo-parity cosmetics, judged on merit (2026-08-12)

Branch `demo-parity`, nine tasks, spec
`docs/superpowers/specs/2026-08-11-demo-parity-design.md`, plan
`docs/superpowers/plans/2026-08-11-demo-parity.md`. **No migration**, no new
dependency, and the entire app-side diff is 18 added lines across five files --
one label (plus its two catalogue entries), one dialog footer, and one CSS
declaration on each of two selectors. This was Proposed #8, raised 2026-07-20 and
grown six times in three weeks without ever being worth a branch of its own.

**The owner replaced the entry's standing assumption, and that is the thing to
carry away from this build.** For its whole life #8 assumed *the demo is right
and the app should change*. He replaced it with **judge each on merit** -- the
demo stays the default answer, but where the app moved deliberately or is simply
better, the demo is what changes. Re-reading eight tracked items against that
rule found four were not what the entry said they were, and **two reversed
direction outright**:

- **The Tags member list.** The entry asked the app to adopt the demo's "+5
  more" truncation. But that is the EDIT dialog: every chip carries a delete
  `×`, and an inert "+5 more" with no way to expand made five of nine members
  unreachable on the one surface built for reaching them. Truncation is right
  for a *display* of members; this is not one. The demo changed.
- **The Setup pick tiles.** The entry filed the app's label-wrapping-a-hidden-
  checkbox as an accessibility gap against the demo's `<button aria-pressed>`.
  They render identically and screen readers handle both (announcing "checked"
  rather than "pressed"); the difference is that a real form control submits with
  JavaScript off and a button cannot, since `aria-pressed` needs a script to
  track state and a hidden input to carry it. The demo changed.

**Both would have been regressions had the pass run as filed**, which is the
case for the ruling, stated plainly: a three-week-old cosmetic list is a set of
claims about the app, and claims decay.

**Item 2 was struck, not built.** Filed 2026-07-21, it asked the demo for a frame
for `.signin-note`. On 2026-07-24 -- three days later -- the UX pass absorbed that
class into the two-shape callout grammar and it ceased to exist; the signed-out
bounce is `<p class="banner banner-block">` now, and
`dekimasen-ux-pass-demo.html` already carried both the replacement shape (line
724) and the migration map naming what it replaced (line 728). The entry spent
three weeks asking for a frame for a deleted component.

**The split pill's "open design question" was not open.** The entry called it
"worse than a gap, because the next person finds four rejected shapes and no
record of the choice". Half right: the demo carried no frame, but the choice and
its reasoning were already in
`docs/superpowers/specs/2026-08-01-character-seiyuu-subunit-design.md` -- the
split shape was chosen over the inline `如月千早（今井麻美）` form precisely
because the merge is conditional -- along with the subunit rail's ruling and its
rejected alternative. That task was a port, not a decision.

**The entry also shrank before the branch existed.** The Following cosmetic
(a disclosure fold against the demo's footer bar plus dialog) left #8 earlier the
same day and became its own unranked entry at the head of Proposed, because
putting it to the owner as a genuine fork surfaced that he wants the whole
Following surface reworked -- so deciding the cosmetic now would decide it twice.
And a sixth item -- the only DEFECT the entry ever carried, `style.css`'s
`.danger` card frame also matching every `<button class="btn danger">` and
beating `.btn` on source order -- had shipped on its own branch on 2026-08-11,
hours after it was filed. It is `.danger-card` now with a CSS-level sweep test,
and the durable half of that lesson lives in `docs/ui-conventions.md`: **an
anatomy class must be specific enough that it cannot match something else wearing
the same tone word.**

**What the entry got wrong divides cleanly in two, and both halves are worth
keeping.** Two were DESCRIPTION errors, written from a stylesheet rather than
from the page: the `.danger` item named the wrong stretched element (the danger
button rendered 48.5px; its quiet sibling reached 88.5px, because `.da` is a flex
row at default `align-items: stretch`) and a margin already overridden inline at
both of the card's real sites, and the `text-wrap` item asserted that three
sibling `h1` rules carried the property when only one of the three selectors is
an `h1` at all. That is the measure-don't-reason rule failing inside the entry
that collects measured defects. The other two -- the member list and the pick
tiles -- described the app accurately and judged it wrongly, because the entry's
standing assumption meant a difference never had to be argued for. Different
failure, same remedy: look at the thing.

**What actually shipped.** App side: `Auto-apply` → `Auto-apply preset` on
Preferences with both catalogues filled by hand (measured before accepting, per
the measure-don't-reason rule, at 365px and 320px in English and Japanese -- all
eight measured cells came back 119px, no wrap, so the longer label costs the
phone row nothing); the new-tag dialog footer hoisted out of the
`<form>` to span the dialog, with `form=` on the submit button; and `text-wrap:
balance` on `.lede h1`. Demo side: the two reversals above, four error-page
frames (403/404/422/500, copy verified byte-for-byte against `web/app.py`
including both 403 variants), the split pill and the subunit rail, the goods-sale
select and the requires/needed-for lines, and the import preview's evidence
block, rejection callout and paste fold. The tag EDIT dialog needed **no markup
change at all** -- it already had the footer-outside-form shape -- but was
untested, and now is.

**`.legal h1` was found during planning and fixed alongside `.lede h1` by owner
ruling.** The spec's claim that "three sibling `h1` rules already carry
`text-wrap: balance`" was wrong: only one of the three selectors carrying it is
an `h1` (`.hero .promise` 3.5rem, `.ctafoot h2` 1.6rem, `.chead h1` 1.7rem), so
the real pattern is *large display headings balance their wrap*, not "h1 rules
do". That made an honest sweep test impossible and left `.legal h1` (1.7rem, the
same size as `.chead h1`) looking like a second oversight. The owner ruled: fix
both, so the set the test pins expresses a coherent rule -- every display heading
1.6rem and up -- rather than an arbitrary list.

**A third instance of the pick-tile bug was found during execution, and the
design had missed it.** `#cap2` ("Already applied to any of these?") is a second
`.tile` block in the same demo, and the counterpart of `setup.html:116`, which
the app already renders as a label plus checkbox. Because it shares the `.tile`
class, the planned literal replacement of the `[aria-pressed]` state selectors
would have stripped every state style off those five tiles -- so the conversion
had to keep both selector families while the file was mixed and simplify to
`:has()`-only once `cap2` was converted too. 14 tiles converted file-wide, 9 in
`#pickList` and 5 in `cap2`. The plan document carries a dated correction saying
so.

**Eight implementation tasks, five of which needed a fix round; Task 7 needed
two.** Every finding ran to zero. Two of the findings were defects in the PLAN
rather than in the implementation -- a brief that put both goods-sale lines on
one round row, producing a self-referential "General sale -- Needed for: FC
presale, General sale" that `resolve_round_requires` makes structurally
impossible in production, and a dual-time example using a comma where
`timezones.py` emits a middot. One fix round then introduced a new defect of
exactly the class it was fixing: an invented `1 on sale` fold chip naming a state
`_FOLD_KINDS` -- a closed vocabulary -- cannot produce. And **two task reports
asserted something their own diff did not contain** -- caught, both times,
because review here checks the diff and not the report.

**One thing was deferred out on purpose and is now Proposed #17:** the demos are
one grammar-migration behind the app. Their `.banner` is a pre-G2 top concept-bar
sharing a class name with the app's G2 callout, and two separate tasks each had
to work around it with a compound rule. That is a migration, not polish, and
folding it in would have turned a polish pass into one.

Revision pass: recorded in the narrative above and in the note at the end of
Proposed -- one removal (this entry), one insertion at #17, the former #9-#17
renumbered to #8-#16 by position alone, and nothing re-ranked on merit. #18-#20
keep their numbers, since the removal and the insertion cancel.

### Outcome correction: taking a recorded result back (2026-08-11)

Branch `outcome-correction`, six tasks, spec
`docs/superpowers/specs/2026-08-11-outcome-correction-design.md`, plan
`docs/superpowers/plans/2026-08-11-outcome-correction.md`, sketch
`docs/superpowers/demo/dekimasen-outcome-correction-sketch.html`. **No
migration** -- the feature deletes rows and adds no column, which is most of
why it is small. Raised by the owner that morning asking whether a user can
backtrack on their 当選/落選 settings; filed at the head of Proposed deliberately
UNRANKED because it was already on a branch, and shipped the same day, so it is
the only entry in this file to ship without ever holding a number.

**It closes the 2026-08-04 irreversible-APPLIED half**, which had outlived the
entry that raised it. That entry (`Per-leg opt-out suppression reaches every
surface`, below) was filed at #1 on a correctness argument SHARPENED by
irreversibility -- Home offered APPLIED, which `record_round_outcome` will not
take back, on a round whose only leg the reader had already said they were
skipping -- and the build that emptied it fixed the suppression and left the
irreversibility standing. This is that half: a mis-pressed "Not applying" no
longer pins a round forever and no longer silently ends the campaign by dropping
its RESULTS and PAYMENT anchors.

**One idea, two surfaces, and no new answer button anywhere.** A correction
returns a round (or one leg of it) to the state it was in before anyone pressed
anything, and the existing capture buttons -- which already encode which answer
is offerable when -- take over from there. That is what kept the build small:
`clear_round_outcome` (`db/core.py`) is the ONE new writer and the only path
that deletes a `RoundOutcome`/`RoundOutcomeDay`, it owns its own
`reinstate_user_rules` so no call site can forget the resync (invariant 2), and
a whole-round clear needs NO downstream reader to change at all, because the
round returns to "no row" -- the common case the model was already built around.
`POST /rounds/{id}/outcome/clear` (`web/routes/outcomes.py`) reuses
`_outcome_response` verbatim. On the concert page a quiet `.reopen` "Change"
sits on round rows and REPLACES "Nothing to do", which was only ever true while
there was no way back. In Discord, "Change my answer" rides on every REPLY to an
outcome press, and the stale flat Won/Lost pair -- the accidental backtrack that
was the only one that existed -- is now guarded and answers with the real one.

**Three owner rulings came out of the build, all of them corrections to the
spec.** (1) A fully-answered multi-leg round must clear PER LEG: the spec mapped
the affordance onto the per-day branch, which such a round never reaches, so the
rule of record became "a clear posts `day_id` exactly when the leg owns its own
answer, whatever branch the row fell into" -- resolved once at the top of the
macro rather than per branch. (2) The correction must survive the results
moment: an APPLIED multi-leg round offered the clear right up until its results
were due and then lost it, at exactly the moment somebody goes looking, since
"results are out" is when you discover you told the app the wrong thing. (3)
**The correction follows the capture buttons** -- wherever a card lets you
record, it lets you un-record. A `leg_off` suppression withheld the clear on a
cancelled or opted-out leg on perfectly good reasoning, and was still wrong
because it was STRICTER THAN THE CAPTURE RULE BESIDE IT; measured, a round
applying only to dead legs on a live concert went from one clear form on the
page to zero while the same card still offered "I won" / "I lost". Writable but
not un-writable. The root cause is recorded with the ruling: the surprise was
the mismatch, not the correction.

**Two defects the reviews found that the plan's own code would have shipped.**
`row.leg_result is not None` is NOT "this leg owns a row" -- `_leg_result_for`
DERIVES one for every covered leg when a WON/PAID/LOST round has zero day rows,
so a single-leg WON round (the commonest shape in the app) would have posted a
`day_id`, deleted nothing, and re-derived itself to APPLIED instead of
unrecorded; `has_day_results` is the switch that turns that fallback off and is
now half the gate. And `preventDefault()` alone does not stop htmx: verified
against the vendored 2.0.4, its submit handler binds to the form in the BUBBLE
phase and never consults `defaultPrevented`, so the confirmation dialog would
have asked permission for a write that had already landed. `stopPropagation()`
in the capture phase is the difference between a dialog and theatre.

Six tasks, ~2,400 lines including tests, 2,786 passing at the end. Every task
ran its findings to zero across up to three fix rounds, and the mutation
discipline earned its keep twice: an inverted branch survived the reviewer's own
suggested assertions, because the inversion lived in the DEFINITION of `perLeg`
and every assertion that READ `perLeg` was true whatever it meant. Task 4 was
additionally browser-verified on a throwaway DB -- five gated presses, zero
POSTs in the server log.

Revision pass: recorded in the narrative above -- one insertion at #14 (two
narrow edges this build deliberately did not widen into), the former #14-#19
pushed to #15-#20 by position alone, #8 grown by a measured CSS defect this
build reproduced in already-shipped markup, and nothing re-ranked on merit. The
renumber the in-flight note deferred turned out to be a no-op, which is the
point of having deferred it.

### Round watch: the quiet-ladders surface (2026-08-11)

Branch `round-watch`, eight tasks, spec
`docs/superpowers/specs/2026-08-11-round-watch-design.md`, plan
`docs/superpowers/plans/2026-08-11-round-watch.md`, migration `0671edabe2ac`
(`concerts.quiet_since_utc` and `concerts.ladder_rechecked_at_utc`, plus a
backfill). This was Proposed #2 -- and #1 on impact for its whole life, the
gate above it delivering nothing to anyone -- filed 2026-08-05 at the end of
the first full triage session with its evidence still warm. **It ships the
CHEAPEST of the three shapes that entry recorded, and only that one.** The
other two are re-filed as Proposed #2 above and remain wanted.

**The problem, kept verbatim because the successor entry points here for it.**
Discovery's sweep answers "what exists that you are not tracking"; NOTHING
answered "what changed about what you already track". A round announced after a
concert is imported was invisible: no sweep visits the concert's own pages, no
surface listed ladders that had gone quiet, and the reminder machinery can only
plan from rounds it has been given. That is the app's core promise failing
silently -- a user who followed the right artist, got the 🆕 DM, and still
misses the lottery because the round arrived after import. The 2026-08-05 batch
had the evidence in triplicate: ブシロード20周年記念ライブ imported with `rounds: []`
because its page says 出演日程やチケットの詳細は後日発表 (correct that day, silently
wrong from the day tickets are announced), ゾンビランドサガ2027 and the 九九組
orchestra live likewise; and the sharpest instance caught only by accident,
石川大観光Ⅱ and the 103期卒業公演 imported with their 最速先行 alone, their missing
アップグレード rounds surfacing solely because fan-calendar leads happened to name
them. The 2026-08-10 grounding change sharpened it once more: some rounds now
exist ONLY on Eventernote, because an official page drops a round once it
closes, and a ladder assembled from two sources that each forget different
parts of it is exactly a ladder that goes stale in a way only a re-check
notices.

**One predicate, in one place, reusing the signal that already shipped.** A
concert is quiet when it is not dead, its latest live leg is in the future OR
it has no legs at all, and no round of it holds a future moment. That third
clause is `next_anchor_at`, PROMOTED out of `db/core.py`'s `_next_anchor_iso`
-- the agent read API's own catalogue-level "earliest future moment among live
rounds", whose `null` already meant precisely this -- with the ISO version
becoming a one-line wrapper over it. Two definitions of "future anchor" free to
drift apart is the defect that promotion prevents, and the test that pins it is
the one asserting a concert WITH a future anchor is absent from the list. The
whole predicate runs in Python over one unfiltered `select(Concert)`, because
`is_round_cancelled` is Python and ~157 productions is cheaper to scan than a
SQL transliteration would be to keep honest.

**The spec corrected itself mid-plan on a state the schema forbids**, which is
worth recording because the test written for it would have asserted something
impossible. Its first draft ruled that "dated legs decide when a concert has
both dated and undated ones"; `ConcertDay.starts_at_utc` compiles to
`DATETIME NOT NULL`, so no leg is ever undated. What the clause actually
distinguishes is a concert with ZERO `ConcertDay` rows -- a skeleton import,
or a `duplicate_concert` clone -- from one whose legs have all been performed.
Past concerts fall off by themselves, so the list drains and never accumulates.

**Two stamps with two different owners, both belonging to the current quiet
spell.** `quiet_since_utc` is system-owned; `ladder_rechecked_at_utc` is the
Checked button. When a concert leaves the list the pass clears BOTH, so a
concert that goes quiet, is checked, recovers a round and goes quiet again
arrives unchecked -- the earlier check answered a different question. The first
column is named "first observed quiet" rather than "went quiet" because the
migration stamps EVERY concert with a blanket `UPDATE` rather than backfilling
the quiet ones -- a predicate backfill would mean transliterating
`next_anchor_at` and `is_round_cancelled` into SQL, where the copy is free to
disagree with the real one, which is the drift the promotion of
`next_anchor_at` exists to prevent. A non-quiet concert's stamp is cleared by
the first pass; what the blanket buys is that NO concert is a newcomer on it,
so the first tick after deploy DMs nothing instead of announcing the entire
back catalogue. Under the other name every one of those stamps would be a lie.

**The pass runs EVERY TICK, with no cadence clock, and that is the one place it
departs from the discovery sweep it otherwise copies.** The sweep's 24-hour
clock protects 86 third-party fetches ending in a DM. This is a query and a
diff over the local catalogue, and the reconcile is SELF-IDEMPOTENT -- a
stamped concert is not a newcomer -- so a clock would protect nothing, would
delay a notice by up to a day, and would turn `quiet_since_utc` from a
measurement into "N days since the pass noticed". The stamps and the queued
notice commit in ONE transaction, which is what makes the notice exactly-once:
stamps first loses the DM on a crash, notice first repeats it. No newcomers
means no DM, and at a per-minute cadence that is load-bearing rather than
tasteful -- a "nothing found" note here would be 1,440 DMs a day.

**The page derives its list live on every load**, so a scheduler failure can
never make it wrong and there is no "run now" button: unlike the sweep, there
is nothing to run. It is admin-only, English-only and unwrapped by `_()` like
every other `/admin/*` page; only the Preferences link is translated, the
precedent `/admin/discoveries` set. Rows sort never-checked first, then
longest-since-checked, then longest-quiet, and a checked row DIMS but is never
hidden -- the stamp answers "have I looked at this", and hiding would silently
promote it to "is this resolved", which it cannot answer. A concert checked in
March genuinely does grow a 一般発売 in July. Its copy block carries the rounds
the catalogue ALREADY holds beside each concert, which is what stops an agent
handed the list re-proposing rounds that are already there.

**What it deliberately is not**, all three recorded in the spec before a line
was written: it FETCHES nothing (no third-party request, no new host policy, no
new trust decision), it WRITES no rounds (whatever a re-check finds is typed
into the edit page by a human, the same shape `/admin/discoveries` has), and it
HIDES nothing. `ApprovedPublicHosts` had already made the heavyweight re-fetch
shape possible, and it was still not what this built: converting the failure
from silent to visible is most of the value and is the half that needs no
judgment about arbitrary ticket pages.

**The recurring find across the per-task reviews was the PROXY ASSERTION** --
nine of them over seven tasks, tests that passed for reasons unrelated to their
own names. The cause was almost always the same and is worth knowing before
writing a test against this page: `build_quiet_ladder_block` emits the concert
titles, leg dates and round labels into a `copy_text` block rendered
unconditionally in a `<pre>` beneath the table, so any bare substring assertion
about a ROW is satisfied by the `<pre>` alone -- verified by replacing the
row loop with `{% for row in [] %}` and watching the old assertions still pass.
The surviving tests assert row-only discriminators instead (the Checked form's
action, a `<td>`-wrapped label, an un-padded date behind a `\b` word boundary
that the block's `%d %b` cannot produce). One real bug hid behind exactly this:
`| unique` without `| list` is a generator, which Jinja treats as always
truthy, so the "no dates announced" branch never rendered -- and the bare
substring test passed anyway, off the `<pre>`.

### The agent read API (2026-08-08)

Branch `agent-read-api`, fourteen commits, spec
`docs/superpowers/specs/2026-08-08-agent-read-api-design.md`, docs
`docs/agent-api.md`. Not a Proposed entry before it was filed -- raised and
built the same day, on the owner's own diagnosis of a friction the AI-triage
arc had been running into since phase 1: every hop between the app and an
agent was copy-paste, in both directions.

**The problem was the copy-paste LOOP itself, not any one hop of it.** The
owner copied the discovery-lead block out of a DM or `/admin/discoveries`
into the agent; the agent emitted YAML with no way to check its own work
against the catalogue; the owner pasted the result back at
`/concerts/import`. Four frictions came out of that, confirmed by the owner
the day this was scoped: the agent cannot see the catalogue, so it
re-proposes duplicates and invents tag names that match nothing; feeding it
leads is manual; iterating on a draft is manual, since the agent cannot read
back its own committed draft or the evidence/rejection result the completion
pass produced; and the owner sits in every step. Read surfaces already
existed -- `GET /admin/export.zip`, a concert's `export.yaml`, the
`/admin/discoveries` paste block -- but all three are browser downloads
behind a session cookie, unreachable by a program.

**It is READS ONLY, and that is the load-bearing decision, not a phase-1
placeholder.** `src/app/web/routes/api.py` declares nothing but `@router.get`,
swept by a test that fails if any other method is ever registered under
`/api/v1`. `POST /concerts/import/draft` -> `/concerts/import/pending/{id}/commit`
stays the only write path into `concerts`, exactly as it already was for a
human pasting YAML. The owner's approval was never friction that leaked in --
it is the safety property the entire AI-triage build (both completion phases)
was constructed around, and reads were sequenced deliberately FIRST because
they make the WRITES question decidable: once the agent can see the
catalogue, leads and its own drafts, the quality of its proposals becomes
observable, and that observability is what would eventually say whether
letting it commit unread is safe. Designing autonomy first would have meant
guessing.

**Auth reuses the calendar feed's shape rather than inventing a second one**
(invariant 5 already named this pattern for "any future personal-secret-link
feature"): a new nullable, unique `User.api_token_hash`, minted at
`POST /me/api-token` in Preferences, shown once, `secrets.token_urlsafe(32)`
with only the SHA-256 stored. The one deliberate divergence from the
calendar feed is transport -- `Authorization: Bearer <token>`, a header, not
a URL, because unlike a calendar client an agent can send one, and a header
keeps the credential out of access logs, browser history and `Referer`. The
token resolves to the same `SessionUser` the cookie path builds, so
`is_editor`/`is_admin` mean exactly what they mean everywhere else and there
is no second permission model to drift from the first.

**Four endpoint families, each scoped to the tier its content actually
needs.** `whoami` (any token) turns "my token doesn't work" into one
request. `/concerts` and `/concerts/{event_id}` (any token -- the catalogue
is already public at `/discover`) answer "do I already have this?", the
latter carrying `draft_yaml`, the SAME `concert_export_yaml` output
`add-concert` already writes and `import_commit` already reads back, so an
agent round-trips it directly instead of the API inventing a second concert
schema to keep in sync. `/tags` (any token; see the owner ruling recorded in
`api_tag_rows` and the spec for why `eventernote_url`/`address`/
`location_url` ride along) is the vocabulary, served, so an agent stops
proposing tag names that match nothing. `/leads` (admin only, same audience
as `/admin/discoveries`) is the open discovery queue. `/drafts` and
`/drafts/{id}` (scoped to the token's own user, 404 not 403 on another
user's row -- invariant 5) close the iteration loop: an agent reads its own
draft text AND the completion pass's evidence/rejection YAML together,
without the owner relaying either half by hand.

**Paging is offset-based, at the owner's explicit request** (2026-08-08)
after the trade was laid out: at current volumes (dozens of concerts, tens
of leads, ~100 tags) a capped `limit` alone would have sufficed, and
`offset` is insurance against growth rather than a present need. Every list
carries a totally-ordered sort with `id` (or, for `/tags`, the already-unique
`slug`) as the final tiebreaker -- offset paging over a non-unique key
repeats or drops rows between two calls even with nothing being inserted,
because SQLite is free to order ties differently call to call. `limit` over
the 500 cap is a 422, not a silent clamp, matching `web/paging.py`'s stated
doctrine: an agent asking for 5000 and receiving 500 with no signal would
believe it had read the whole set.

**Two footguns named rather than left to be found the hard way.** `/api/v1/*`
needed an explicit carve-out from `web/app.py`'s HTML-error-page machinery,
since an agent's request looks exactly like a browser navigation to that
code -- every response stays JSON even for a request carrying
`Accept: text/html`. And `draft_yaml`'s timestamps are JST while the rest of
the envelope is UTC, because the draft format has to stay byte-compatible
with what the editor forms and `parse_draft` already expect; both the spec
and `docs/agent-api.md` state the rule by name (never parse `draft_yaml`'s
timestamps as UTC) rather than trust a reader to infer it from two
differently-shaped datetime strings sitting next to each other.

**Delivery data stays out entirely**, on the same invariant-4 grounds that
keep `/admin/deliveries` the only surface naming DM recipients: nothing under
`/api/v1` touches `delivery_log` or anything that names who received what.

A final whole-branch review (this same day) found the branch mergeable and
raised documentation-accuracy and test-strength items rather than design
issues -- an owner ruling recording that the `/tags` tier crossing is
deliberate (`api_tag_rows`'s docstring, the spec), a closed hole in the
read-only sweep (`include_in_schema=False` routes were invisible to an
OpenAPI-schema-based sweep; it now walks the real routing table), and two
tests that asserted less than their names claimed. Folded into the same
merge rather than a separate entry, since none of it changed what shipped.

### AI completion of a skeleton draft (AI triage, phase 2) (2026-08-06)

Branch `draft-completion`, twelve tasks, spec
`docs/superpowers/specs/2026-08-05-draft-completion-design.md`, plan
`docs/superpowers/plans/2026-08-05-draft-completion.md`, migration
`2fa4d11a473a` (`fetch_domains`, plus `pending_drafts.completion_yaml` and
five `triage_runs` columns). This was Proposed #3, filed the day before by the
owner as the second half of the AI-triage build; it shipped a day later
because phase 1's calibration verdict -- the thing it said it was waiting for
-- came back good.

**One button on `/concerts/import/pending`, and it still commits nothing.** A
press writes a `TriageRun` with `kind="complete"`; the next 60s tick picks it
up and, for up to `COMPLETION_DRAFT_CAP` (15) of that admin's pending drafts,
reads the `official_url` the draft ALREADY NAMES, extracts the page text, and
asks for the ticket rounds. Survivors are merged into the draft by rewriting
exactly one key, `rounds:`; the draft is still a `PendingDraft` whose preview a
human presses Create event on, so `import_commit` stays the only write path
into `concerts`. Reusing the phase-1 run row through a `kind` column rather
than a second table is what keeps the request/pickup handshake, the budget
shape and the re-stamp-after-rollback rule existing once -- and the tick picks
up the oldest requested run of ANY kind, so the two halves serialize by
construction.

**Evidence grounding is phase 2's `strip_rounds`, and the locality rule is an
owner ruling.** Phase 1 could promise honesty cheaply: it emitted no rounds and
stripped any the model invented anyway. Phase 2 emits rounds, so the promise is
earned per round, in code: the model must quote the page line it read each
timestamp from, and `domain/round_evidence.py` drops any round whose quote it
cannot find in the same text the model was given -- plus the nastier case, a
quote that IS on the page but does not carry that timestamp. **A review defeated
the looser rule this feature was specced with**, and the owner ruled the
tighter one (2026-08-05, recorded in the spec): "do the digits appear somewhere
in the quote" validates a claimed 01:00 and a claimed 10:00 against a perfectly
correct quote of `申込締切 2026年1月10日(土)23:59` -- the hour matches the `1` of
`1月`, then the day -- and a model that simply quotes the whole page validates
anything assembled from digits anywhere on it. So the rule is CONTIGUITY: month
immediately followed by day, hour the very next number token after that date
and immediately followed by minute, the date-to-time span capped at 60
characters and the whole quote at 200. The accepted cost is false rejections on
some phrasings, and that is the trade the whole feature is built around -- a
rejection is visible, carries its reason, and costs one round typed by hand,
while a false accept is a fabricated deadline reaching a real user as a real
reminder. NOTHING IS DROPPED SILENTLY: rejections reach the preview in a banner
with their reasons, because a real deadline quietly discarded is exactly as
harmful as a fake one quietly kept.

**One known false rejection, deferred with its fix already verified.** A
same-day window on ONE line -- `受付期間 2026年1月10日(土)10:00〜23:59` -- passes
its OPEN time and rejects its CLOSE, because the hour must be the token right
after the day and the second time on a dated line is unreachable. The
refinement is written down rather than left to be rediscovered: keep
hour-adjacent-to-date and the span cap, but ADDITIONALLY require that no other
date pair intervenes between the matched date and the matched time. That was
verified against the four false accepts that motivated the locality rule and
closes none of them, so it is safe -- it simply did not ship, because this
failure is visible, carries its reason, and costs one timestamp typed by hand.

**The fetch policy widened, and an approval queue is what pays for it.**
`fetching.py` took a host STRING; it now takes a host POLICY. `PinnedHost` is
every pre-existing caller unchanged; `ApprovedPublicHosts` is this pass's, and
it is the first fetch in this app not pinned to a host named in code -- a
draft's `official_url` is by nature somebody else's domain. Three things stand
in for the pin: https only, every resolved address public unicast (ALL of them,
not any -- one public and one private answer is a rebinding setup), and the
host approved BY NAME by an admin at `/admin/fetch-domains`. A human is what
the pin became. The same policy runs on every redirect hop, so a redirect off
an approved host onto an unapproved one is refused. The SSRF review of that
widening earned its keep twice over: `ip.is_global` reports the IPv6 WRAPPER's
classification rather than an embedded IPv4's, so `::169.254.169.254`,
`::ffff:0:a9fe:a9fe` and `64:ff9b::a9fe:a9fe` -- all three encoding the
Lightsail metadata endpoint -- each read back global unpatched, and the review
also caught a synchronous `getaddrinfo` on the one asyncio loop this whole
process shares. Both are closed and pinned by a 29-row address-family table.

**The paste fallback is not a lesser path.** "Fill rounds from a page I paste"
on the preview runs the IDENTICAL `complete_one`, so the two cannot drift on
what counts as a grounded round -- the fallback exists because the fetch
declined, not because the rules change when it does. It covers a
JavaScript-rendered vendor page, an unapproved host, and any host the owner
would rather not put on the list at all.

**Cost is bounded the same way phase 1's is.** 15 fetch+call pairs per press
whatever the queue's size, fetches sequential with a 1s pause, a total deadline
per fetch, a wall clock over the loop (the cap bounds the CALLS; only a clock
bounds the TIME) and `heartbeat.beat()` per draft. A draft is attempted at
MOST ONCE -- `completion_yaml` is written even when the reply or the merge is
unusable, because the call was already paid for and a second press must not pay
for the same junk twice -- while a draft skipped without a call (no URL,
unapproved host, dead fetch) stays a candidate. `SQLAlchemyError` is the one
per-draft failure that is NOT absorbed: a poisoned session means the remaining
fourteen paid calls would write nothing at all. No new env vars; it reuses
`TRIAGE_ENABLED` and the DeepSeek keys, and `deploy.md` carries the operator
half, including the fact that the first press completes nothing on purpose.

Full suite 2462 green.

**The calibration press ran on 2026-08-09/10, and its verdict is folded in
here** -- the Proposed entry that carried it (#1 from 2026-08-06) shipped
nothing itself, so per its own closing instruction it was DELETED on 2026-08-11
rather than moved to Shipped, and this paragraph is where its answers live.

**Both questions it was filed to settle came back positive.** Does the model
reliably emit `evidence` at all? Yes: across three models over the real
catalogue, 39 rounds were accepted with ZERO invented timestamps. What is the
false-rejection rate on real Japanese pages? Livable, and -- the useful finding
-- its causes turned out to be namable bugs rather than the contiguity trade
this entry feared. A live press grounded 16 rounds and rejected 8, and SEVEN of
those eight rejections were CORRECT: the page stated a date only and the model
supplied a time nobody wrote, which is the fabrication the module exists to
stop.

**What the press actually found was that the CHECKER was wrong, in one place
badly.** A mutation harness over the evidence corpus -- 129 real timestamp
claims, each with its page -- shifted every claim forward one year and measured
how many the checker still proved: **111 of 129 ACCEPTED**. Rule 4's locality
covered month, day, hour and minute; the YEAR was checked apart from all of
them against the WHOLE PAGE, and this catalogue is full of pages whose SHOW is
next year and whose DEADLINES are this year, so a quote plainly reading 2026
proved a 2027 claim. A deadline claimed a year late fires its reminder after
the real deadline has passed, silently, which is the exact failure this module
exists to prevent. Nine commits shipped on `triage-calibration-fixes`
(PR #146, merged 2026-08-10). The year is now STATED evidence or ARITHMETIC
against the leg dates and never "somewhere on the page" -- requiring it in the
quote was measured and refused, since only 92 of the 129 claims carry it and
「9月13日（日）23:59」 is the ordinary shape (harness after the fix: 111 accepted →
0, with zero disagreements over the 129 real claims). A 12-hour Japanese time
is now REFUSED rather than converted: `申込締切：2026年7月28日午後11時59分` had been
ACCEPTING a claim of 11:59, twelve hours early, while refusing the true 23:59
-- both a misreading and a correct reading came out wrong, and the misreading
was the one that reached a user as a real reminder. A bare closing day now
inherits its month from earlier in the same quote (`…7月14日（火）21:00 ～ 28日（火）
23:59`), in a THIRD matcher rather than a looser main loop. Pass 3's rounds are
GROUNDED instead of deleted, which is what recovered a complete three-round
ladder surviving only on Eventernote. The classify prompt's input is BOUNDED
(it failed two ways on a real 511-lead queue, both size rather than judgment)
and `max_tokens` is sent explicitly. And `lovelive-anime.jp` earned a per-host
User-Agent exception -- a table of NAMED exceptions, never a global switch --
because its CDN answered the honest agent 403 and so hid 8 of the 12 exported
concerts, and 28 of the 47 hand-typed rounds, from the measurement entirely.

**The one known false rejection this entry deferred is still deferred.** The
same-day window on one line (`受付期間 2026年1月10日(土)10:00〜23:59`) still passes
its open and rejects its close; the press did not touch it, and the refinement
recorded above stays verified-but-unapplied for the same reason it did the
first time -- the failure is visible, carries its reason, and costs one
timestamp typed by hand.

### AI triage of discovery leads (phase 1) (2026-08-05)

Branch `ai-triage`, seven tasks, spec
`docs/superpowers/specs/2026-08-05-ai-triage-design.md`, plan
`docs/superpowers/plans/2026-08-05-ai-triage.md`, migration `ff500647fa9c`
(`triage_runs`). Not a Proposed entry in its own right: it is the
discovery-lead half of the in-app LLM extraction entry, unblocked the day
the owner bought DeepSeek V4 Flash credits and pointed at a different first
target than that entry had imagined. The import-page half stays Proposed and
was rewritten in place rather than closed.

**One button on `/admin/discoveries`, two phases, and neither one commits
anything.** Classify: one DeepSeek call over every open lead collapses repeats
into productions and rules keep-vs-dismiss against the 2026-08-02 scope ruling,
emitting the prune-list YAML the paste box already reads. Draft: for up to
`TRIAGE_DRAFT_CAP` (25) survivors, one Eventernote fetch and one call each
author a SKELETON draft in the `add-concert` vocabulary -- trilingual titles and
leg labels, legs, cast tags. The prune YAML is stored TEXT the owner still
pastes through the existing plan → apply screen, which stays the only path to a
dismissal; the drafts land as `PendingDraft` rows, so `import_commit` stays the
only write path into `concerts`. The whole design rests on that: **the model
speaks two formats this app already parses**, so bad model output dies at the
same boundary a bad agent draft does and no second validation vocabulary exists
to drift from the first.

**`rounds: []`, and the prompt is not what guarantees it.** The draft prompt
asks for an empty rounds list; `strip_rounds` then runs on every generated
draft in code, whatever the model returned, pinned by a test asserting the
property on the STORED text. Round research needs an official page plus
judgment and sits exactly where a hallucination would break this app's core
promise ("a deadline it names is real"), so it was carved out as phase 2 before
implementation started rather than attempted and walked back -- it was filed the
same day as its own entry, which also carried phase 2's one known hard question
so it would not be rediscovered, and which shipped as the phase-2 entry above.

*Superseded 2026-08-10 (owner ruling).* `strip_rounds` is gone; phase 1 now
runs the same evidence grounding phase 2 does, over page TEXT, and keeps the
rounds it can quote. The carve-out above was right for the reason it gave --
phase 1 had no way to tell a read deadline from an invented one -- and
`round_evidence.py` is now that way. The measurement that turned it: the claim
"Eventernote carries no ticket data" is simply false (the ladder sits in the
free-text description), and over 13 real productions `strip_rounds` deleted 7
real rounds, every one verifiable on its own page, some of which the official
page no longer states at all. The promise is unchanged and the test that pins
it is now the inverse pair: a round whose quote is not on the page is refused,
a round quoting the real line survives, and every refusal is written to the
draft's preview.

**The request stamp IS the `TriageRun` row.** Unlike the sweep, which stamps
the `DiscoveryState` singleton, triage wants per-run history, so the button
inserts a `status="requested"` row and the tick picks up the oldest one -- and
`stamp_discovery_run`'s two-halves rule then applies in ROW form: a rollback
restores the row to `"requested"`, so `scheduler/loop.py` re-marks it failed and
commits on the cleaned transaction, or a dead run re-fires 25 fetches and 26 LLM
calls every 60 seconds forever. Implementation found one refinement worth more
than the feature: **`session.rollback()` expires every attribute of every object
in the transaction, PRIMARY KEY INCLUDED on this aiosqlite stack**, so reading
`run.id` inside the failure handler raises `MissingGreenlet` instead of giving
back an id. The id is captured before the run starts. Any future post-rollback
bookkeeping keyed on a row has the same hole, which is why it is in CLAUDE.md
beside the rule it varies.

**Cost is bounded by construction, not by care.** One classify call per press
whatever the queue's size, at most 25 fetch+draft pairs after it, fetches
sequential with a 1s pause (25 parallel requests at a third party is how an IP
gets blocked) and `heartbeat.beat()` per production so a minutes-long tick does
not page the owner about a healthy app. `TRIAGE_ENABLED` gates the scheduler
pickup exactly as `DISCOVERY_ENABLED` gates the sweep, `DEEPSEEK_MODEL` ships
with NO default (hardcoding a guess at a third party's current alias starts
billing a model nobody chose), and tokens in/out are recorded per run so spend
is visible rather than inferred. The run queues ONE admin notice through the
outbox (invariant 4), kind `"triage"`, deliberately NOT in
`UNREPORTED_NOTE_KINDS` -- that set is for notices reporting ON deliveries, and
this one reports on a model's proposals.

**Prompt quality is not testable in CI and was not faked.** Tests use a fake
LLM client and a fake fetch, so nothing touches the network; what they pin is
the safety properties (rounds stripped, the gate, the pickup, the
re-stamp-after-rollback path, per-production skip-and-count, the duplicate
containment check, the rendered strip). Judgment is calibrated operationally
instead, on the first real press, for cents -- with `deploy.md` carrying the
guidance and the one ordering caveat the code makes: `open_leads` sorts
`event_date DESC`, so on a backlog longer than the cap the FURTHEST-FUTURE
productions are drafted first. If V4 Flash's Japanese-domain judgment
disappoints, the fallback needs no code: stop at the prune plan and the
classify half alone still cuts the queue by the largest factor.

Full suite 2287 green.

### The crawler trap, closed where the tree can see it (2026-08-04)

Branch `crawler-trap-hardening`, four tasks, no spec and no design discussion --
the diagnosis was run live during the outage itself, so the entry was already a
work order and the build only had to execute its three build bullets in the
order it listed them. Filed
at #1 that evening on the outage precedent, emptied the same night. No schema
change, no migration, no catalogue strings: the whole build is one template's
link attributes, one route, and two runbook paragraphs.

**`rel="nofollow"` on every Discover filter link, at ten sites.** Eight are
server-rendered -- the active-tag chips and the active-status chip in the
filter row, "Clear all", the sort links, the round-status facets, the tag-chip
macro every franchise/group/artist chip renders through, the region toggles,
and "Clear filters" -- and two are places the page's own script CREATES an
anchor: the active-filter chip factory and the "Clear all" link it appends
beside them, which set `.rel` at construction. Not one `href` changed, and
that is the property the entry insisted on: the chips stay real links, so the
page still degrades correctly without JavaScript, and a signed-in human who
reloads, bookmarks or shares a filtered view still gets the view back. The one
thing worth verifying rather than assuming was `updateLinks()`, which rewrites
filter hrefs on every client-side filter change -- it only ever touches
`.href` on anchors that already exist, so a server-set `rel` survives it. Two
tests pin all of it, one sweeping every rendered `/discover?` anchor for the
attribute so a NEW filter link cannot ship without it.

**The `/robots.txt` route, and the shape question answered instead of
guessed.** The entry deliberately left the directive's shape to build time
("check what the majority grammar actually supports rather than assuming"),
and the check came back clean in both directions: `?` is not a metacharacter
in the original 1994 robots grammar OR in RFC 9309, and both specify a
`Disallow` value as a literal PREFIX matched against the request's path plus
query. So `Disallow: /discover?` blocks every query-stringed Discover URL --
the entire combinatorial `?sort=…&tag=…&tag=…` space the 605-tag expansion had
made effectively infinite -- while `/discover` itself, the catalogue page that
genuinely should be indexed, stays crawlable. It also asks NOTHING of the
crawler: the wildcard alternative the entry offered would have needed the
`*`/`$` wildcard shape, which pre-RFC 9309 crawlers are not required to
implement, and would have been the broader directive of the two. The route is a
plain `PlainTextResponse` in `web/app.py` with that reasoning in a comment
beside it, so nobody re-derives the grammar question next time, and its test
pins both halves: the query-stringed space blocked, the bare page not.

**The two dashboard mitigations are now named in the runbook**, which was the
whole argument the entry was filed on -- the cure was already deployed and
lived entirely outside this repository, and a guard nobody can see from the
tree is a guard that gets lost. `docs/deploy.md` gained the Cloudflare WAF
rule (Managed Challenge when URI path is `/discover` AND the query contains
`tag=`, plus the AI-crawler blocking toggle) with the reason it is a CHALLENGE
and not a block written next to it, since `history.replaceState` puts filtered
URLs in the address bar and real humans therefore do issue `?tag=` requests.
It also gained the response-time UptimeRobot monitor the incident proved was
missing: the existing keyword monitor stayed GREEN for the entire half-day,
because `/healthz` answered 200 `"ok":true` in 72 seconds and a keyword
monitor has no latency threshold. Both entries say "dashboard-only, recreate
on re-setup" in as many words.

**Explicitly NOT built, per the entry's own line: caching or a cheap-render
path for anonymous filtered `/discover`.** That stays the heavyweight remedy,
and it is now reserved for exactly one trigger -- a challenge-passing crawler
firing the trap again. The known case is ended by three layers that cost
nothing to run: the edge challenge already live, plus these two in the repo,
and both named culprits (`meta-webindexer` under four browser-disguised user
agents, and `SemrushBot`) respect nofollow. Recording the non-build matters as
much as recording the build here, because the next person to meet a slow
Discover will reach for a cache first, and the reason not to is that nothing
has yet shown the two cheap layers to be insufficient.

Three new tests (two in `tests/test_discover.py`, one in `tests/test_web.py`).
Full suite 2255 green.

### The calendar story becomes the feed (2026-08-04)

Branch `calendar-feed-story`, six tasks, spec
`docs/superpowers/specs/2026-08-04-calendar-feed-story-design.md`, plan
`docs/superpowers/plans/2026-08-04-calendar-feed-story.md`. Raised by the owner
that morning ("adding each event with a new calendar event file just sucks --
let's make it a subscription link"), filed at #1 with all four candidate gaps
confirmed, and shipped the following morning. No schema change, no migration.
One terminology note carried over from the entry so it is not re-opened: the
owner said "caldav", but CalDAV is two-way SYNC and nothing here needed it --
the shipped feed was already the right protocol shape (one-way `.ics` over
HTTPS) and `webcal://` is that same URL with a scheme that makes apps
subscribe instead of import. This was UX and content, not protocol.

**The content ruling is the substance, and it is a rewrite of one function.**
`user_calendar_events` no longer reads `reminder_queue` at all. It derives the
user's LANDSCAPE over their tracked concerts: every live leg's show date, plus
each surviving round's next moments selected by that user's standing on it --
no outcome gives the future `opens_at_utc` AND `closes_at_utc`, APPLIED gives
`_result_moment` (results, falling back to the close), WON gives the payment
deadline, and LOST / NOT_APPLIED / PAID give nothing, because a LOST round's
auto-armed successor is an ordinary no-outcome round contributing its own
opens and closes. Future-only throughout. The effect is that **reminder rules
go back to meaning exactly one thing: when Discord DMs you** -- the surprise
behind the entry was a sparse preset producing a sparse calendar, which reads
as broken rather than configured, and that failure mode is gone by
construction rather than by a bigger default preset.

**No new suppression rule was invented anywhere.** Every exclusion routes
through the helpers the other read surfaces already consume --
`tracked_concert_ids`, `user_opted_out_day_ids` + `_round_fully_opted_out`
(the pair the opt-out build had shipped hours earlier), `is_round_cancelled` /
`all_legs_cancelled`, `covered_round_ids_by_concert`, and upgrade eligibility
-- each one batched query. That is what paid the entry's own inherited
constraint: "the feed must never carry an opted-out leg" held for free while
the feed read the queue, and this build is precisely the branch that stopped
sourcing from the queue, so the constraint was re-applied at the derivation on
purpose, which the entry had named as the one case requiring it.

**`CalendarEvent` gained a required `anchor`, because a no-outcome round now
emits two events with the same summary.** "Opens" and "apply by" have to be
distinguishable entries on somebody's phone, so the rendered summary carries a
short qualifier -- and the split follows the standing locale contract exactly.
The `.ics` feed stays CANONICAL (locale `None`: a URL has no viewer) and
qualifies with the Japanese ticketing terms the domain already speaks, from
`CANONICAL_ANCHOR_QUALIFIERS` in `domain/ics_export.py` -- a plain module-level
map, deliberately NOT gettext, because canonical text is by definition
untranslated. `/mydeadlines` passes the recipient's language as it always has
and qualifies through `_()` msgids in both catalogues.

**`/mydeadlines` inherits the landscape, and that is a deliberate behavior
change to a shipped command.** The cog reads the same function, so its answer
moves from rule-derived to standing-derived; accepted in design because one
derivation is the point and "my deadlines" answering from actual standing is
strictly more useful, with the cog's tests moving with it.

**The per-round downloads were deleted whole, not deprecated**, per the
entry's own ruling: the 📅 link in `_round_rows.html`, `GET /rounds/{id}/ics`,
and `domain/ics_export.py`'s single-event `build_ics`. A file is a snapshot
that rots the moment a deadline moves -- invariant 2 re-plans the queue and
nothing re-plans a file in somebody's calendar app -- while the feed re-plans
on every fetch. `build_calendar` and its VEVENT helpers stay (the feed renders
through them); a 404 test pins the route's absence, and three tests in
`test_venue_regions.py` that had been riding the deleted route were cleaned up
with it.

**The UX is one partial on three surfaces.** `_feed_links.html` renders a
freshly minted URL identically for Preferences, welcome step 4, and the
concert page's new "📅 Calendar" dialog -- a `webcal://` "Open in calendar
app" link (the https URL with its scheme swapped), the URL in a copyable box,
and a copy button -- replacing three hand-rolled copies so the ergonomics
cannot drift. The dialog is server-rendered in three states (no feed yet, a
feed already on, or the shown-once URL after a mint), is a regular action
beside Edit/Export rather than a kebab item (the kebab stays
destructive-only), and closes through base.html's global drag-safe handler
like every other dialog. The mint route's `next` grew from a two-path
hardcode to `_allowed_next`: `safe_next` FIRST (invariant 5's standing
open-redirect guard, a same-origin path or None), then a shape allowlist --
`/preferences`, `/welcome`, or a `/concerts/` prefix -- because the concert
page is a third minting surface and hardcoding every concert is not a list
anyone maintains. Everything else still falls back to `/preferences`.

**Copy became true in all three languages.** Welcome step 4 and Preferences no
longer describe the feed as a mirror of reminder rules; both now say what it
is -- every show you follow, and the deadlines that still need you. Moved but
unchanged msgids were kept byte-identical so their translations survived, and
the msgids the deletions orphaned fell out of both catalogues as obsolete.

Twelve new tests in `tests/test_calendar_landscape.py` (one per standing
state, plus the exclusions, the future-only rule, the untracked case and the
LOST-to-next-round handoff), a reworked `test_calendar_feed.py`, and the
`test_ics_export.py` cases that survived `build_ics` ported to
`build_calendar`. Full suite 2250 green.

### Per-leg opt-out suppression reaches every surface (2026-08-04)

Branch `leg-opt-out-surfaces`, seven tasks, data migration `db750444962a`, plan
`docs/superpowers/plans/2026-08-04-leg-opt-out-surfaces.md` (no spec -- the
root cause was verified against the tree before the entry was even filed, so
the entry WAS the spec, and the plan carried the rest). Filed as #1 that
evening on the correctness precedent, shipped the same night, which is the
second time in two days a defect entry has lived for hours.

**The shape is one rule and one loader, and that is the whole fix.**
`_round_fully_opted_out(round_, opted_out_day_ids)` is invariant 8's round
rule as a single predicate -- non-empty `applies_to`, every named leg opted out
-- reading RAW `applies_to` so the empty/all-legs convention (a General round
names no leg) can never be covered by any set of opt-outs. `user_opted_out_day_ids`
is the one batched loader behind it. `_apply_outcome_suppression`, which used
to hold the rule inline and privately, was refactored onto both rather than
copied from: the entry's diagnosis was that the rule existed in exactly one
pass and every other surface never asked, so leaving a second copy anywhere
would have shipped the same bug with a longer fuse.

The surfaces it now feeds: `sync_rule`'s DAY candidates (filtered beside the
`cancelled` check, which is what removes show-start rows from `reminder_queue`
and therefore from the `.ics` calendar feed, the show-start DM and
`/mydeadlines` at once -- one filter, four symptoms); `my_deadline_rows` for
both row shapes, fixing Up next and Coming up (`UpcomingDeadline` gained
`day_id` so an EVENT_START row can say which leg it came from); `board_cards`'
LIVE card set; the concert page's `_needs_you` veto and `pending_capture_row`
skip, via a new `RoundRow.opted_out`; and `/setup`'s application rows and
tallies.

**The board question the entry left open was settled by test, not argument.**
The entry said an open round on fully opted-out legs was "expected to keep a
card in *Open now* -- skimmed, not pinned; the fix's tests should settle it
either way". They settled it the other way: a fully-opted-out round leaves the
live card entirely, and with nothing else placing the card, the card leaves the
board -- the exact mirror of what a leg-cancelled round already does. The DEAD
path deliberately keeps every round, because a dead card is standing-only: no
actions, no countdown, nothing an opt-out could suppress.

**Two surfaces were checked and deliberately NOT changed, which is a result.**
`discover_statuses` stays blind on purpose: its event-state pill is a fact
about the catalogue rather than about the viewer (a concert-level prune does
not hide catalogue state either), and its standing half renders `RoundOutcome`
records, which an opt-out never touches by invariant 8's own rule. `_wants_you`
stays blind too -- the veto belongs in `_needs_you`, and Home's rows are
filtered upstream, so the shared primitive stays as ignorant of opt-outs as it
already is of coverage and cancellation. The concert page's row RENDERING and
capture gates stay open for the same family of reason: the page shows the whole
campaign in context, it is where you opt back in, and a record is never hidden.

**The data migration exists because the queue is a materialized outbox.**
Filtering `sync_rule` fixes what gets PLANNED; rows planned before the fix sit
in `reminder_queue` until some unrelated write resyncs that rule, and the
scheduler delivers them meanwhile -- which is the owner's own repro. Worse, the
one write that should have cleared them was the one restoring them:
`set_leg_opt_out`'s invariant-8 resync re-ran the same blind `sync_rule`.
`db750444962a` deletes exactly the stale set (unsent, day-anchored, on a leg
its rule's own user opted out of) and nothing else -- sent rows are history,
and round-anchored rows were never stale because the round pass has run at
write time since per-leg opt-outs shipped. Downgrade is a deliberate no-op:
re-planning is always safe (invariant 2), so there is nothing to restore.

**The half this build did NOT close was closed on 2026-08-11.** The entry was
filed at #1 on a correctness argument sharpened by IRREVERSIBILITY -- Home
offered APPLIED, which `record_round_outcome` will not take back, on a round
whose only leg the reader had already said they were skipping -- and this build
fixed the offering while leaving the irreversibility standing, which is why the
entry stayed half-open in Shipped for a week. Outcome correction (above) is that
half: `clear_round_outcome` is the way back, on the concert page and in the DM.

Sixteen new tests in `tests/test_leg_opt_out_suppression.py` plus one migration
test. The property that earns most of them is the PARTIAL case -- one leg of
two opted out -- pinned separately on every surface, because it survives BY
DESIGN (mirroring partial cancellation) and a filter written slightly too
eagerly would have deleted it silently on any one of them.

### Onboarding is decided by `welcomed_at`, not by row existence (2026-08-03)

Branch `fix-onboarding-skip-and-dialog-drag`, migration `aba3e97e4467`, plan
`docs/superpowers/plans/2026-08-03-onboarding-skip-and-dialog-drag.md` (no spec
-- the root causes were verified against the tree and written into the plan
instead, which is the right shape for two bounded defects). Filed as #1 by the
owner that morning after an admin `delete_user` + re-login walked him straight
past the wizard. **It shipped exactly as the entry prescribed**, which is worth
recording in a file that also carries the Python-pinning embarrassment: the
entry named the column, the check and the backfill, and all three survived
contact with the code.

`User.welcomed_at` is aware UTC and NULL means the wizard has never finished.
It is stamped at BOTH exits -- `advance()` crossing into done, and `skip_all()`
-- and the OAuth callback keys the `/welcome` redirect off it instead of asking
whether a `users` row existed a moment ago. The existing `onboarding_step`
column could NOT answer this, and the reason is the same one that decides the
backfill: its own migration set every pre-existing row to 0, so a real
pre-wizard web user and a bare row the bot's `ensure_user` minted are
indistinguishable by step. At migration time nothing can tell them apart
either, so every existing row is grandfathered from `created_at` and nobody is
re-wizarded.

**One refinement the entry did not name, found in the callback rather than
predicted.** `is_new_user` gated two things, not one: the redirect AND the
language-cookie seeding. Only the redirect moved. The seeding stays keyed on
row absence deliberately, because `users.language` cannot distinguish
"defaulted to en" from "chose en" -- the moment before the row exists is the
only safe moment to seed it, and a column about the wizard has nothing to say
about that.

**One deliberate behaviour change, stated so it is a decision rather than a
surprise:** every login with `welcomed_at` NULL now goes to `/welcome`, not
just the first. An unfinished onboarding is unfinished, and the exit is one
click because skip-all renders on every wizard screen. Five new behaviour tests
cover it, including the owner's original repro (`delete_user`, log in again,
land on `/welcome`); four pre-existing `test_auth` tests were updated only by
having the user finish the wizard between their two logins, which is what they
always meant -- no assertion was weakened to make the change fit. The entry's
doc-rot rider went in the same commit: `service.delete_user`'s docstring had
claimed "no route or UI calls this" since before `POST /me/delete` shipped.

### Dragging out of a quick-create dialog no longer closes it (2026-08-03)

Same branch. Filed as #8 the same morning, unranked-in-anger and prescribed
WRONG on purpose -- the entry said the symptom was undescribed, suspected the
backdrop CSS or a stacking problem, and per the measure-don't-reason rule
parked itself pending a real viewport. **The owner's one sentence re-routed the
whole diagnosis, and that is the durable half of this entry.** "Dragging from
inside of dialog to outside closes it", desktop, does not describe a backdrop
that renders wrong; it describes an EVENT bug, and it was settled by reading
two templates and one commit with no harness built at all. That is not an
argument against the measure-don't-reason rule -- it is the step in front of
it. One sentence of symptom is what tells you which kind of question you have,
and the entry was right to refuse to guess before it had one.

The root cause is the kind this file exists to catch. Commit `e23943d`
(2026-07-30) had already fixed this exact bug GLOBALLY, with a drag-safe
handler in `base.html` that closes a dialog only when the press and the release
agree on the target -- and its commit message claimed it covered "every dialog
in the app". It only touched `base.html`. Two dialogs carried their own LOCAL,
naive backdrop-click handlers that predate it and fire regardless of the global
guard, so the bug a commit message had documented as dead was still alive in
exactly the two dialogs an editor types multilingual names into, discarding
what was typed. A click's target is the nearest common ancestor of mousedown
and mouseup, which is why selecting text in a field and releasing outside is
indistinguishable, to a naive handler, from a real backdrop click.

**The fix was deletion**: both local handlers replaced by a comment pointing at
the global one, plus a sweep test in `tests/test_theme_and_tokens.py` that
fails if any template ever hand-rolls one again. Two things worth keeping. The
tag quick-create dialog had the identical bug though only the venue one was
reported -- a report names a symptom, not a scope, and the second dialog was
found by looking rather than by waiting for a second complaint. And the plan's
own suggested replacement comment QUOTED the forbidden line verbatim, which
would have made the new sweep test fail forever on the comment explaining why
it exists; the implementing task caught it and reworded. A guard that forbids a
string is tripped by anything containing that string, documentation included.

### Calendar-feed discovery, and characters leave the daily sweep (2026-08-03)

Branch `calendar-discovery`, seven tasks, migration `d446e6c0a3e6`. Spec
`docs/superpowers/specs/2026-08-02-calendar-discovery-design.md`, plan
`docs/superpowers/plans/2026-08-02-calendar-discovery.md`. Never a Proposed
entry: it exists because the owner asked what the ~90-tag im@s/LL character and
seiyuu expansion would do to the daily sweep, and the answer was bad enough to
reprioritize -- every tag with an `eventernote_url` joined the sweep, so the
expansion meant hundreds of extra daily fetches at a third party for pages the
app was reading one artist at a time.

**The investigation is the durable half, so it is recorded before the code.**
Two obvious sources were evaluated and REJECTED: `ll-fans.jp/data/event` and
`imas-db.jp/song/event/` are retrospective SETLIST ARCHIVES -- their own
descriptions say 過去, and imas-db's newest entry on 2026-08-02 was July 26.
They document shows after they happen, and discovery needs events before their
deadlines close. Eventernote's own `/users/ical` was checked too and fails for a
different reason: it exports events a USER marked attending, so it can confirm
what you already know and can never discover. What the same search DID find is
that both fan communities already publish forward-looking Google Calendars of
exactly the right thing -- maruamyu's アイマス関連イベント チケット申込期限 feed,
and the LL-Fans calendar family. Those are the find, and all three archives
remain good RESEARCH references for the add-concert drafting step, which needs
no code. (Recorded here so the next person searching for a cheaper source finds
the three dead ends already walked.)

**Nine feeds, each probed live before inclusion**, and the verdicts live in
`app/calendars.py`'s own header block rather than in a plan file nobody opens.
The probe corrected the plan twice: LL-Fans publishes EIGHT calendars, not the
four the plan knew about, and the main feed the plan called stale is not (1764
VEVENTs to 2027-03, 49 of them future) -- so nothing was dropped for staleness.
The site's own division of labour turned out to be load-bearing: the main
calendar carries EVENTS and the per-group subs carry 申込期限, which is why
`dates_are` is per FEED and why the subs and the main are separate roster
entries rather than one mixed feed. It also found the separator is an ASCII
`": "` and not the full-width `：` the plan guessed, which matters because
`ライブ映像無料公開:` is a real summary a bare `ライブ` prefix would swallow.

**`imas-tix` was kept with zero future rows**, deliberately and against the
build's own stated drop rule. A deadline calendar empties its forward window by
construction, the feed was demonstrably alive (DTSTAMP that day, July carrying
seven entries), and applying "no future VEVENTs means drop" literally would have
deleted the im@s half of the feature on a one-day sample. That judgment is the
one thing here that could be wrong, so it ships with a re-check date rather than
a shrug -- see the feed-silence entry in Proposed, which owns it.

**The migration was the dangerous step and was hand-written for it.**
`eventernote_event_id` became `source_event_id` (widened to 200): autogenerate
reads a rename as drop + add, which would have destroyed every existing lead's
external id -- the one column the whole discovery diff keys on -- so the
revision uses `alter_column(new_column_name=...)` and the test asserts the DATA
survives, not merely that the schema changed. Calendar rows namespace their ids
(`"<feed key>:<UID>"`) so one UNIQUE column serves both sources; `source` is
stored EXPLICITLY rather than parsed back out of the id; `date_is_deadline`
rides alongside because the imas feed's DTSTART is an application deadline, and
rendering it as a performance date would mislead precisely the person triaging
it. Both new columns carry server defaults, so every pre-existing row reads back
as the Eventernote lead it is.

**One sweep, one digest, and characters leave the rotation.** The calendar pass
runs first (cheap, bounded), pours into the same `record_discovered` call and
the same single DM as the actor loop, and a feed that fails is counted and
skipped without costing the artists behind it. It sits OUTSIDE the actor budget,
so the tick's worst case is the sum of both phases -- written down in
`discovery.py`, because a feed roster that grows must never starve the rotation
in front of which it runs. And the daily tag query now excludes CHARACTER tags,
**reversing a documented decision** (CLAUDE.md said the sweep was kind-blind):
a character's `eventernote_url` is her seiyuu's actor page, reading it daily for
an expanding catalogue is the cost this build exists to avoid, and the manual
per-tag button stays unfiltered because one fetch the owner asked for is not a
daily cost.

The parser is hand-rolled (`domain/ics_read.py`), warnings over failures like
every parser in this repo, and keeps only the DATE half of a DTSTART -- a lead's
date is a pointer, and inventing a midnight instant would put a deadline-shaped
fake into an aware-UTC schema. `申込締切` renders as an ADDITIVE prefix on the
date in all three surfaces (the DM digest, `/admin/discoveries`, the prune plan)
and never reorders the line, because `triage-leads` parses that block by field
position -- the skill's own doc was updated in the same branch to know both id
shapes and to say plainly that a fan-maintained deadline is still a pointer to
verify, not a round to copy.

Nothing here creates a concert. `import_commit` remains the only write path, and
that was reaffirmed rather than tested: a calendar entry is exactly as much
evidence as an Eventernote row, which is "this exists", and the no-invented-
deadlines rule is the reason the whole discovery arc has stayed read-only.

### Goods-sale rounds, and the item a round requires (2026-08-02)

Branch `goods-sale-rounds`, ten tasks, migration `f846bca262ad`. Never a Proposed
entry: the owner raised both halves in one breath, and they are one feature only
because the second needs the first to have anything worth pointing at. A merch
window had been going in as a mislabelled General sale, and nothing anywhere
could say that 最速先行 is unenterable without the CD sold two weeks earlier --
which is the single most common shape in Japanese ticketing and was invisible in
an app built for exactly that market.

**`RoundKind.GOODS_SALE` is deliberately cosmetic** ("Goods sale", 🛍️, both
catalogues, グッズ / 物販 / goods in the ramen.events heuristics). It adds no
behaviour branch, and the spec says so by citing the standing WISHLIST entry
about the nine -- now ten -- cosmetic kinds. A CD/BD sale that exists to hand out
serial codes STAYS `eligibility_item_sale`; the goods kind is for merch whose
point is the merch, and both are legal requires-targets because 抽選券付き goods
exist.

**Display-only was the owner's choice, option 1 of 3**, and the entry records it
so the ceiling is a decision rather than an omission. No per-user "I bought it"
capture and no suppression keyed on the link: that is the recorded LATER layer,
and it would hang off this FK exactly the way per-leg outcomes layered onto round
outcomes. A free-text "requires" note was rejected in the same conversation --
it cannot render the item sale's own deadline, and gives a future capture feature
nothing to key on. Storage is a single nullable self-FK
(`Round.required_item_round_id`, `ON DELETE SET NULL`, indexed), not a
qualifier-set join table mirroring UPGRADE's: no real campaign asks for two
serial codes, and several lottery rounds pointing at one CD sale falls out of an
FK for free. YAGNI, stated as such.

One validator, `resolve_round_requires`, is shared by all three write boundaries
(create, edit, import commit) -- same concert, kind in `ITEM_SALE_KINDS`, never
itself. It is ONE function for the reason `EVENTERNOTE_KINDS` is one table: three
copies of a rule drift, and here a drifted copy would let a venue-shaped mistake
render as a requirement. A POSTED bad target is a 422; a PRESERVED one (the whole
array omitted by an older client) that no longer resolves drops silently, because
422ing a value the submitter never sent is undebuggable from a browser -- the
same asymmetry `parse_round_legs` already draws.

**Same-submit references bind by `round_key`, not by array position**, which is
the one place the implementation deviates from its own spec and is recorded here
as a decision. The spec said "position in the submitted arrays" while itself
citing the `day_key` mechanism; keys survive a row being re-ordered or removed
between render and submit, and positions do not. The edit page renders each
saved row's REAL id as its key, which is load-bearing in a way one test pins
explicitly: let that regress and the client script mints a fresh key, the
server-rendered selection resets, and the next save drops an existing link with
no error anywhere.

The draft vocabulary gained an optional per-round `requires:` naming another
round in the same draft by its ja label -- both halves at once, per the
`tags_yaml` lesson, so `export.zip` stays a faithful backup and the add-concert
skill can author the link. It names a LABEL like `applies_to` names legs, never
an id, and the preview splits its two failure modes into two different warnings
(no such round vs a round of the wrong kind) because they are two different
mistakes for an editor to fix. The skill's example draft now carries the link
itself, and the test that pins the example to the parser asserts the link
actually resolves -- teaching material that emits a label the preview would warn
about is worse than teaching material that omits the feature.

Two small things worth keeping. The reverse line shipped as "Needed for:" rather
than the spec's "Feeds:", which reads as a fact about the item rather than as
jargon. And the requiring round's line carries the item sale's close time only
while that sale is still OPEN -- the actionable half ("you still need this, and
it stops being buyable on the 15th") -- resolved in `concert_round_rows` and
`due_reminders` rather than in the template, because round timing is not
presentation.

### The triage arc: prune in bulk, import in bulk, and the skill (2026-08-02)

443 open leads, and the loop did not close: a lead became a tracked concert only
when somebody found the ticket page, extracted the rounds, grouped the legs and
wrote the trilingual titles. The owner's flow was the other way round from how
this entry had imagined it -- **he does not prune by hand; the agent classifies
everything and he imports the decision.**

**Phase 1, prune by imported list.** A YAML file mapping `DismissReason` to
Eventernote event ids -- never internal lead ids, because the copy block an agent
reads only ever shows `/events/486243`. Three routes mirroring
`/admin/import/tags`: paste, plan, apply, with `/apply` RE-PARSING from the text
so nothing the browser sends can name a lead the file did not. Four buckets are
shown and none swallowed: will-dismiss, not-in-queue, already-dismissed, and
already-a-concert.

The parser RAISES where the concert-draft parser warns, and the asymmetry is the
reason: an applied entry is a permanent dismissal with no un-dismiss anywhere in
the app, while a rejected file costs one re-paste. So an unknown reason key, a
duplicate id across two reasons, a non-scalar id, a zero-dismissal file and a
REPEATED YAML KEY all refuse the file -- that last one because `safe_load`
silently resolves a repeated key to its last occurrence and drops the earlier
list, which here would silently lose leads.

**Phase 2, many drafts from one paste.** Several YAML documents separated by
`---`; no new format, `parse_draft` untouched. Both obvious splitting strategies
are wrong and were rejected with evidence: `text.split("---")` cuts a draft in
half when a `---` sits inside a block scalar, and `safe_load_all`/`compose_all`
ABORT their generator on the first bad document, silently losing every one
after it. Boundaries come from `yaml.scan()`, which tolerates a parser error;
a scanner-level failure falls back to a line split, so the worst case is one
oddly-split fragment rather than a lost batch.

`PendingDraft` is the ONE place this app keeps step state, and the reason it is
not a contradiction: it is a work BATCH, not flow state -- fifty to a hundred
concerts each needing a human-read preview, which is not one sitting, and a
hidden form field would lose it to a closed tab. Every concert still passes one
reviewed preview; this removed the copy-paste, never the review, and
`import_commit` stays the only write path into `concerts`.

**Phase 3, the skill.** `.claude/skills/triage-leads/`, three passes cheapest
first -- collapse by title stem, classify against the scope ruling, then research
only the survivors. It delegates drafting to `add-concert` rather than restating
its schema, and both its example files are pinned to the real parsers by test,
the same guarantee `add-concert`'s example has.

**The collapse finding is what made the arc tractable.** 443 leads is not 443
things: a nine-performance run is nine leads, so grouping by title stem gives
roughly 120-150 productions, and the scope ruling takes that to about fifty. Two
DIFFERENT mechanisms produce repeated titles and want opposite treatment, which
is the trap the skill names outright: 学園アイドルマスター LIVE TOUR is one concert
with eight legs, while 『Liella!と結ぶプロジェクト』お渡し会 is eleven events because
each member got her own slot at one venue on one day.

Four defects review caught that would have shipped, all found by mutating or
measuring rather than reading: a lead already bound to a concert would have been
dismissed and had a reason stamped on it; a double-commit silently minted a
SECOND concert (`alpha`, `alpha-2`), and the test that should have caught it was
written against a draft shape agents never produce, since they emit no
`event_id`; `PendingDraft.created_by` was the only `users.discord_id` FK in the
model file without an `ondelete=`, which would have broken self-serve erasure
(invariant 5); and the batch EXAMPLE claimed its dates were real when every round
time in it was invented -- teaching material that would have taught an agent to
present a fabricated deadline as sourced fact, which is the worst thing this arc
could produce.

One finding outlives the feature: **Starlette hard-caps every `Form(...)` field
at 1MB**, whatever an app-level constant says. It is in CLAUDE.md because it
applies to every form field in the codebase and bites as an opaque failure well
before any of them.

### Discovery dismissal records a reason (2026-08-01)

Branch `dismissal-reason`. `DiscoveredEvent.dismiss_reason` is a nullable
`DismissReason` column -- NULL means dismissed before the column existed and
is never backfilled, the same rule subscriptions and leg opt-outs already
follow. Its eight values are the taxonomy `docs/discovery-lead-taxonomy-
2026-08-01.md` named reading all 443 open leads end to end, LIVE and FANMEET
included on purpose: a real concert or fan meeting you choose not to track is
still a dismissal, and without a value for it every one of those would land
in `other` and wreck the agreement rate the column exists to measure.

`POST /admin/discoveries/{id}/dismiss` takes `reason` as the typed enum at
the route boundary (`Annotated[DismissReason, Form()]`), so a hand-posted
value outside the eight is a 422 before anything is written -- this column's
whole value is that every row in it is a real human judgment. `db/service.py`'s
`dismissed_reason_counts` excludes NULL rows rather than bucketing them as
`other`, for the same reason, and the page renders its "Dismissed so far"
paragraph only when that dict is non-empty -- an all-NULL history says
nothing, rather than a paragraph naming zero of every class.

The row's control is one `<select name="reason">` plus one "Dismiss lead"
submit, not a button per class: at the queue's documented 443-lead size,
eight buttons per row would have been roughly 3,500 buttons and as many tab
stops standing between the table and the copy block beneath it, and it still
works with JavaScript off. Labels render from `DISMISS_REASON_LABELS`
(`domain/types.py`), sentence case beside the enum, since raw values like
`live` and `free` read as internal shorthand to anyone who has not read the
taxonomy doc.

### A character bucket in the concert draft vocabulary (2026-08-01)

Bookkeeping caught up, not work done: this shipped INSIDE the character-tags
branch and was filed here anyway. It was raised at Task 10's review as a
deferred minor, a later task in the same branch built it, and nobody moved the
entry -- so it led the morning's list as an open gap while its own tests were
already green on main. The same debt the cache-bust entry ran up on 2026-07-22.

What exists, verified by running it rather than by reading for it:
`domain/yaml_export.py`'s `concert_to_yaml` takes `characters` as a REQUIRED
parameter (deliberately un-defaulted -- a kind added after the format shipped and
quietly defaulting to empty is exactly how the hole opened);
`domain/yaml_import.py` carries `characters` in `_SERIES_KEYS` and parses both
`series.characters` and `series_handles.characters`; `web/routes/imports.py`'s
preselection loop is a four-tuple with `character` in it, and `import_commit`
takes `character_tags`; and `.claude/skills/add-concert/SKILL.md` documents the
key, including that a Love Live-shaped bill has no characters and should leave it
out.

Three tests in `test_draft_import.py` pin exactly the failure the entry
described: a draft naming a character pre-selects her, a handle beats a name, and
an unmatched name becomes a create chip carrying `character` as its kind. The
entry's stated failure mode -- the picker's character row always arrives empty,
so an editor commits an im@s concert credited to nobody and the seiyuu's
followers are never told -- is the thing the first of those asserts cannot
happen.

### The im@s catalogue reformat (2026-08-01)

An operation, not a build: no code changed. The character-tags feature
shipped inert -- every rule in it was a rule about nothing, because the live
catalogue held no CHARACTER tags -- and this is what switched it on.

`scratchpad/build_gakumas_tags.py` authored the file through the app's own
`domain/tags_yaml.tags_to_yaml`, so the output was by construction what
`/admin/import/tags` expects and the omit-empty rule was applied by the code
that owns it. Thirteen new tags (`kind: character`, `parent: idolm-ster`, a
`voiced_by` handle each, trilingual names) plus one group row listing ONLY the
characters -- which is what made the thirteen seiyuu render as REMOVALS, the
importer's one destructive act, applied solely when a human ticks them.

**The surprise was the scope.** The entry assumed the whole franchise; the
catalogue held one im@s group. Surveyed before authoring: `idolm-ster`
(franchise) and `gakuen-idolm-ster` (group) and nothing else. So the reformat
is complete rather than phase one, and future im@s groups get character
members at creation instead of a migration.

Research provenance, since a wrong pairing would bind a character to a
stranger's event page: 13/13 seiyuu->character pairings confirmed against three
independent sources that agreed on every row (ja.wikipedia, game8, zh.wikipedia),
and 6/13 Eventernote actor ids verified by opening each actor's events page --
not by trusting a search hit. The other seven were confirmed ABSENT rather than
merely unfound: full-name search, surname substring search (Eventernote's search
is a substring match, so a stored `有村 麻央` would otherwise hide), and a direct
id-range probe of the block around the ids that did resolve. One near-miss is
worth remembering: 姫崎莉杏 is a real unrelated performer one character away from
姫崎莉波, and a fuzzy or first-result match would have bound them.

Three Chinese renderings were single-sourced and went to the owner rather than
being guessed: 藤田ことね (琴音 chosen over 言音, the reading-based form) and
葛城リーリヤ (莉莉娅 chosen over 莉莉亚). 篠澤広 took the fully-simplified 筱泽广.

What it does NOT do, by invariant 3: already-catalogued concerts keep the seiyuu
chips they were built with. Membership edits never reach existing concerts, so
the payoff is entirely prospective -- it changes what future attachments expand
to.

### Character tags, their seiyuu, and subunits (2026-08-01)

Branch `character-seiyuu-tags`, eleven tasks, migration `bb9780f0ad82`. Never a
Proposed entry: the owner noticed that an idolm@ster bill is credited to 如月千早
and never mentions the 今井麻美 who voices her -- Eventernote carries both as
separate actors, and a character-credited show never appears on the seiyuu's
page at all -- so a user following the performer missed the show completely,
which is this app's worst failure mode.

`TagKind.CHARACTER` is a fifth kind and `Tag.voiced_by_tag_id` a nullable
self-FK to the ARTIST who plays her. **Attaching a character attaches her
seiyuu**, and because `tracked_concert_ids` matches MATERIALIZED `concert_tags`
rows, that single act makes following the performer work with zero change to
subscription code -- the payoff for reusing invariant 3's proven seam instead of
inventing a second matching rule. Expansion chains one FIXED extra step (group
-> character -> seiyuu) and terminates by construction, because a seiyuu is an
ARTIST. `parent_id` widened to allow GROUP -> GROUP (a subunit) and CHARACTER ->
FRANCHISE, with a cycle guard, since loops became possible for the first time.

Four decisions were the owner's, three of them from mockups:

- **The split pill** over an inline `如月千早（今井麻美）` gloss, chosen because the
  merge is CONDITIONAL: when only one end is attached the chip is plain, and the
  split shape makes that difference read as meaningful rather than as
  inconsistent styling.
- **The subunit keeps the repetition** (indented rail, members still listed
  under the parent). The alternative -- the subunit absorbing its members -- was
  rejected because it makes the parent cluster stop being a truthful lineup, and
  what it displayed would depend on which OTHER tags happened to be attached.
- **Pruning a character prunes her seiyuu**, refined by the build to "unless
  another still-attached character shares her" (a performer can voice two
  characters on one bill).
- **Recast history is out of scope**: one column, re-pointed.

The ruling that mattered most came MID-BUILD, from a review finding rather than
from the design: **a seiyuu attached via a character is DERIVED, never chosen.**
The prune rule above had shipped and was unreachable from the concert editor --
a derived seiyuu was an ARTIST, so she was pre-ticked, always submitted, always
in the desired set, and therefore never detached whatever happened to her
character. The owner's answer ("only the character tag is added; the artist is
auto-correlated and displayed as `cv. xxx`") fixed it by removing her from the
picker entirely, which needed the desired set to expand characters to seiyuu on
the detach side to avoid the opposite bug. Two halves that only work together,
and the entry worth remembering is that the FIRST correct-looking fix was
disproved by mutation: moving the detach loop after the attach loop cannot work,
because the seiyuu sits in both diffs' inputs and appears in neither.

Three more findings cost real effort and are recorded in CLAUDE.md rather than
only here. The importer refuses a `voiced_by` whose target is not an ARTIST,
which refuses SELF-voicing for free -- and self-voicing fails SILENTLY, since a
character pointed at herself lands in her own `paired_seiyuu` set and vanishes
from the Performing panel. A GROUP under a GROUP was invisible in the Tags chips
directory and took its members with it, so the directory now walks `parent_id`
in Python with a leftover pass guaranteeing every group renders exactly once
(a template recursion would HANG on a cycle). And the split pill was MEASURED in
a real viewport per the standing rule: it came out 22.88px against a plain
chip's 28.72px, and the fix was to derive the pill's box from the chip's own
padding, line-height and border rather than to tune it -- a chip SETS
`line-height: 1.5` and does not inherit it, which is what made the obvious fix
still 2.5px short.

What it deliberately did NOT build, so these read as decisions rather than
oversights: the catalogue reformat itself (now #1 -- the feature is inert until
it runs), a character bucket in the concert draft vocabulary (now #4),
provenance on `concert_tags` (a seiyuu who was also there in her own right is
removed when the character is pruned, and the editor re-adds her -- group
expansion has had that blind spot since it shipped), nested MEMBERSHIP (a
subunit contains characters directly; `TagMember`'s rule stands), automatic
subunit attachment, and any path to change an existing tag's KIND (immutable by
design, and the reformat does not need it).

### Per-tag sweep button (2026-07-31)

Joins PR #117, branch `discovery-manual-sweep`. No migration. Checking whether
one artist has a new performance cost 86 third-party fetches and a wait for the
next tick; now it is one fetch, in the request.

**It runs INLINE, and that is the one interesting decision.** The full sweep is
queued because 240 seconds is not a thing an HTTP request may hold; ONE page is
1-10 seconds and already bounded at `FETCH_DEADLINE_SECONDS` (30), which is an
ordinary request. Queueing it would mean waiting up to a minute for an answer
the operator is sitting there watching for.

`sweep_one_tag` reuses every piece of `run_sweep`'s diff, so dedup, the exact
event-id match and the date-and-venue hint behave identically. Three things it
deliberately does NOT do, each of which would be a silent regression in the
DAILY sweep and visible nowhere near this button: no `stamp_discovery_run` (that
is the 24h clock -- checking one artist would displace that day's sweep of all
86), no `set_sweep_cursor` (the cursor is progress through the full list; one
artist read out of order is not, and moving it would skip artists), and no
`Notification` (the operator is looking at the page they land on). All three are
pinned by tests carrying a positive control, so none can pass against a function
that swept nothing.

The route lives in `routes/discoveries.py`, not `routes/tags.py`, even though
the button is on the Tags page: a router registers whole, and every discovery
route being admin-only in one module is the property worth keeping. The button
narrows further than the dialog it sits in -- admin AND artist-or-group AND a
URL to read -- so a visible button never 403s.

A fetch failure is the COMMON case (Eventernote timed out on 12 of 86 in a live
run), so it redirects rather than 500s, and "swept, found nothing" and "could
not reach the page" get different words. This codebase has no flash mechanism,
so the result rides in `?swept=`, a closed three-code vocabulary with the
wording in the template -- unlike `/admin/rehearsal`'s `?note=`, which puts its
sentence straight in the URL. Nothing operator-typed, editor-typed or fetched
ever reaches that URL, which ends up in logs and history.

No re-rank pass: a per-tag convenience button changes no other entry's impact or
effort, and the head of the list is untouched.

### Manual sweep button, and `DISCOVERY_ENABLED` documented (2026-07-31)

Shipped as PR #117, branch `discovery-manual-sweep`, migration `34179560cec0`
(`DiscoveryState.sweep_requested_at`). Not a Proposed entry -- an owner request
the same day discovery merged, made while planning the scrape-to-agent workflow
that needs a sweep on demand.

`DISCOVERY_ENABLED` existed only in `config.py`, so nothing in the repo hinted
the knob was there; `.env.example` now carries it. A later correction to that
same comment is worth keeping visible: "the first sweep runs within a minute of
enabling" is true only when it has NEVER run, since `discovery_due` keys on a
NULL `last_run_at` -- disable and re-enable after a run and the next sweep waits
out the remaining 24h, which is exactly the shape of an "I turned it on and
nothing happened" evening.

The button does NOT run the sweep. A sweep can occupy 240 seconds, which no HTTP
request should hold open, and an inline run would be a second execution path for
something already bounded by a budget, a cursor and a heartbeat. It sets
`sweep_requested_at` and the existing 60s tick picks it up. **A pending request
sweeps even with the flag off** -- the flag gates the AUTOMATIC behaviour, and
scraping on demand should not require committing to a daily job.

The load-bearing detail is that the request is cleared whether the sweep
succeeded or failed, and the clear lives inside `stamp_discovery_run` rather
than at either call site -- the single point the sweep's own `finally` and the
scheduler's post-rollback re-stamp both converge on. A request surviving a
failure would re-run the sweep every 60 seconds: 86 third-party fetches a
minute, the exact trap this subsystem was bitten by once already.

Two accepted consequences: a manual sweep DISPLACES that day's automatic one
(it sets `last_run_at`, so a habit of pressing mid-morning drifts the daily
slot), and a second press during a running sweep is absorbed by the finishing
sweep's stamp -- the page then shows nothing pending, so the recovery is another
press rather than machinery.

### `/admin/discoveries` row height, measured (2026-07-31)

Was Proposed #10, logged PRE-DEPLOY during the discovery build and closed the
same day by doing what the entry asked: putting the seeded page in a real
browser instead of reasoning from the CSS. Measured at an `innerWidth` of
2560px -- a `resize_window(1440)` call was issued and did NOT take, which was
only noticed later, so read the numbers below as desktop-at-2560 and not as the
1440 they were first reported at. The finding survives the correction with room
to spare: the table is width-capped at 1104px either way, so a NARROWER viewport
makes the wrapping worse, never better.

**The measurement moved the answer.** Hint-marked rows are 81px against a plain
row's 60px -- real, consistent, and NOT the cause of the unevenness. The actual
cause was elsewhere and affected every row: `.tagtable` uses automatic layout, a
column of long Japanese titles left the date column 83px, and at
`white-space: normal` an ISO date rendered as `2027-03-` / `22`. Fixed with a
`.nw` class on the Date and Announced columns; the date now claims 91px from the
title column, which wraps happily. Re-measured after: every date cell is one
text line, was two.

Row heights are unchanged and that is correct -- a four-line title is what sets
them, and titles should wrap. The 81-vs-60 delta was accepted as-is rather than
softened, because `.banner` is the sanctioned needs-attention shape and
inventing a third callout shape for this was declined. The Artist column still
wraps `大西亜玖璃` across two lines at 84px; left alone as a width judgment, not
a bug.

Also worth recording, because it is the same lesson this codebase keeps
relearning: the FIRST verification of the fix was itself a proxy. Comparing a
cell's height to its line height proves nothing, because a `<td>` stretches to
its ROW height -- it was reporting the title column's wrapping and reported
"still wrapping" after the fix had already worked. The honest check measures the
text's own client rects via a `Range`.

**Three traps in browser measurement, all hit for real on 2026-07-31 and all
invisible unless instrumented for.** Worth reading before the next layout
question, because each one produces a confident, wrong number:
1. **`resize_window` can silently not take.** A side panel ate the viewport and
   `innerWidth` read 546 while `outerWidth` read 2560 -- which matches the PHONE
   media query, so a measurement there would have described the bottom-sheet
   layout and called it desktop. Assert `innerWidth` and the matched media
   query on every read; do not trust the resize.
2. **The browser caches the stylesheet.** A first post-fix read returned the
   pre-fix numbers. Dump the matched CSS rule alongside the rects, so a stale
   sheet is visible rather than reported as "the fix did not work".
3. **A `<td>` reports its ROW's height**, per the paragraph above.

### Eventernote actor-page discovery (2026-07-31)

Shipped as: spec `docs/superpowers/specs/2026-07-31-eventernote-discovery-design.md`
+ impl plan `docs/superpowers/plans/2026-07-31-eventernote-discovery.md`, nine
tasks on branch `eventernote-discovery`, migrations `48cd59cae5d7`
(`discovered_events` + `ConcertDay.eventernote_event_id`) and `052f924bbcb0`
(`discovery_state`). **Proposed #1**, promoted there on merit two days earlier by
the catalogue round-trip.

A concert nobody has added has NO deadline tracking at all -- the worst failure
this app has, because the user never learns there was a deadline to miss. A
daily sweep now walks every tag carrying an `eventernote_url`, parses that
artist's events page, and records what the catalogue does not have as a lead.

**It is a lead generator, not an importer, and the spec talked itself out of the
entry's own premise to get there.** The entry assumed discovery would emit a
paste-ready YAML draft through `POST /concerts/import/draft`. It cannot:
Eventernote carries no ticket information, so a scrape can never produce ROUNDS
-- the lottery windows and deadlines that are the entire point of this app. So
the app does what is mechanical (fetch, parse, diff, report) and an agent
following `.claude/skills/add-concert` does what needs judgment (grouping loose
legs into one concert, finding the official ticket page, extracting rounds). A
second consequence is robustness: parsing a LIST of (title, date, venue, id) is
far less fragile than parsing a whole event page, because an index's markup
changes less often than a detail page's.

**One fetch per artist, not eighteen.** Measured against the live site rather
than assumed: rows are 20 to a page and strictly newest-first, so future events
are always a PREFIX. The stop rule is a take-while, which makes a sweep ~86
fetches instead of the ~1,548 that reading every page of every artist would
cost. Also measured: the actor URL's name segment is decorative (`/actors/x/5847`
resolves the same), so only the id is identity.

**The date-and-venue collision is a HINT, never a suppression.** The obvious
heuristic -- same date, same venue, therefore already held -- is wrong in a case
this app models explicitly: 昼公演 and 夜公演 are two Eventernote events on one
date at one venue and two legs of one concert, so auto-suppressing would hide
precisely the second show. `ConcertDay.eventernote_event_id` is the exact half
of that question instead, populated by the import path going forward, which
turns "do I already have this?" from a guess about Japanese titles into an id
lookup over time.

Four things worth keeping on the record.

**The host-pinned fetch was EXTRACTED, not copied.** The ramen.events importer's
three-way SSRF guard became `app/fetching.py` on an owner ruling made before a
line was written, because two copies of a security control means a weakness
found later is fixed in one and missed in the other. The site's own next-page
link points at an `eventernote.s3.amazonaws.com` host, so the redirect re-check
is not hypothetical paranoia here. The extraction's obvious bug -- a module-level
hook pinned to one host -- was avoided by building the hook per call, and the
control was re-verified by measurement (13 hostile URLs and 10 hostile Location
headers, no bypass).

**Two ways the same failure nearly shipped: a sweep that does not stamp its clock
re-runs 86 third-party fetches every 60 seconds, forever.** The plan's own step 7
returned early on a quiet day and so never stamped; and only `DiscoveryFetchError`
was caught, while `parse_actor_events` builds `date(y, m, d)` from a regex, so one
`2026年2月30` anywhere would have escaped and re-armed the same loop. Both were
caught in review. The fix needed a third part nobody instructed: `stamp_discovery_run`
only FLUSHES, so the stamp written in a `finally` is discarded when the scheduler
handler rolls the poisoned session back -- and when the raise is itself a DB error
the `finally` cannot even flush. `scheduler/loop.py` re-stamps on the cleaned
transaction, and that half is the only one that works in the worst case.

**A long in-tick job must beat the heartbeat itself.** `heartbeat.beat()` fires
BEFORE `tick()` and `/healthz` goes false at 180s, so a nominal ~130s sweep had
no margin and a slow one would have paged the owner about a perfectly healthy
app. The sweep beats per artist. Moving discovery off the tick entirely is the
right end state and is logged rather than done.

**Announcing marks every lead the DM covers, listed or merely counted**, and
`open_leads` deliberately does not filter on `announced_at`. The two together are
one decision: on the first sweep every future event of all 86 tags is new at
once, mostly duplicating concerts already held, so marking only the named ten
would trickle a real backlog out at ten a day for weeks. The count-plus-link says
the true thing once and sends the maintainer to the surface built for bulk
triage -- which only works if that surface still shows announced rows. Getting
that wrong (the spec did, and review caught it) would have left a first sweep's
"+N more" reachable from nowhere.

One decision came from the DM's shape rather than the design: the message is the
same content twice, a readable markdown list and then a fenced copy block,
because Discord does not linkify inside a fence. The budget work behind it is
larger than it looks -- every scraped field is unbounded free text, and past
Discord's real 2000-char cap discord.py raises and the WHOLE DM is lost rather
than trimmed, so the block yields first, says so when it drops lines, and a hard
prose floor sits under all of it (verified by a 4,000-case fuzz, not by reading).

### Tag import: fills, and conflicts you resolve (2026-07-31)

Shipped as: spec `docs/superpowers/specs/2026-07-31-tag-import-conflicts-design.md`
+ impl plan `docs/superpowers/plans/2026-07-31-tag-import-conflicts.md`, three
code tasks on branch `tag-import-conflicts`. Not a Proposed entry -- it came out
of a question the owner asked hours after the round-trip shipped. No migration.

The importer that shipped that morning was a RESTORE tool: match on handle,
skip the tag whole if it exists. Right first answer, and the limit surfaced the
same day. All 79 artist tags in the live catalogue have an empty
`eventernote_url`, the Eventernote-discovery entry needs them populated, and
there was no way to carry them across -- every one of those tags already exists
by handle, so an import skipped all 79 and the field never moved.

The first proposal was a fill-blanks-only mode. The owner asked for something
better: **show the disagreements and let a person choose.** That subsumes
fill-blanks -- a blank on one side is not a disagreement, so it simply happens
-- and it makes the importer a real sync tool without the stale-file danger,
because nothing is overwritten that was not looked at.

Four cases per field; only "both differ, both non-blank" asks anything. Every
default changes nothing: an unanswered conflict keeps the catalogue's value, and
a member removal -- the single destructive act in the feature -- happens only
when explicitly ticked. `kind` is compared but never choosable, because a venue
arriving as an artist could orphan a leg's `venue_tag_id`; it warns and refuses
the tag whole.

Two properties worth the design's weight. `/apply` re-parses and re-plans from
the pasted file, so the browser sends only a decision and never a value -- a
forged post cannot inject, and a conflict that vanished since the preview is
simply not applied. And nothing is ever deleted: a catalogue tag the file omits
is untouched and unmentioned.

Three findings worth keeping. The plan's task boundary was WRONG -- deleting
`import_tags` broke the route that still called it, 43 collection errors from
one import, so two tasks were really one change. The report needed an
`unchanged` list that the plan did not anticipate, because `skipped` had
silently changed meaning from "left alone" to "refused" and a no-op import would
otherwise have reported nothing at all. And one new test asserted `"alert(1)"`
was absent from a response, which matched `base.html`'s own comment explaining
invariant 7 rather than any injected value -- the second test that day to pass
or fail for a reason unrelated to its claim.

### The catalogue round-trip: an admin export and a tags import (2026-07-31)

Shipped as: spec `docs/superpowers/specs/2026-07-30-catalogue-round-trip-design.md`
+ impl plan `docs/superpowers/plans/2026-07-30-catalogue-round-trip.md`, seven
tasks on branch `catalogue-round-trip`. **Proposed #1**, and the only entry the
owner ever personally asked for. No migration.

`GET /admin/export.zip` writes `tags.yaml`, one `concerts/<event_id>.yaml` per
concert, and a `RESTORE.txt` that states the order -- tags first, because a
concert draft refers to its tags by handle and a handle that does not exist yet
cannot bind. `POST /admin/import/tags` reads the first back.

**No personal data by CONSTRUCTION, not by filter.** The queries reach concerts,
days, rounds, qualifiers, tags and tag_members and nothing else; no JOIN to a
user table exists to get wrong, and `created_by` is never emitted. Nothing to
leak beats a filter to maintain.

Three things the arc turned on.

**The format has two masters, and one file serves both.** An agent authoring a
draft knows NAMES; a restore needs IDENTITY. So the concert draft gained
`series_handles` and per-leg `venue_handle` beside the existing names, plus
`event_id` -- all optional, so every agent-authored draft and the skill's pinned
example kept working untouched. Where a handle block names a kind it is
authoritative and the name list is ignored outright, with no per-entry fallback:
falling back would reintroduce `match_tag_ids_by_name`'s first-tag-wins guess,
which is the exact failure handles exist to remove. Its test is two performers
both written 佐藤有紀, and the right one binding.

**`event_id` round-trips**, so a restore lands on the original URLs rather than
minting new ones and breaking every link anybody holds -- and re-importing a file
whose concert still exists answers 409 instead of quietly creating a second.
`validate_event_id` does the checking, the same function the edit page calls, so
format, reserved words and uniqueness could not drift apart.

**The import skips, never updates.** An existing handle is left entirely alone,
including its membership, which makes the import idempotent and means a stale
file can never revert an edit made since the export. It wires parents and
members only for tags it created, writes `TagMember` directly rather than
through `attach_tag` (which would drag invariant 3's expansion into something
that must touch no concert), and queues no notification.

Two test traps were caught in the SPEC, before either was written, and both are
the kind that pass while proving nothing. "Assert the zip's bytes contain no
`created_by`" would pass vacuously -- entries are DEFLATE-compressed, so the
string is not there to find even when the data is; the test extracts every entry
instead. And "two exports are byte-identical" is false with
`ZipFile.writestr`, which stamps the current time; zip timestamps have
two-second resolution, so the first probe slept 1.1s, landed in the same bucket
and "proved" determinism that did not exist. Entries now go through an explicit
`ZipInfo` pinned to the 1980 epoch and the test crosses a bucket boundary.

The shared tag writer that the tag-handles spec deliberately deferred landed
here, because the importer is its second real caller -- and unlike the three
editor routes it supplies an explicit handle from a file rather than generating
one, which is the distinction the extraction had to express.

### Tag handles: a stable identity that is not the name (2026-07-30)

Shipped as: spec `docs/superpowers/specs/2026-07-29-tag-handles-design.md` +
impl plan `docs/superpowers/plans/2026-07-29-tag-handles.md`, seven tasks on
branch `tag-handles`, migration `eb4cb4f7927a`. **Not a Proposed entry** -- it
came out of designing #1, the admin catalogue export, and it is the prerequisite
that entry did not know it had. Three dialog fixes found alongside it shipped
separately as PR #112.

The owner asked for the export and named the hole in the same breath: *"the
issue is I also want a way to import tags."* Correct -- a YAML draft can
REFERENCE tags but never DEFINE them, so an export was a pile of concerts
pointing at a taxonomy you would have to hand-rebuild first. Designing that
importer ran into a question it could not answer: **"do I already have this
tag?"**

**Two live crashes fell out of asking it**, both measured against a real DB
rather than reasoned about. `Tag.name` was globally unique in the schema while
the routes checked name+kind, and `find_tag_by_name_and_kind`'s own docstring
recorded an owner ruling -- same name across kinds is allowed -- that was never
implemented. So creating an `Aqours` VENUE beside the `Aqours` GROUP passed the
routes' check and died on the column's UNIQUE: an unhandled IntegrityError, a
500, the editor's input gone. The same thing in different case SUCCEEDED (the
constraint was case-sensitive, the check was not) and from then on every
name lookup raised `MultipleResultsFound` -- a working page started 500ing with
nothing saying why.

**The owner then supplied the requirement that killed the obvious fix**:
uniqueness scoped to a kind is still wrong, because two performers may share a
name. Which means a tag's name is not its identity and no amount of scoping
makes it one -- every name-match in the app was a guess. Concerts had solved
this years earlier with `event_id`; tags had no equivalent, and that absence
was the actual gap.

So tags got a `slug`: auto-generated from `name_en`/`name`, editable, unique,
ASCII, and deliberately absent from every URL. Name uniqueness was dropped
outright rather than narrowed, both crashes died with it, and the single-result
name lookups were DELETED rather than fixed -- `scalar_one_or_none` raises the
moment a duplicate exists, so keeping either would have left a function that is
safe only while the data cooperates.

Four things worth keeping on the record.

**The `#new-tag-dupe` warning had been telling the truth while the server
refused.** The Tags page has warned "creating another one will keep them
separate because tags cannot be merged yet" for a while, and `create_tag` 409'd
exactly that. Removing the block was a correctness fix, not merely an enabler --
and it meant the UI work budgeted for this arc mostly already existed.

**The three create surfaces deliberately diverge**, which is documented in
CLAUDE.md so it does not read as drift: `POST /tags` allows a duplicate name,
while the two quick-create routes still answer 409 with the existing tag's id.
Mid-import, an existing tag of the name you just typed is almost certainly the
one you meant. `tests/test_error_pages.py` also uses that 409 as its only
vehicle for the HTML-vs-JSON regression guard, which removing it would have
quietly emptied.

**The migration nearly shipped half-applied.** Its structural phase briefly sat
inside the reporting helper, after that helper's early return, so it ran only
when there was something to report -- a database whose handles all came out well
would have been left with `slug` nullable, the unique still on `name`, and the
revision stamped as applied. All nineteen migration tests passed for the wrong
reason because every fixture happened to contain a reportable row; an unrelated
downgrade test in another file is what caught it. The full account is in the
spec, along with the two guards now encoding the lesson.

**The backfill reports what it guessed at**, at the owner's request: handles
that fell back to the kind, handles built from one stray Latin letter
(`Kアリーナ横浜` -> `k`), and every tag with no English name -- the last being
the one the owner expects to come back empty, which is exactly why it is worth
printing. Names escape rather than raise if the console cannot encode them,
because a diagnostic must never be the thing that aborts a deploy.

One decision left open on purpose and recorded in the spec: whether a
one-ASCII-character handle should fall back to the kind. Left as-is because any
threshold is arbitrary, the alternative is an anonymous handle rather than a
useless one, and handles are editable.

### Correctness sweep: a permanent wrong DM and an unreachable URL (2026-07-29)

Shipped as: spec `docs/superpowers/specs/2026-07-29-correctness-sweep-design.md`,
two fixes on branch `correctness-sweep`. Proposed #5 and #7, both filed by
reviews of the 2026-07-28 cleanup batch and deferred out of it -- #5 on risk,
#7 on rarity. No migration; no model, template or catalogue was touched.

**A. Create and import no longer announce a born-dead concert.** The cleanup
batch shipped the rule that a tag attached to a dead concert -- every leg
cancelled -- notifies nobody and applies no preset. It held on `edit_concert`
and on both venue rollups, and not on create or import, for a reason that was
pure ordering: `create_concert_row` called `handle_newly_tagged` straight after
flushing the `Concert`, before a single leg existed. `all_legs_cancelled` reads
that as a dateless draft and exempts it -- correctly, and it must, or every
create would silence itself -- so it notified. A concert created or imported
with its only leg submitted cancelled therefore DM'd every franchise, group
and artist follower a 🆕 "Apply here" for a show that is off, while every
VENUE follower on the *same request* was correctly skipped by the rollup a
hundred lines later. The fix: `create_concert_row` returns its `newly` list
instead of consuming it, and both callers run the pipeline after their legs
flush, in the exact position and order `edit_concert` already used.

Two decisions inside it are worth keeping. **Two calls, not one merged
call**: merging the concert-level tags with the venue rollup's would have
improved one notice slightly and saved a query, and was rejected to keep the
three write paths structurally identical -- the parity the editor coherence
pass and the cleanup batch were both largely paid for. And
**`duplicate_concert` was deliberately left alone**: it does not go through
`create_concert_row`, it creates no legs at all, so its clone is a genuine
dateless draft and the exemption is right there. It is the one create path
where announcing a legless concert is correct, and it now has a test saying
so, because the obvious "tidy" follow-up is to make it match the others.

**B. `generate_event_id` treats a reserved id as taken.** Invariant 6 reserves
`"new"` and `"import"`; `validate_event_id` enforced it on what an editor
types, and the app's other producer of ids never consulted the set, with
neither caller validating what came back. A concert titled exactly "Import"
took that id -- and since both owning routes are registered ahead of
`/concerts/{event_id}`, deliberately, its own page was unreachable for good
while every list kept linking to it, and its edit page pre-filled the
offending id so saving anything at all 422'd until the editor worked out that
the field they never filled in was the problem. One condition in the
uniqueness loop; the suffix pass now mints `import-2`. No `.lower()`, unlike
`validate_event_id`, because `slugify` lowercases -- pinned by a test so a
change to `slugify` surfaces here rather than in production.

One process note. A single run of the two touched test files failed once, in
`test_user_with_existing_rules_is_skipped` -- a test neither fix touches --
and did not reproduce in four subsequent runs of the same command, including
the identical argument order, nor in the full suite (1609 passed). It is
recorded here rather than quietly dropped: the failure mode was the fan
holding ZERO rules, which points at that test's own `login_as`/subscription
setup and not at the notify-and-apply pipeline, and the test's create passes
no tags at all, so `newly` is empty and the reordered call returns before
doing anything. Believed pre-existing and order-sensitive; if it resurfaces,
this is the note that says it was seen on 2026-07-29 and not introduced here.

### Real 403/404/422/500 pages, and an admin index in Preferences (2026-07-28)

Shipped as: commit `d84f63e` on branch `error-pages` (PR #109). **Not a
Proposed entry, and logged here late** -- see the 2026-07-29 revision pass
above: this build had no Shipped entry, no spec and no plan, the only build of
that week to skip all three, and it is dated to its commit rather than to the
day the debt was noticed. Recorded from the commit message and the diff.

Two things, both about being findable. **Admin tools in Preferences**: three
admin pages shipped that week -- deliveries, broadcast, rehearsal -- and none
was linked from anywhere, so reaching any of them meant already knowing its
URL. A section beside the existing Editors block now lists them, admin-only,
with the rehearsal link gated on the same `rehearsal_enabled` flag that
REGISTERS the route, because offering a link that 404s is worse than offering
none. **Error pages**: 403/404/422/500 rendered plain JSON, or for a 500
Starlette's unstyled plain text, with no way back.

The design turns on one distinction and it is deliberately NOT the status
code: a browser navigation gets HTML, an XHR keeps the JSON body it was
already parsing. `_tag_create_dialog.html` and `_venue_create_dialog.html`
both read `(await resp.json()).detail` off a 409 to offer "that already
exists, select it instead", so a blanket HTML handler would have degraded both
to a generic failure with nothing saying why -- it has its own regression
test. Copy is per-code, because each code has a different amount to usefully
say: 403 NAMES the account you are signed in as, since the commonest cause is
being on the wrong Discord account and nothing else would tell you, and 422
lists the real messages and offers "Go back and fix it" via `history.back()`.

Seven files: one shared `error.html`, the handlers in `web/app.py`, one line
in `routes/preferences.py`, the Preferences section, and `test_error_pages.py`
(242 lines) -- plus 19 new msgids filled in both catalogues with no fuzzies,
which is the part a late-logged entry is likeliest to have left undone.

### Local rehearsal harness with a second Discord bot (2026-07-28)

Shipped as: spec `docs/superpowers/specs/2026-07-28-rehearsal-harness-design.md`
+ impl plan `docs/superpowers/plans/2026-07-28-rehearsal-harness.md`, six tasks
on branch `rehearsal-harness`, plus the operator guide `docs/local-dev-bot.md`.
Proposed #2. Sub-project **A** of three and the last to ship, closing the arc
that began as "let me test the whole flow on prod": B made a bad delivery
visible, C made it answerable, and A is the piece that means neither of them
has to be rehearsed on live users. No migration -- it adds no column and no
table, which is the whole point of what happened to the design.

**The retargeting is the most valuable thing in this entry.** The first draft
of the spec took "on prod" literally and designed the apparatus that premise
requires: a `Concert.rehearsal` column, an `include_rehearsal` parameter, three
global query filters and a rehearsal-only tag convention, every one of them
existing only to make a fake concert harmless inside a shared production
catalogue. The rewrite deleted all of it, because the premise was wrong. There
is no rule that production is the only environment -- only a gap in the three
tiers that already existed (the suite proves logic, web-only dev mode proves
the real app in a real browser, and nothing at all proved embeds, buttons, the
60s tick and delivery), and that gap existed solely because there was one
Discord bot. A second Discord application closes it for free, and one boolean
replaces the column, the filters and the convention. A feature spec that ships
a schema change is ordinary; one that talks itself out of a schema change by
questioning where it runs is worth keeping on the record.

**Gated, not guarded.** `rehearsal_enabled: bool = False` decides whether
`web/app.py` registers the router at all, so on production `/admin/rehearsal`
is absent from the route table rather than protected -- a "pull every reminder
forward" button behind only a permission check is one misconfiguration away
from firing real reminders early. That flag IS the safety model, so it is
asserted directly, against the built app's routes. `require_admin` stays on
every route as a second layer for a misconfigured deploy. The harness got its
own router module because a router registers whole and `admin.py` now serves
`/admin/deliveries` and `/admin/broadcast`, which production needs.

**Two rules survived the rewrite, and one of them replaced the dropped
column.** The rehearsal concert is identified by a constant `event_id`, so the
pull-forward action cannot reach another concert's rows *by construction*:
there is no id for a caller to pass, and the rows are resolved by joining
through the concert. The safety test for that was proved to bite by mutation
(widen the query to the whole table, watch it fail, restore) rather than
trusted. And teardown deletes the `Concert` row only, letting existing cascades
take days, rounds, queue rows and outcomes, never touching users, presets or
subscriptions -- the operator's real local state.

**Pull-forward fakes the WAIT, not the work.** The seed writes realistic
anchors and real rules and lets `sync_concert` and the pure planner compute the
fire times; the action then rewrites the soonest unsent row's `fire_at_utc`
into the past and the real 60s tick delivers it. Suppression, the eligibility
gate, the send path and the buttons all run exactly as in production. The spec
had already rejected the two alternatives: an injectable clock (the tick calls
with the real clock, so the component most worth proving would be the one not
honouring the fake) and compressed anchors with real waiting (which cannot
exercise a "3 days before" offset without the anchor genuinely being three days
out).

**The page is an oracle, not a trigger.** For the row about to fire it names
the buttons a correct DM should carry, restated in `domain/rehearsal.py` rather
than derived from `bot/messages.py` -- an oracle that read the code under test
would agree with it however wrong that code became. That is the difference
between watching DMs arrive and testing them: "no button rendered" and "wrong
button rendered" stop looking alike.

**The prescribed walk was wrong in three places, and every correction was made
at the source.** (a) Step 4's buttons were "won / lost", which is what a
*single*-leg round renders; R1 deliberately covers both legs, so the DM is the
per-leg split -- `wonall`, one `wonday` per covered leg, `lostall`. (b) Four
rows omitted the trailing `snooze`/`remindlater` that every reminder DM
carries. Both were re-derived empirically against real `custom_id`s. (c) Step 8
"cancel Day 2" queues *nothing*: `notify_newly_cancelled_legs` is
concert-scoped by design and stays silent while the reader still holds a live
reminder anywhere on the concert, so the action cancels every live leg at once
and was renamed `cancel_rehearsal_show` -- a function called
`cancel_rehearsal_leg` that cancels every leg is the same untrue label this
project has had to correct twice in user-facing copy. It also makes step 8
terminal, which the page now says. A fourth correction came out of writing the
guide: **step 1 sends no DM at all.** `handle_newly_tagged` is the only
producer of `new_event`, it fans out to the followers of a newly attached tag,
and the seed attaches no tags -- it tracks the concert with an explicit
subscription. The page and the guide now say so, and the `new_event` embed is
reached through the catalogue instead. A walk whose job is to tell you what a
correct DM looks like is worse than useless when it teaches you to expect the
wrong thing and "fix" working code.

**The shape catalogue answered the spec's open question yes.** It renders any
of the eight shapes through the real builders in any of the three languages and
sends it now, needing none of the walk's state -- eight embeds in three
languages in about a minute, which makes it the fastest ja/zh copy review the
project has. It is the **second sanctioned exception to invariant 4**, next to
`POST /me/test-dm` and for the same reason (a manual, user-initiated,
one-at-a-time diagnostic is not a system-initiated notice), with one claim
`/me/test-dm` cannot make: the route does not exist in production at all. Two
traps showed up inside it. `NoticeContext` and `LegCancelledContext` resolve
their UGC fields from the RECIPIENT's `users.language`, not `get_locale()`, so
`set_locale` alone would have rendered the prose in the picked language and the
concert title in the operator's, silently -- the catalogue borrows the
operator's row for the length of the build and rolls back, which is free
because it writes nothing. And the ops alert ignores the locale picker on
purpose: `evaluate_and_alert` composes it as a bare f-string with no gettext
anywhere, so it is in the catalogue for its LAYOUT (plain text, no embed, no
buttons), the one thing the other seven cannot show.

**`/privacy` needed nothing, and that was checked rather than assumed** -- the
broadcast's entry above is a reminder that "no new user data" is not the same
question as "no new disclosure". Here both answers hold: no new category of
data is stored, and the routes do not exist in production, so there is nothing
about the live service to disclose.

Revision pass: recorded in the narrative above -- entries renumbered 1-12 by
two removals, the second being the six-day-old bookkeeping debt below; nothing
re-ranked on merit; the admin catalogue export gained a live pointer from
`docs/local-dev-bot.md` without moving.

### Pin the Python version across dev, CI and the server (2026-07-22)

Shipped as: commit `63f0f78`, one file -- `.python-version` at the repo root,
pinned to 3.14. `uv sync` honours it in dev, in CI (via `astral-sh/setup-uv`)
and on the Lightsail server, so a version-dependent bug now fails in all three
places or in none. That closes exactly the failure the entry was filed for: CI
once went red with an `UnboundLocalError` no local run could reproduce, a
3.12-vs-3.13 comprehension-scope difference, because nothing pinned the
interpreter and `requires-python = ">=3.11"` made every one of them fair game.
The deploy consequence the entry flagged was carried in the commit message
rather than deferred: it changes the PRODUCTION interpreter, so uv downloads
3.14 to Lightsail and rebuilds the venv on the next `uv sync`.

**Moved to Shipped on 2026-07-28, six days late**, by the rehearsal harness's
revision pass -- it had sat in Proposed the whole time describing a gap that
was already closed, and a Shipped entry two days younger than the commit even
annotated it with a `.venv` drift reproduction, which should have been the
tell. Logged here dated to the commit, not to the move. The cache-bust entry
below set the precedent for a late move by being one day late; this is the
larger version of the same lapse, and the lesson is the cheap one: a build
commit that closes a Proposed entry is still a shipped entry, even when no
spec, plan or PR announced it.

### Targeted admin broadcast: a DM you can take back (2026-07-28)

Shipped as: spec `docs/superpowers/specs/2026-07-28-admin-broadcast-design.md`
+ impl plan `docs/superpowers/plans/2026-07-28-admin-broadcast.md`, seven tasks
on branch `admin-broadcast`. Proposed #1, filed that morning by the delivery
feed's own revision pass. Sub-project **C** of three and the last to ship; **A**
(the rehearsal harness) is now the only one left.

**Why it exists, in the owner's framing rather than the feature's: detection
without remedy is half a tool.** B can tell you forty people received a wrong
DM; before this, the only thing you could do about it was nothing. It is also
the most dangerous route in the application -- everything else here reads, or
writes rows only the owner sees, while this one puts text into other people's
Discord DMs at a scale the sender picks, with no recall once it is on the wire.
So the design question was never whether it exists but what makes it
survivable, and the answer is four rails the owner asked for by name: a preview
that writes nothing, a typed confirmation above ten recipients, a guaranteed
undo window, and a permanent audit record. A fifth came nearly free from the
audit table -- the compose page warns when an identical body went out inside
the last hour, which is both the stale-tab resubmit and the "did I already send
this?" question during an incident.

**Decision one: all three recipient modes are RESOLVED, never derived.** BATCH
(the recipients of one `delivery_log` batch), ALL, and EXPLICIT (typed Discord
ids) each reduce to a known list of user ids before anything is queued. Two
obvious modes were considered and REJECTED for the same reason -- everyone
tracking a concert, and followers of a tag -- because both are derived: the set
can change between the preview an admin approved and the send that executes, so
the count they confirmed would be a lie. Every surviving mode is resolved, so
that class of bug does not exist here at all. (The tag-followers mode is also
the one most likely to be a mass-send while FEELING targeted, since a popular
franchise tag may be most of the userbase.) The same reasoning made send
re-resolve from mode + param rather than trust a snapshot posted back in the
form: tampering was never the threat -- only admins reach the route, and
EXPLICIT already accepts arbitrary ids -- drift was, and `recipient_count` has
to record what was queued. In the same spirit, an EXPLICIT id matching no user
is REPORTED in the preview and never silently dropped: quietly discarding a
mistyped id is how you conclude you messaged someone you did not.

**Decision two: the undo window is the answer to an otherwise unrecallable
action.** It is the only rail that helps AFTER the press, which is when
mistakes are actually noticed, and it is what turns "you cannot take this back"
into "you have two minutes". Mechanically it is one nullable column --
`Notification.send_after_utc`, `HOLD_SECONDS = 120`, a constant rather than a
setting because that is one fewer thing to get wrong at 3am (owner ruling) --
plus `broadcast_id` as the handle Cancel deletes by. Both nullable, and NULL
means exactly the pre-broadcast behaviour, which is the single most important
property of the change, since it modifies the drain query every notice in the
app passes through: `due_notifications`' `send_after_utc IS NULL` branch is
load-bearing rather than defensive, because SQL evaluates `NULL <= now` as NULL
and dropping it stops the entire outbox. That is a CLAUDE.md rule now, and the
plan had the implementer PROVE it by mutation -- drop the branch, watch the
no-hold regression test fail, restore. **And the cancel race is reported rather
than hidden**: a tick can drain rows between the click and the delete, so
Cancel removes only unsent rows and the status page reads "cancelled -- 12 of
40 had already been delivered". A rail that lies about what it undid is worse
than no rail.

The message is plain text, no embed, so it rides `_notification_context`'s
existing `concert_id=None` path exactly as `ops_alert` does and the scheduler's
send code did not change at all. The admin types ONE body in one language;
each recipient gets it under a frame resolved in THEIR language
(`From dekimasen.app` / `dekimasen.app より` / `来自 dekimasen.app`, the brand
never translated), applied at QUEUE time via `gettext_in` because a row is
written per recipient anyway and their language is already in hand. Requiring
three bodies under the all-three-or-none rule was rejected: an incident remedy
is written under time pressure, and a rule that blocks sending until three
translations exist is a rule that will be fought.

**One entry-level claim turned out to be wrong, and correcting it was Task 1.**
The Proposed entry said the kind was "ALREADY in `UNREPORTED_NOTE_KINDS` ...
and should not be re-litigated". It was listed there, and it was wrong to be:
the feedback loop that set guards is specific to the digest reporting on
ITSELF, and a broadcast terminates after one hop (broadcast -> logged -> one
digest line -> digest delivered -> not logged -> stop). Logging broadcasts is
not merely harmless, it is the point -- whether the remedy reached its
recipients, `FORBIDDEN` ones included, is the question you send it asking, and
the exclusion suppressed exactly that. B's Shipped entry above is annotated
rather than rewritten, so the reasoning stays findable.

**Two obligations came out differently than the spec predicted.** The
`/privacy` page needed a sentence after all: no new category of USER data is
stored, which is what the spec checked, but "Why we collect it" said the data
is used "only to send the event deadline reminders you asked for" and "not for
anything else", and an operator DM about the service is another use. One
sentence added in all three languages rather than an edit to the existing
msgid, which would have silently dropped its translations. And the migration
needed a deploy note: `broadcast_id` carries a foreign key, Alembic cannot add
one on SQLite outside batch mode, and batch mode is a copy-and-move rebuild of
`notifications` -- the DM outbox, written by the scheduler every 60s and by
`handle_newly_tagged` from web routes. A raw column-level `REFERENCES` and an
FK-less column were both built and rejected during implementation (see the
spec's Deviations); the rebuild deploys with the service STOPPED, which is now
its own section in `docs/deploy.md` alongside the column-drop migration's
reversed order.

One trap the build caught and is worth repeating: the typed-confirm test
originally monkeypatched `service.TYPED_CONFIRM_THRESHOLD` and would have
passed VACUOUSLY, because `admin.py` does `from app.db.service import
TYPED_CONFIRM_THRESHOLD` and binds the value into its own namespace at import
time. It seeds fifteen real users instead and exercises the real constant.

Revision pass: recorded in the narrative above -- nothing re-ranked on merit,
entries renumbered 1-14 by pure removal, A's argument restated as fact rather
than promoted, and one line of B's Shipped entry corrected.

### Delivery feed: a durable record of every DM, plus a per-tick digest (2026-07-28)

Shipped as: spec `docs/superpowers/specs/2026-07-28-delivery-feed-design.md`
+ impl plan `docs/superpowers/plans/2026-07-28-delivery-feed.md`, seven tasks
on branch `delivery-feed-impl`. Sub-project **B** of three; not a Proposed
entry, so nothing moved up from Proposed -- the owner asked for it directly.
Its revision pass filed the other two as Proposed #1 (C) and #3 (A).

**The problem it closes.** A production incident in which the bot DMed the
wrong people was, until now, invisible: `reminder_queue` can answer "who was
DMed about this" by joining back through the rules, but those rows are not
evidence. `sync_rule` deletes rows it no longer plans and a deleted round
cascades them away, so the trail vanishes exactly when a bad concert edit is
the thing being investigated. Hence a separate `delivery_log` with
DENORMALIZED labels and SET NULL id pointers: a row outlives the catalogue it
describes. `user_id` is CASCADE, which is not optional -- the table records
which events a named person was reminded about, and `POST /me/delete` is one
`session.delete` relying on cascades (invariant 5).

**Both drains, not reminders alone.** The incident class this exists for is
"messages sent to the wrong users", and the likeliest cause in this codebase
is `handle_newly_tagged` fanning a `new_event` NOTIFICATION across a tag's
followers -- the cleanup batch's ruling 1 shipped one instance of exactly
that a day earlier. A reminders-only log would have been blind to it.

**The digest is impersonal on purpose.** One DM per admin per tick,
failure-first, grouping what was sent and COUNTING the recipients rather than
naming them. Three reasons, and the third is the load-bearing one: identity in
a DM builds a permanent record of who follows which artists in a place
`/me/delete` cannot reach; a 100-reminder tick would blow Discord's
2000-character ceiling; and the recipient count IS the anomaly detector -- a
group reading x40 on a three-user app is the tell, which a per-recipient list
would bury. Grouping therefore keys on IDS, never labels: `due_reminders`
resolves titles per recipient with `loc_field(..., user.language)`, so
label-keyed grouping would split one fan-out across languages and halve the
number that carries the whole signal.

**Three hazards the build had to design around, all named in the plan.** The
digest reporting its own delivery -- closed by `UNREPORTED_NOTE_KINDS`, which
lists C's future `admin_broadcast` UP FRONT, because discovering that
afterwards means finding a DM loop in production; it is now a CLAUDE.md rule
next to invariant 4. (Corrected the same day, when C shipped: the broadcast was
REMOVED from that set. Listing it up front was the right instinct applied to
the wrong kind -- see C's entry for why a broadcast terminates after one hop.
The rule itself stands, for kinds that report ON deliveries.) Logging endangering delivery -- the log write gets its
own try/except and its own commit AFTER the bookkeeping commit, the same
isolation the health block uses, because by then the DMs are on the wire and a
rollback of `sent_at_utc` would re-send every one of them next tick; there is a
test that breaks the writer and asserts the reminder stays marked sent. And
unbounded growth -- a 30-day prune on the existing every-5th-tick health
cadence rather than a new cron, 30 to match `deploy/backup.sh`'s S3 lifecycle
so the system has ONE retention number.

`/admin/deliveries` is the reader: recent failures (the incident view), the
batch list, and one batch expanded to its actual recipients. That last screen
is the deliberate other half of counts-in-the-DM -- identity lives behind
`require_admin`, inside `/me/delete`'s reach, on the 30-day window, rather
than permanently in Discord history. English-only and unwrapped, following the
`/me/test-dm` precedent; the page ships in the same commit as the `/privacy`
disclosure it makes necessary, in all three languages, because shipping the
table without the disclosure is the state to avoid.

One deviation from the spec, applied throughout: the table is `delivery_log`,
not `reminder_deliveries`. The spec named it for reminders and then specified
logging both drains, so the original name described half its contents.

Revision pass: recorded in the narrative above -- two entries ADDED (C at #1,
A at #3), nothing re-ranked on merit, the admin catalogue export pushed to #2
by insertion while gaining a second use, entries renumbered 1-15.

### Cleanup batch: five debts, two owner rulings (2026-07-28)

Shipped as: spec `docs/superpowers/specs/2026-07-28-cleanup-batch-design.md`
+ impl plan `docs/superpowers/plans/2026-07-28-cleanup-batch.md`, four tasks
on branch `cleanup-batch`. Clears Proposed #2, #3, #4, #5 and #7; #6 is
disposed of in Rejected below.

**One entry, not five, deliberately.** These five have nothing to do with
each other -- a slug rule, one dialog's copy, a notification suppression, a
client-side fold listener, a docstring -- so five headings would be the
easier lookup. They are logged together anyway because the BATCH is the unit
of decision here, not any item in it: the two owner rulings cut across the
list (ruling 1 governs the tag pipeline; ruling 2 disposes of an entry that
was never built), the spec, plan, branch and review waves are shared, and
the honest account of why five low-ranked entries shipped at once is an
account of the batch. The five are named in bold below so the lookup still
works.

**Owner ruling 1: a tag attached to a dead concert notifies nobody and
applies nothing.** The old #4 left two decisions open and this closes both.
Rejected: rewording the notice (one nobody can act on still costs a msgid in
three languages) and keeping the preset (invisible rules on a dead event,
justified only by a revival that may never come).

**Owner ruling 2: a dead board card keeps one badge, not per-rung marking.**
Left exactly as shipped -- see Rejected.

- **`event_id` slugs prefer `title_en`** (old #2). `slugify` strips
  everything outside `[a-z0-9]`, so a Japanese-only title collapsed to the
  `"concert"` fallback and imports minted `concert-2`, `concert-3` --
  unique, meaningless in a URL whose job is to BE the identity (invariant
  6). One line of preference at the function, which turned out to matter:
  the spec framed the defect at `generate_event_id` but named only the
  importer, and `POST /concerts/{event_id}/duplicate` calls it too, so both
  callers inherit the preference and a duplicate now mints
  `<english>-copy`. No backfill -- existing ids are editor-owned.
- **The unfollow dialog stops overstating** (old #3). Its two LIVE branches
  promised "we'll remove that mark and the payment reminder" / "...and its
  reminders". The reminder half was true; the mark half never was -- an
  opt-out does not delete a `RoundOutcome` (invariant 8), and a reader who
  believed the sentence thought unfollowing erased the ticket they recorded,
  which is exactly the fear that stops the press. Both now name the reminder
  loss and state the record survives, in three languages.
- **Nothing is announced about a dead concert** (old #4, ruling 1). The
  tenth surface of the dead-concert rule, and the one the cancelled-concerts
  branch could not have listed, because it fires on TAGGING rather than on
  cancelling: `sync_concert_venue_tags` runs `handle_newly_tagged` on every
  venue rollup, so a routine leg edit on a dead tour DMed every venue
  follower a 🆕 "Apply here" and applied their preset behind it. Two things
  the entry did not foresee are recorded in the spec's deviations. The
  predicate could NOT read `concert.days`: two call sites reach the pipeline
  with that relationship silently empty, so the question would have answered
  "alive" unconditionally on exactly the automatic path the ruling targets
  -- it runs its own indexed SELECT. And `edit_concert` was asking the
  question at the wrong point in BOTH directions -- it attached tags at the
  top and reconciled legs 100 lines below, so un-cancelling a leg while
  adding a tag suppressed a notice that was owed (permanently: there is no
  re-announce path) while cancelling the last leg while adding one announced
  a show that is off. The call moved to the foot of the route, which also
  fixed something nobody had filed: presets now cover rounds created in the
  same submit and no longer mint rules on rounds it is about to delete.
- **Expanded folds survive an htmx swap** (old #5). The entry's own warning
  was the valuable half -- `open_round_id` is the WRONG instrument, because
  it reopens the fold that OWNS a written round and an opt-out writes none
  -- and the fix is general: every `<details>` in a swappable region carries
  a stable `data-fold` key, open keys are collected on `htmx:beforeRequest`
  and reopened on `htmx:afterSettle`. It only ever OPENS. `open_round_id`
  stays and is not duplicated by it: that half is server-rendered, so it is
  the half that works with JS off. The listener keys its collected set per
  REQUEST rather than in one module variable -- two overlapping requests
  interleave into a set that settles empty otherwise.
- **Importer review debt** (old #7), minus one item. The `DraftError`
  fallback that could never fire, `_text`'s container guard now warning
  where it used to blank silently (WITHOUT stringifying the value -- that
  stringify is what the alias-fan-out DoS fix removed), and
  `match_tag_ids_by_name`'s docstring stating its first-tag-wins order and
  its blank-name drop. **Item (d) was a phantom**: the `preferences.html`
  preset-item form was said to write its action with backslashes, and it
  does not -- not at HEAD, and not at any commit checked back through
  `6855538~1`; a tree-wide scan found no backslash in any URL in any
  template. It is struck rather than recorded fixed. Worth the sentence
  because the entry existed to stop these being rediscovered, and a fake
  one is the most rediscoverable kind.

The batch's own reviews found four things worth naming beyond the items.
Three became the polish pass that closed it: the fold listener's overlapping-
request hole above, and two assertions that were passing without testing
anything -- a script guard asserting `htmx:beforeRequest` appears in the
page, which the `#hxbar` progress bar had satisfied since long before the
feature, and a fold-state helper whose regex was attribute-order-coupled,
the exact fault it existed to fix, so an attribute inserted in the wrong
place would have returned an empty set and made every NEGATIVE assertion
vacuous. The fourth is filed as Proposed #7.

Revision pass: recorded in the narrative above -- nothing re-ranked on
merit, #1 untouched, entries renumbered 1-13 with two insertions.

### A concert whose every leg is cancelled stops asking you to act (2026-07-27)

Shipped as: spec `docs/superpowers/specs/2026-07-27-dead-concerts-design.md`
+ impl plan `docs/superpowers/plans/2026-07-27-dead-concerts.md`, six tasks on
branch `cancelled-concerts`. Proposed #2, filed the same day from the
ladder-declutter branch's final review as a pre-existing defect that build had
made starker. The entry asked for a concert-level question `is_round_cancelled`
cannot answer, and that is exactly what shipped: `all_legs_cancelled(days)`,
the Python twin of `discoverable_concert_criterion` (the same rule, already in
SQL, already hiding these concerts from Discover), pinned to it by an agreement
test so the two cannot drift.

**Owner ruling on the entry's one open question -- "leaves the board entirely
or shows as a cancelled card?": both, decided by the reader's standing.**
Applied, won or paid keeps the card, badged Cancelled and never in *Open now*,
because a cancelled show you hold a ticket for is news. No standing and the
concert leaves the board, matching Discover. Always-stay was rejected outright:
the board would fill with dead events the reader never had a stake in, the
opposite of the de-crowding shipped two entries earlier.

The entry named three surfaces. The branch touched nine: those three, the
planner (added deliberately at spec time), and five more found by review. Two
of those six additions were beyond the entry in ways worth recording. **The
planner was pulled in deliberately at spec time** -- a general round on a dead
concert still planned DMs saying "apply now", which is the worst instance of
the lie the entry describes, and leaving it out would have fixed the screens
while the scheduler
kept contradicting them. **`/setup` was found genuinely broken**, not merely
stale: `_round_asks_application` carries its own eligibility rule, never goes
through `capture_gates`, and nothing upstream filtered dead concerts, so a dead
concert with a general round closing next week reached the applications screen
and offered to record an APPLIED that `record_round_outcome` would never let
the reader take back -- precisely the harm the entry opens with, on a screen
neither the entry nor the spec looked at. The other four review-found surfaces
were the cancellation notice (the planner's deletions would otherwise have
taken reminders away silently), the bot's `/upcoming`, `ShowDeadlinesButton`,
and the follow toggle, whose caption promised "you will be reminded about every
round below" some 40px under the new cancelled banner.

The branch's final review found the Discord half had stopped one button short
and added two more surfaces (eleven now, thirteen call sites):
`ReinstateRemindersButton` reported `reinstate_user_rules`'s return value as
reminders re-armed, but that counts RULES re-synced -- they survive a
cancellation untouched, only their queue rows go -- so on a dead concert it
promised notifications the planner can never send, on a DM this very branch
had just widened to fire for whole-event death. And the cancellation DM itself
said "a performance you had a reminder for was cancelled" for a show that is
off entirely, understating the one notice that carries the news that every
reminder here is gone, a won ticket's payment reminder included. The same
review found the new dead-concert unfollow copy claiming an opt-out removes
the won mark, which it never does -- fixed on the branch; the two pre-existing
live branches making a version of the same claim were filed as their own entry
and shipped in the 2026-07-28 cleanup batch (see its Shipped entry above).

One real design defect surfaced mid-build and is recorded in the spec's
deviations section: passing `has_open_round=False` into `column_for`, as the
spec specified, could not deliver the owner's ruling, because a dead concert's
leg-bound rounds are dropped before outcomes are gathered -- so only
general-round standing kept a card, while a 先行 lottery, which names its legs,
is the common real shape. A dead card's outcomes AND rungs now come from the
concert's full round set; the countdown is suppressed, since a badged card
reading "closes in 3 days" is the same lie in a smaller font.

Revision pass: this is the first ship in a while that does NOT change the top
of the list -- the admin catalogue export was #1 and stays #1, untouched, and
event_id slugs rises to #2 by pure removal. Nothing
re-ranked on merit and nothing got cheaper: this build lived in the planner,
the board and the capture surfaces, none of which the remaining entries go
near. Entries renumbered 1-14, and again to 1-17 by the final review's three
filings (which is where the numbers in this paragraph point); the sign-in-bounce
entry's demo-parity/Discover-head pointer was bumped in place again. Two of the
remaining entries were re-read against what shipped and both stand: the opt-out
fold reset (since shipped in the 2026-07-28 cleanup batch) is a sibling defect
on the same page but an
htmx-fold-state problem with nothing to do with cancellation, and minute-level
offsets is untouched, since a dead concert now plans no
reminders at any offset. Worth recording for whoever takes the list next: with
this gone, every remaining entry is assistant-raised or review-raised except
the admin export, and the two defects the ladder-declutter review filed
together are one shipped here and one shipped in the cleanup batch since.

### Group the performer chips by group on the concert page (2026-07-27)

Shipped as: spec `docs/superpowers/specs/2026-07-27-performer-clusters-design.md`
+ impl plan `docs/superpowers/plans/2026-07-27-performer-clusters.md`, three
tasks on branch `performer-clusters`. Proposed #1, filed 2026-07-26 from the
owner's usage feedback ("insanely crowded") and built the day after the entry
that outranked it shipped. The Performing panel is one labelled block per
attached GROUP now -- the group's chip on a row of its own, that group's
attached performers wrapping underneath -- followed by an unlabelled trailer
for performers in no attached group. The flat row it replaced listed every
group chip and then every artist chip, which on a two-group bill said nothing
about who belonged to which.

**Owner decision 1: a performer in several attached groups appears under EACH
of them.** The repetition is information -- she really is in both -- and the
clustering, not deduplication, is what fixes the wall. Showing her once under
the "most specific" group would leave the other group's cluster looking
incomplete, misleading exactly the reader who came to see that group.
**Owner decision 2: no folding.** The Performing panel is reference, not a
to-do: you read it, you never act on it, so a click to see who is playing is
friction without payoff. Labelled clusters are the whole fix. **Owner ruling,
mid-build: at zero the count disappears.** A group attached with none of its
members attached kept its label row (deliberate -- dropping it would hide a
tag that IS attached) but the new header then read "-- 0 performers". That is
the opposite of the truth: a groups-only bill is a line-up nobody has listed
yet, not a concert with nobody on it. The count is simply absent in that
state, which needed no new msgid -- the absence carries it.

The grouping is derived SERVICE-side (`db/service.py:performer_clusters`),
never in the template: `Tag.members` is a lazy self-referential m2m and a lazy
load during async template rendering is the `MissingGreenlet` 500 this project
has already shipped once. Membership is ONE batched query over `tag_members`
for the attached group ids -- deliberately not the existing per-group
`group_members` helper, which would be an N+1 on a franchise bill -- and a
test asserts the statement count so the loop cannot creep back in.

No new msgid anywhere, which was the point: the header's distinct-performer
count reuses the existing plural pair `%(count)s performer(s)` already on the
Tags page, and the three composed msgids the old header assembled ("N members,
from the X group tags") were retired from both catalogues by hand -- pybabel
had only commented them out as `#~`, which is the drift that goes uncaught.
The count is DISTINCT, not the sum of the blocks: someone in two attached
groups occupies two seats but is one person.

Revision pass: this closes the LAST of the owner's 2026-07-26 usage-feedback
batch except the admin catalogue export, which rises to #1 by pure removal and
is unchanged in size -- it touches the catalogue tables and a zip route,
nothing this build went near. Nothing moved on merit and nothing got cheaper.
Entries renumbered 1-15; the sign-in-bounce entry's demo-parity/Discover-head
pointer and the ladder-declutter entry's pointer at its two filed defects were
both bumped in place. Worth recording: with this gone the list is entirely
assistant-raised and review-raised behind the export, the first time since
2026-07-26 that no owner-reported pain point sits in the top few.

### Cap the board card ladder at the rungs that matter (2026-07-27)

Shipped as: spec `docs/superpowers/specs/2026-07-27-ladder-declutter-design.md`
+ impl plan `docs/superpowers/plans/2026-07-27-ladder-declutter.md`, four
tasks on branch `ladder-declutter`. Proposed #1, filed 2026-07-26 from the
owner's usage feedback and built the day after the entry that outranked it
shipped. The entry's one open question -- "exact collapsed shape is an owner
UX call (fold vs cap vs scroll)" -- was answered: **cap, never expand.** A
board card shows at most `VISIBLE_RUNGS = 2` rungs plus a plain, inert
"+N more rounds" line. Not a `<details>`, deliberately: uniform card height
is what makes four columns scan as a board, and an expandable card gives
that back the moment anyone opens one. Nothing on a card is interactive
anyway -- capture lives on Coming-up rows, an existing invariant -- so
hiding rungs costs the reader nothing, and the full ladder is one click away
on the concert page.

The selection is a pure function, `domain/board.py:visible_rungs`, sitting
beside `column_for` and `pill_tone` and taking the already-built rung list
so the ORM stays out of pure code. It keeps the rung that EXPLAINS the
card's column plus the first "live"/"todo" rung after it. Which rung
explains the column is the one thing the plan got wrong and review caught:
"the last non-todo rung" reads by POSITION, and on the ordinary
mid-campaign shape `[lost, won, live]` -- a card sitting in "Won -- pay"
because money owed outranks a round you could still enter -- position
surfaces the open round and hides the win, leaving the card naming a column
nothing on it explains. It now ranks by STANDING using `column_for`'s own
precedence (won-upgrade > paid > won > applied), which forced `Rung` to
carry `is_upgrade`: without it a `[paid, todo, won-upgrade]` ladder shows
the paid rung while the card sits in "Won -- pay", the same failure in the
upgrade corner. Surviving rungs keep their ORIGINAL ladder numbers -- a
rung's mark IS its place in the full ladder, so rungs 3 and 4 of five stay
"3" and "4". The count line reuses the existing "+N more round(s)" msgid
from the Coming-up fold rather than minting an "earlier rounds" pair, which
would have been a lie: the state rung can sit mid-ladder, so hidden rungs
are not all earlier ones.

Also paid off here: the query debt the spec carried in section D, which NO wishlist
entry tracked -- it was recorded in the spec when the "Coming up" build
amplified it, and folded into this arc rather than filed. `covered_round_ids`
was called once per secured concert; the batched sibling
`covered_round_ids_by_concert` takes one pass over the whole page. Home fell
from **42 statements to 19** on a 12-concert page (pin tightened 45 -> 22).
It is a pure refactor by construction: the shared fold `_covered_from_secured`
is byte-identical and the planner's suppression suites were untouched and
stayed green, which is the equivalence evidence.

Revision pass: the concert-page per-leg fold shipped alongside this on the
same branch and has its own Shipped entry below -- it was raised by the
owner after this list was written, so it never appeared in Proposed.
Performer-chip grouping rises to #1 by pure removal, unchanged in substance:
it touches the concert page's Performing section, which neither half of this
build goes near. Nothing else moved on merit. Worth recording for whoever
takes #1: this build ADDS a reason to look at the concert page's chip wall,
since the page's round list is now short enough that the chips are the most
crowded thing left on it. Entries renumbered 1-14; the sign-in-bounce
entry's demo-parity/Discover-head pointer was bumped in place once more.

Final-review addendum (2026-07-27): the cap surfaced a latent defect in the
rung vocabulary and the same wave fixed it, so it is recorded here rather
than filed as its own entry. `_rung_state` mapped NOT_APPLIED onto "todo",
making a round the viewer had declined indistinguishable from one that had
not opened -- so with only two rungs to spend, a closed declined round could
take the card's "what's next" slot and hide a genuinely open one, while the
per-leg fold below counted that same round under its "skipped" chip. A
declined round now has its own state, mark and label, sharing `_FOLD_KINDS`'
word so the two declutter surfaces agree. `visible_rungs` needed no change:
"skipped" is settled, so it already qualified for the state-rung fallback and
was already excluded from the live/todo lookahead, and it takes no
`_RUNG_STANDING` entry because `column_for`'s `_RANK` places nothing for
NOT_APPLIED. The review's other two findings were pre-existing and were filed
in Proposed (the cancelled-leg-only concert and the opt-out fold reset, both
shipped since -- the second in the 2026-07-28 cleanup batch).

### Fold settled rounds per leg on the concert page (2026-07-27)

Shipped as: the second half of the same spec, plan and branch as the entry
above. Never a Proposed entry -- the owner raised it on 2026-07-26, after
the per-leg-outcomes build made a round render under EVERY leg it covers and
a 3-leg x 6-round concert became eighteen rows, most of them settled
history. Logged here rather than backfilled into Proposed, the same way the
mobile retrofit and the signed-out redirect were.

One `<details class="moreround">` per leg, closed by default -- the owner's
call over a single page-level toggle: you expand the leg you came for
without expanding the other two, which is also how the page is already
structured and how Home's blocks work. What stays visible is ONE rule, in
`service._split_leg_rounds`: the round that secured the leg (its receipt --
a second owner decision, so you can always see which round got you in
without expanding, even once fully paid), anything that still wants
something from you, an upgrade round you are eligible for, and -- when the
leg is not secured -- exactly one upcoming round you could still enter.
Everything else folds: losses, skips, rounds made moot by a ticket you
already hold, locked upgrades, and every unopened round after the next one.
A cancelled leg folds entirely.

That "still wants something from you" clause is `_wants_you`, and this is
its THIRD consumer, not a redefinition: Home's block lead, the concert
page's "Next for you" strip and this fold now answer the same question the
same way, pinned by the agreement test #98 added. Two rulings came out of
review. Clause 4 excludes upgrades from its single slot entirely -- a locked
upgrade is not enterable and an eligible one is already visible, and without
the exclusion the slot could go to the locked one and fold the round you
could actually enter. And a COVERED round with a recorded APPLIED stays
visible: a pending application is an open obligation regardless of what else
you hold, so `_needs_you`'s covered veto is a ranking rule for the
one-moment strip, not a hiding rule. The spec's prose was corrected to match
the code, not the other way round.

The summary carries `+N more round(s)` plus state chips -- `N lost`,
`N skipped`, `N covered`, `N upcoming` -- each its OWN msgid with its own
plural. Deliberately chips rather than a composed sentence: assembling
"3 earlier rounds -- 2 lost, 1 skipped" from fragments is a word-order trap
in ja/zh, and this project's i18n rule is that translators own word order.
Chips make the ordering a layout question instead. Folded rows are the SAME
markup as visible ones through the same partial, so the fold hides rows and
never changes them, and a folded round's capture form stays in the DOM and
works -- verified by pressing one. One unbriefed fix earned its place: the
outcome POST swaps the whole region, so a round answered from inside a fold
used to vanish at the moment the reader acted on it; the route now passes
the written round's id and that fold comes back open, the same server-side
fix (no JS, no client-held state) Home got in #98.

### De-crowd "Coming up": one block per concert, two folds (2026-07-27)

Shipped as: spec `docs/superpowers/specs/2026-07-27-coming-up-decrowd-design.md`
+ impl plan `docs/superpowers/plans/2026-07-27-coming-up-decrowd.md`, four
tasks on branch `coming-up-decrowd`. Proposed #1, filed 2026-07-19 as the
per-anchor half and expanded 2026-07-26 with the many-rounds-per-concert
dimension; built the day the entry reached the top of the list. Home's
"Coming up" is no longer a flat list of anchors: `my_deadline_blocks`
(`db/service.py`, built ON `my_deadline_rows`, no second derivation)
collapses each round to its soonest future anchor, groups what is left into
one `ConcertBlock` per concert, and hands the template a header line
(title, venue, performance date), a LEAD row, and the rest. The concert is
named once per block instead of once per anchor, which is the de-crowding.

Which row leads is the same question the concert page's "Next for you"
already answered, so it is now literally the same rule: `_needs_you` was
generalized to a primitive `_wants_you(outcome, can_capture, closes_at_utc,
now)` that both surfaces call -- standing first (APPLIED awaiting a result,
WON owing payment), then soonest -- pinned by a test asserting the two
answer identically on shared inputs, so a future edit cannot drift them.
Two native `<details>` folds carry the overflow: "+N more rounds" inside a
block and, past `VISIBLE_BLOCKS = 6` of `DEADLINE_ROWS_LIMIT = 10`
concerts, a page-level "+N more events". Both are PRESENTATION, never
filtering -- every folded row is in the DOM with its capture form intact,
so the fold can never become a second silent limit and the htmx swap and
the fold always agree about how much exists. `DEADLINE_ROWS_LIMIT` keeps
its name and its role as the one constant `GET /` and
`POST /rounds/{id}/outcome` share, and now counts concerts.

Two owner rulings landed at the end. The page-level fold says "events",
not "concerts", matching the empty state directly above it -- a msgid
change, with both catalogues' existing msgstrs carried across byte-for-byte
because ja/zh already used their event-word. And the hairline BETWEEN
member rows came back: the first pass moved it to the block boundary only,
which read as one run-on paragraph once a three-round block was expanded.
It returns as a border-top on revealed rows only, so a collapsed block
still shows exactly one rule and the fold summary is never boxed.

Measurement (seeded temp DB + iframe harness at 375/730/1200 in both
themes, per the measure-don't-reason rule) earned its keep twice: it caught
a PRE-EXISTING tablet-band bug -- the band's `data-happens` `::after` had
been dead since the band shipped, killed by a main-body `display: none`,
and its content string's CSS escape ate its own separator space -- and it
rejected the first separator placement, which sandwiched the collapsed
fold's summary between two hairlines. Both fixed, the first with a new
guard test so a silent re-break fails CI. Other deviations from the spec
are recorded in the spec itself: `_needs_you` keeps its `covered` veto
outside the shared primitive (per-leg outcomes added it after the plan was
written), and both `data-happens` and `.act-c` carry the anchor verb alone
now that the cell beside them names the round.

Revision pass: board-card ladder collapse rises to #1 by removal, and this
build makes it CHEAPER without making it more urgent -- the fold vocabulary
it will need now exists and is proven (native `<details>`, our own rotating
caret, the "+N more" summary shape, and a house rule that a fold hides
nothing the DOM lacks), so the open question there is unchanged and still
the only real work: what the collapsed ladder SHOWS is an owner UX call,
not a mechanism. Not re-ranked on merit: cheaper is not higher-impact, its
medium-high reading stands, and it already sat directly behind the entry
that just shipped. Nothing else moved -- performer-chip grouping, the
catalogue export and everything below touch other surfaces entirely, and
this build neither obsoletes nor enables them. Entries renumbered 1-15; the
sign-in-bounce entry's demo-parity/Discover-head pointer was bumped in
place once more.

### Per-leg outcome truth: covered rounds stop asking, wins record per day (2026-07-27)

Shipped as: spec `docs/superpowers/specs/2026-07-26-per-leg-outcomes-design.md`
+ impl plan `docs/superpowers/plans/2026-07-26-per-leg-outcomes.md`, nine
tasks plus a whole-branch review wave. Proposed #1, filed 2026-07-26 from the
owner's usage feedback (the secured-user nag and the all-legs display dislike
were the same modeling gap), built the next day. `RoundOutcomeDay` now layers
per-day WON/LOST under the existing per-round `RoundOutcome`, and the
"secured elsewhere" suppression that only the reminder planner honoured is
threaded through every read surface via one shared fold
(`_covered_from_secured` / `covered_round_ids`), so Home, the concert page,
`/setup` and the DMs can no longer disagree about what is still worth asking.
The concert page's separate all-legs section is gone with it: a round renders
under each leg it covers, each leg reading as a complete story, which is only
truthful because standing is per-leg now. Capture is progressive on both
surfaces -- Discord's DM buttons and the web's per-day forms funnel into the
same `record_round_day_result` / `record_remaining_days_lost` writers -- plus
a catch-up dialog on the concert page for the whole-round shortcuts a
leg-scoped card cannot honestly offer.

The design deviations are where the value is. Day rows are MATERIALIZED
rather than inferred: no rows means the round settled every leg it covers
(which is every row predating this build and every single-leg round), and the
first explicit write materializes the implicit ones first, so the convention
never has to be re-derived downstream. PAID is never demoted -- a lost leg
moves the LEG, not a round you have already paid for. A round you won is
never "covered", by anything, including another win over the same nights:
covered answers the apply/results question, and money you owe is not settled
by holding the seat twice. `set_leg_opt_out` took ownership of its own
`reinstate_user_rules` resync, because the read-side suppression only governs
what the NEXT sync decides and a materialized outbox had already queued the
reminders (invariant 8). Leg cards are scoped to ONE leg -- three questions
per leg, not nine on a three-leg concert -- and the shortcuts live in the
dialog. Two review waves fixed real holes: the DM's whole-round shortcuts
going stale against a round resolved elsewhere in the meantime, and (final
wave) the web still offering "Won (all)" on a round already being resolved
leg by leg, where the write secures nothing and the next lost leg erases it.
The same wave made opt-out and cancellation COMPOUND with a win in the
covered fold -- "won Sat, not going Sun" now silences the other Sat+Sun
rounds, the exact case the owner reported -- guarded so a wholly opted-out
round stays with the every-leg opt-out pass rather than being called
"covered" on the strength of no win at all.

Revision pass: "De-crowd Coming up" rises to #1 by removal, and it is
CHEAPER than when it was filed -- covered suppression already deletes some
of the rows it was going to collapse, so the work is now sizing a smaller
set rather than the row budget it was scoped against. Not re-ranked on merit
even so: cheaper is not higher-impact, its impact reading (medium-high) is
unchanged, and it already led everything but the entry that just shipped.
Nothing else moved -- the board-ladder, performer-chip and export entries
touch different surfaces entirely, and this build neither obsoletes nor
enables any of them. Entries renumbered 1-16; the sign-in-bounce entry's
demo-parity/Discover-head pointer was bumped in place once more.

### UX pass 2026-07: page polish + the callout grammar (2026-07-24)

Shipped as: spec `docs/superpowers/specs/2026-07-24-ux-pass-diffs.md` +
concept demo `docs/superpowers/demo/dekimasen-ux-pass-demo.html` + impl plan
`docs/superpowers/plans/2026-07-24-ux-pass-impl.md`, from one brainstorm arc.
Not a Proposed entry -- logged in Shipped per the convention. 20 changes in
five batches: board column-head colours (A1), Home discovery teaser+peek
merge (A2), Discover active-filter chips with full client-side rebuild (B1)
and live section counts (B2), the "Next for you" strip moved into the
concert header with a countdown over the standing pill and oob-swapped on
outcome capture (C1), "via <tags>" beside Following (C3), the numbered
create-form spine (D1), the single-msgid JS-built covers legend (D2), the
tags chips⇄table view (E1) and follow bell (E2, notify ON, next=/tags), the
htmx progress bar (F1), the global focus ring + the picker input's
`outline: none` fix (F2), the outcome toast (F3 -- not_applied excluded: the
prune dialog is its confirmation), the 400/600/700 weight ramp (G1), the
two-shape callout grammar `.edgecard`/`.banner` absorbing ten one-off
treatments (G2), the radius-system comment (G3), light `--ok` nudged to
`#187a49` for WCAG AA (G5), and `.badge` at 3px (G7). Rejected on review:
C2 (kebab stays destructive-only) and the A1 breathing dot (motion budget:
one hover, zero decoration). Owner review caught a C1 staleness regression
(strip froze on outcome capture) before it shipped -- fixed via the oob
contract, with `test_concert_page.py` pinning it. Revision pass: nothing
re-ranked; the pass touched no Proposed entry's ground. Local-env note for
#8: the owner's `.venv` drifted to Python 3.12 against `.python-version`
3.14 with dev deps missing, which broke `uv run` outright until the
project's own processes holding `.venv/Scripts` were worked around -- the
pinning entry now has a reproduction on record.

### Inline tag creation for unmatched scraped/draft tags (2026-07-24)

Shipped as: PR #96, after an owner UI checkup that picked per-name create
chips and the kind+names+parent popup (over a generic button and a
minimal name-only dialog). Each unmatched franchise/group/artist name in
the import preview renders as its own + chip; the popup opens with the
kind pre-selected (the draft parser knows which list the name came from),
the name prefilled, optional EN/中文, and a parent-franchise select for
groups (Tag.parent_id, so no Tags-page orphans). Create & select joins
the picker via pickerAddAndSelect and removes the chip; the kind-scoped
409 returns the existing tag's id+name for one-click select-existing.
Groups are created EMPTY -- expansion stays attach-time-only (invariant
3), and no notification fires (creation is not attachment, invariant 4).
VENUE keeps its richer /tags/venue/quick. Found-and-noted: Tag.name is
globally unique across kinds (a pre-existing model quirk create_tag
shares); the endpoint mirrors it rather than diverging. Revision pass:
event_id slugs rises to #1 by removal; nothing re-ranked on merit -- the
review-debt batch (#2) remains the natural pairing for the slugs work,
both being small importer-adjacent cleanups.

### A real tablet layout for the 701-1040px band (2026-07-24)

Shipped as: spec + concept demo PR #94, implementation PR #95, same day.
Grounded in measurements of the real app (locally seeded DB, iframe
harness at 730/760/820/1000/1100) rather than reasoning from CSS. One
banner-commented `@media (min-width: 701px) and (max-width: 1040px)`
section now holds every band rule -- the mobile retrofit's discipline,
replicated: compact one-row header (nowrap wordmark, hidden username,
icon-only Preferences/Sign out), the swipeable 280px-column campaign
board preserving the apply->win->pay story (owner's pick over 2x2),
`.peek` 2-col, the coming-up rows' data-happens fold (the what-happens
column is never dropped without its text landing in the title line), and
Discover's filter sheet as an inline disclosure panel. The scattered
1024/960 breakpoints died into the section (the 1024 rule's phone duty
was absorbed into the phone block first -- deleting it outright would
have resurrected the hidden column on phones); 900/860 stay standalone
on purpose; the `.fsheet`/`.layout` coupling moved to 1040/1041 as one
unit and the phone bottom-sheet overlay re-anchored to 700 (its 760
rationale died with the move). A guard test pins the top-level max-width
query count so breakpoint scatter fails CI. Found-along-the-way: the
global `[hidden]` override bug (every non-upgrade round showed the
upgrade qualifier box on edit pages) shipped separately as PR #93.
Revision pass over Proposed: inline tag creation rises to #1 by removal;
nothing re-ranked on merit.

### Editor and concert pages coherence pass (2026-07-24)

Shipped as: spec + reconciled demos in PR #91, implementation in PR #92 --
filed, designed and built the same day. The four owner decisions from the
brainstorm: keep the flat editor structure (the demo's never-built
nested-rounds concept is retired; the demos were rebuilt to match shipped),
destructive actions move into a top-right kebab menu (`details.kebab`, the
app's first overflow menu, deliberately single-purpose -- the x beside
Cancelled is gone), ja/EN/中文 label trios sit on an always-visible second
row, and the sentence-style reminder builders render through locale-ordered
slot patterns (`domain/sentence.py:split_slots` + the `sentence_slots`
Jinja global) so ja reads 「申込締切の1日前に通知。」 instead of English
word order. The structural win underneath: the six hand-rolled leg/round
card copies collapsed into `_editor_leg_card.html`/`_editor_round_card.html`,
shared by all three editor surfaces AND their <template> blocks. Riders
shipped along the way: offset labels became real msgids (bare 時/时 as the
moment label so the direction-hidden sentence completes), welcome's
JS-added rows are fully translated (clone a server-rendered template), and
both catalogues gained a placeholder-integrity hygiene test. The
concert_detail viewer was untouched by design. Revision pass: the tablet
band rises to #1 by removal only; nothing re-ranked on merit; the
minute-offsets entry was re-checked (the new offset-label msgids change
nothing about its missing minutes column).

### Major i18n translation calibration (owner review pass) (2026-07-24)

Shipped as: PR #88. The 762-row review CSV came back from external
native-level review (a full pass plus a later zh-only overlay that won its
6 conflicts) and 307 ja / 344 zh msgstrs were applied by script:
placeholder sets validated per row, the overlay's renderer-mangled HTML
tags repaired positionally from the msgid, zero rows skipped. Four i18n
smoke tests updated to the calibrated strings. The review's 132
English-source fixes were deliberately NOT applied -- msgids are frozen at
the catalogue layer -- and are extracted verbatim to
`docs/i18n-english-source-fixes-2026-07-24.csv`, tracked as Proposed #3
(since shipped -- see the entry above).
Revision pass: the two design brainstorms this entry deliberately preceded
(corrected wording feeds chip widths, label wrapping, dialog copy) rise to
#1/#2 unchanged in substance, and the reviewer's "fragment -- needs UI
reorder" notes independently confirm the sentence-builder scope already
folded into the coherence pass.
Round 2 (same day, PR #89): after the owner's external-LLM route proved
more trouble than it was worth, an agent proofread of all 762 ja rows
against the approved glossary (round=受付, sign in=サインイン, 購読 banned
with calendar subscribe=登録, upgrade round stays アップグレード抽選)
returned exactly 8 correctness fixes -- two mis-localized literal
add-concert skill names, four banned-term survivals, two sign-in strays
-- and confirmed the calibrated catalogue otherwise holds up.

### Apply the reviewed English-source fixes (132 msgids) (2026-07-24)

Shipped as: PR #90, hours after being filed as Proposed #3. All 132
review rows applied at the source layer (bot cogs, views, 20 templates,
including the trans-block %(var)s <-> {{ var }} conversions); both
catalogues re-keyed under the corrected English with the just-reviewed
msgstrs preserved byte-for-byte; the "{n} reminder(s)"/"active event(s)"
shortcuts in touched strings became real plurals (one ngettext call, two
{% pluralize %} blocks, one latent wrong-selector Jinja bug fixed); two
msgid collisions merged losslessly; 10 test files updated. One row
REJECTED: the visible add-concert -> add-event rename, conditional on
renaming the shipped skill itself -- the follow-up commit also fixed the
zh msgstrs that still carried add-event (the bug ja round 2 had fixed
for ja). The mapping CSV stays in docs/ as the record of what was
reviewed and the one rejection. Revision pass over Proposed: no rank
changes -- the coherence pass (#1) now inherits corrected copy on every
surface it will redesign, which is the sequencing the rider asked for.

### Cache-bust static assets with a per-file content hash (2026-07-22)

Shipped as: PR #84 (`static_url` Jinja global appending a per-file content
hash, `web/static_assets.py`), exactly the fix shape this entry proposed --
Cloudflare now sees each deploy's CSS as a fresh URL and the manual-purge
step is gone from the ritual. Moved here belatedly on 2026-07-23: the PR
deliberately shipped code-only and the WISHLIST move fell through the crack,
so the entry sat in Proposed for a day after it was live. The three-builds-
in-a-row purge pain it recorded (i18n switcher, mobile retrofit, signed-out
redirect) is what finally got it built.

### Downloadable add-concert skill zip on the import page (2026-07-23)

Shipped as: a small distribution affordance layered on the agent-import build
below, the same day. `GET /concerts/import/skill.zip` (editor-gated, in
`routes/imports.py`) zips `src/app/web/skill_dist/add-concert/` at request
time -- a committed binary would go stale the moment the skill changed -- and
a linked line under the import page's paste card hands it out where editors
already work. The dist copy is a deliberate variant of the repo skill
(drift-warning instead of the CI-pin sentence, an editor-access note,
tool-agnostic fetching for recipients on claude.ai rather than Claude Code);
its `example-draft.yaml` is pinned byte-identical to the repo skill's by test,
so the schema contract cannot fork between the two copies. Not a Proposed
entry (raised and built directly by the owner on 2026-07-23); revision pass
over Proposed found no rank changes -- it strengthens the case for the
Eventernote actor-page discovery entry (more editors can now produce
drafts) without displacing anything.

### Agent-driven concert import (YAML draft round-trip + add-concert skill) (2026-07-23)

Shipped as: a way to skip the typing that the trilingual arc created. The
all-three-languages-or-none rule (i18n phase 4) roughly TRIPLED the keystrokes to
create a concert by hand -- three titles, three labels per round, three notes --
and with no budget for a server-side LLM API call the fix had to be agent-side. A
new seam, `POST /concerts/import/draft`, takes a pasted YAML draft and renders
the existing `import_preview.html` fully prefilled (trilingual titles/labels, all
four round anchors, real multi-leg round binding), so an agent does the typing
and the editor only reviews and submits. To anchor the draft vocabulary the YAML
EXPORT (`domain/yaml_export.py`) was made two-way -- `title_zh` and notes
variants added -- so an exported concert round-trips back through a new pure
parser, `domain/yaml_import.py` (`yaml.safe_load` only, warnings over failures,
hardened post-review against hostile structure: RecursionError maps to
DraftError, container values for scalar fields blank safely, and the alias
fan-out DoS is closed). Tag and venue NAMES in the draft resolve to picker
pre-selections -- `match_tag_ids_by_name` across all three name columns,
`match_venue_tag_id` for the leg venue -- and a name that matches nothing renders
as a visible hint rather than being silently dropped; the venue quick-create
dialog even prefills from the draft's per-leg venue/city/address hints via
`data-hint-*` attributes. `import_commit` stays the ONLY write path: the seam is
a second PRODUCER of the preview, never a second writer. The producer is normally
an agent following `.claude/skills/add-concert/SKILL.md`, whose
`references/example-draft.yaml` is pinned to the parser by
`test_skill_example_draft_parses_clean`, so the skill's example and the code
cannot drift apart. The import form's paste-card strings shipped in ja as the
下書き-family wording for catalogue consistency.

Not a Proposed entry here -- it came out of the trilingual arc's typing cost
rather than the wishlist -- but logged in full, and its revision pass ADDED two
entries: Eventernote actor-page discovery (#2, cheap now that the skill and the
draft seam exist) and in-app LLM extraction behind the same seam (#9, deferred on
budget, since the seam is producer-agnostic on purpose). It changed no existing
entry's substance -- #1 (minute-level offsets) in particular was re-reviewed and
is untouched by it.

### Drop the legacy free-text venue columns (venue-to-tags phase 5) (2026-07-22)

Shipped as: the second and final deploy of the two-deploy venue-to-tags
migration, dropping `Concert.venue`/`venue_en`/`venue_zh` and
`ConcertDay.city`/`venue`/`venue_address` (migration `ce43bfcfcae3`) now that
every venue lives on a VENUE tag and nothing reads the free text. Phase 1 kept
these columns as legacy read-only data so a leg the backfill could not match
stayed recoverable and the first save of an existing concert could not null
them; with the backfill long settled and the import-preview picker (below)
closing the last surface that still produced venueless concerts, they were safe
to remove. Completes the venue-to-tags move end to end.

This also retired old Proposed #13 (`discover.html`'s venue guard testing the
raw Japanese column while the body rendered the locale variant) for free rather
than by a direct fix -- the buggy `{% elif cv or c.venue %}` branch read a
column that no longer exists, so dropping the column deleted the branch and the
inconsistency with it. Logged that way deliberately: the entry predicted phase 5
would delete it, and it did.

### All-three-languages-or-none variant enforcement (i18n phase 4) (2026-07-22)

Shipped as: one pure rule -- `domain/translations.py:missing_variants` -- that a
translatable field is filled in all three languages or none, enforced where it
can be and surfaced where it can't. At the create boundaries it is a 422, paired
with a browser-side block that paints an inline error next to the offending
field so nothing the editor typed is lost to a reload. Edit is deliberately
NEVER blocked -- an existing concert can predate the rule and forcing it whole
on the next unrelated save would be hostile -- so the edit page instead names
what is still missing as a notice. The Tags page grew an untranslated count so
the backlog is visible rather than silent. No migration: the rule reads the
columns already there.

### Round-label phrase library (i18n phase 3) (2026-07-22)

Shipped as: a self-populating suggestion set for round labels. Labels stay free
text -- the design decision behind this, carried over from old Proposed #4,
is that real labels do NOT decompose into a taxonomy: of nine labels taken from
Liella! campaigns (`「Liella! CLUB 2025」最速先行`, `いち早プレリザーブ`,
`オフィシャル2次抽選`, `ファミリーマート先行` among them) an
ordinal-plus-kind enum decomposes zero, because the missing axis is CHANNEL
(which fan club, which reservation service, which convenience-store chain sold
it) and channels are proper nouns, an unbounded list that goes stale the moment
a new retailer appears. So instead of composing labels from parts, every
trilingual triple an editor types once (a `RoundLabelPhrase` row, migration
`14bc590fdb44`) becomes a one-click suggestion in a `<dialog>` picker on every
later round, with per-row forget for a mistyped phrase. The second and third
concert reusing a phrase cost one click and no retyping, and nobody has to have
predicted the phrase in advance. This IS what old Proposed #4 asked for; closes
it. (Old Proposed #5, franchise-aware ranking of these suggestions, is the
natural next extension and remains Proposed, now buildable on this.)

### Trilingual leg and round labels (i18n phase 2) (2026-07-22)

Shipped as: leg and round labels that render in the viewer's language, the layer
the earlier i18n build (UGC titles/notes, tag/venue names) did not yet reach.
`ConcertDay.label_en`/`label_zh` and `Round.label_zh` were added (migration
`a589d82c11b4`), and `Round.label_en` CHANGED MEANING: it predated the i18n
layer and used to render to every viewer as an English gloss beside the Japanese
label, and it became a true locale variant selected by `loc_field`. The subtle
part was the ~10 sites in `db/service.py` that copy a label string into a
dataclass before it ever reaches a template -- the field resolves at the copy
site, not at render time, so each had to resolve the viewer's (or recipient's)
locale right there rather than trusting the template. Discord DM tag lines were
localized to the recipient's `user.language` in the same pass. This is the
prerequisite the phrase library (phase 3) and the enforcement rule (phase 4)
both build on.

### Import preview per-leg venue picker (venue-to-tags phase 1 follow-up) (2026-07-22)

Shipped as: the per-leg VENUE-tag select the editor already had, added to the
ramen.events import preview (`import_preview.html`), with auto-match of the
scraped venue name to an existing VENUE tag and the same inline
`_venue_create_dialog.html` for a miss. This closed old Proposed #1: phase 1 had
moved venues onto the leg and removed the concert-level venue field, but import
preview never got the picker, so every import committed a concert with zero
structured VENUE tags -- invisible to Home, the board, Discover tiles and
Discover's region filter. With the picker in place, `import_commit`'s existing
`sync_concert_venue_tags` call does the rest, and phase 5 (dropping the free-text
columns) could land without stranding imported venues. Ranked #1 while open
because it degraded data on every future import; done now, so minute-level
reminder offsets returns to the top of Proposed.

### Leg venues become VENUE tags (venue-to-tags phase 1) (2026-07-22)

Shipped as: the venue moved from free text on the concert AND on each leg into
one structured place -- `ConcertDay.venue_tag_id`, an FK to a VENUE tag (ON
DELETE SET NULL, because a venue tag is shared taxonomy and deleting one must
never take performances down with it). This replaced a case-insensitive NAME
match resolved at render time, which had a real failure mode: re-point a leg at
a different venue and it kept rendering the previous one forever. The old
`find_venue_tag` helper is gone. The tag gained `city`/`city_en`/`city_zh` and
`address`, on the reasoning that a venue is always in ONE city, so the city
belongs to the venue rather than being retyped on every leg that visits it;
`address` deliberately has no locale variants, since its job is to be pasted
into a map and `location_url` already covers the maps link.

The concert level became DERIVED: `sync_concert_venue_tags` rewrites a
concert's VENUE tags as the union of its legs' and is called from the create
route, the edit route and `import_commit`, so the two levels can never
contradict each other. It returns the tags it newly attached and every caller
feeds those to `handle_newly_tagged` -- VENUE tags are subscribable, so a
follower is owed the same notice a concert-level attach always gave them
(invariant 4). Discover's region filter needed no change at all: it reads
`concert_tags` off each tile's `data-tags` client-side, and the rollup is what
keeps that current. The concert-level venue picker was removed from both forms
(`create_concert_row` now sets `venue=None`), and `POST /tags/venue/quick` plus
`_venue_create_dialog.html` let an editor create a venue without leaving the
form -- 409 on a duplicate name specifically, so the dialog can say so, 422 on
everything else.

Two things about it are load-bearing and easy to undo by accident.
`ConcertDay.venue_tag` is `lazy="raise"`, because a lazy load during async
template rendering is a `MissingGreenlet` 500 this project has shipped once;
every path handing legs to a template eager-loads it. And this is deliberately
a TWO-DEPLOY migration: `ConcertDay.city`/`venue`/`venue_address` and
`Concert.venue`/`venue_en`/`venue_zh` still exist so a leg the backfill could
not match stays recoverable, `apply_day_fields` assigns the day columns
preserve-on-empty so the first save of an existing concert cannot null them,
and a later phase drops them on purpose. Migration `789bbcc95bc3` did the
backfill by name and prints every unmatched leg; its SQL passes an explicit
trim set including U+3000 because SQLite's `trim()` strips only U+0020 while
the Python that wrote the data used a Unicode-aware `str.strip()`.

Not a Proposed entry here -- it came out of the trilingual-concert-page design
rather than the wishlist -- but logged in full, and its revision pass ADDED
four entries above: #1 (the missing import-preview picker, which this build
caused), #13 (a venue guard bug it surfaced on Discover), and #4/#5 (round
labels and the phrase library, from the same design discussion). It also
reinforced nothing in the cache-bust entry for once -- eleven templates changed
and `style.css` did not.

### Mobile parity retrofit (2026-07-21)

Shipped as: a phone layer over the existing desktop design, built from a
dedicated spec, plan and two concept demos (`dekimasen-mobile-demo.html`
static frames, `dekimasen-mobile-live.html` interactions) across PRs #61
and #63 plus follow-up overflow fixes. Structurally the whole retrofit
lives in ONE `@media (max-width: 700px)` section at the end of
`style.css`, so desktop pixels are untouched by construction -- nothing
outside that block may change, with one documented exception
(`.fsheet` switches at 760px to track `.layout`'s own collapse point,
since splitting them would open a 701-760px band where the layout has
stacked but the sheet still thinks it is in two columns). Narrow phones
(<=380px) get a NESTED query inside it rather than a second top-level
one. Three patterns recur: a fixed bottom `.tabbar` replacing the header
nav (same `aria-current`/`nav_page` as desktop), an editor-only `.fab`
replacing the header "+ Add", and every `<dialog>` becoming a bottom
sheet (`max-height: 78dvh`, `14px 14px 0 0` corners -- the one deliberate
deviation from the 3px-radius guard). Home's board became a swipeable
carousel and its deadline rows became cards; Discover puts content first
with filters in a sheet that still degrades without JS. The tail of the
work was overflow hardening -- the language chip on one row under 370px
(which is why the narrow query drops the wordmark), the preferences
section rail, and a long Discord ID widening the editors row.

Not a Proposed entry here, so nothing moved up from Proposed; logged
retroactively on 2026-07-21 because the single-`@media`-section rule and
the 760px exception are exactly the kind of constraint a later change
breaks by accident. Reinforced entries #2 (row budget now costs scroll)
and #4 (a large CSS-touching deploy) in its revision pass.

### Signed-out redirect home, with return-to-page after login (2026-07-21)

Shipped as: a replacement for the bare `401 Login required` every
`require_user` route served an anonymous visitor -- a dead end in a browser,
since there is no auth challenge this app can answer, and the exact response
someone got for following a shared or bookmarked link to any concert page.
`require_user` now raises `LoginRequired` (deliberately NOT an
`HTTPException`, so the decision lives in one handler in `web/app.py` rather
than FastAPI's JSON error path), and the handler 303s to `/`, which signed
out is already a real landing page with the sign-in CTA. 303 and not 307 so
a signed-out POST is not replayed against `/`; htmx requests instead get
`HX-Redirect` + 204, because an XHR would follow a 303 and swap the whole
landing page into whatever fragment target it carried. Being signed in but
unauthorized stays 403 -- only anonymous is a wrong turn.

The redirect carries `?next=<path>` so login returns the visitor where they
were headed, which also fixed the silent-bounce problem the first pass
introduced (Home shows a "Sign in to continue" note when it sees one, so the
click no longer just looks broken). `next` rides to Discord in our own
signed session cookie alongside `oauth_state` -- never as an OAuth query
param, so it cannot return attacker-controlled -- and passes
`domain/urls.py:safe_next` on both legs; that guard folds backslashes,
since browsers send `/\evil.com` as scheme-relative `//evil.com` and a naive
`startswith("/")` waves an open redirect straight through. Three carve-outs
are load-bearing: only GETs get a `next` (a POST body is gone, so replaying
its URL renders a form that looks like it submitted and didn't), htmx reads
`HX-Current-URL` path-only (the fragment endpoint is not somewhere you can
stand, and a forged origin steers nothing), and a brand-new account still
goes to `/welcome` regardless. Templates link sign-in through a
`login_url(request)` global rather than a bare `/auth/login`, with a test
asserting no bare href survives -- one missed CTA would silently drop the
destination the others keep. 26 new tests (15 of them hostile inputs to
`safe_next`); the ~15 existing `== 401` assertions became `== 303`.

Not a Proposed entry here -- raised directly by the owner on 2026-07-21 and
built the same day -- but logged in full because the invariant it adds
(signed-out redirects, unauthorized 403s, and the three `next` carve-outs)
is the kind that gets flattened by a later well-meaning refactor.

### Multi-language support (English / Mandarin / Japanese) (2026-07-20)

Shipped as: end-to-end i18n across web and Discord. gettext catalogues
(`messages.po`, ~704 msgids each) for ja and zh, compiled to `.mo` in memory
at startup -- no `.mo` on disk, no deploy-ritual change -- with `en` mapped
to `NullTranslations` so English stays byte-identical to the pre-i18n app
(existing render tests needed no changes beyond two legitimately-retargeted
ones). Locale resolution reuses the shape of the existing timezone
preference: a `lang` cookie caches `users.language` (never the source of
truth), `Accept-Language` covers first-ever visits, a public `POST
/language` is the single write path (cookie always, DB column when signed
in, since Discord DMs read the column not the cookie), and the OAuth
callback seeds the column from the cookie at account creation only. A 🌐
header switcher, a Preferences "Time & language" row, and a welcome-wizard
language step are the three surfaces that set it. All ~28 templates,
service-layer prose, and the Discord side (embeds, views, cogs, plus
per-recipient locale threaded through `DueReminder.user_language` /
`NoticeContext.user_language` / `LegCancelledContext.user_language`) are
gettext-wrapped; date formatting grew hand-built locale-aware ja/zh patterns
in `domain/timezones.py` layered on top of the existing dual JST/local
rendering (invariant 1 unchanged). UGC gained parallel translation columns
(`Concert.title_zh/notes_en/notes_zh/venue_en/venue_zh`, `Tag.name_en/
name_zh`, alongside the pre-existing `title_en`), a display-vs-identity
rule (read surfaces localize via `loc_field`/`loc`; search/edit/match stay
canonical), and an editor "Translations" fold -- two migrations, no
backfill. The legal pages got a full translation plus a non-EN-only
"English version governs" note. A hygiene test
(`tests/test_i18n_catalogues.py`) extracts every msgid in-process and fails
CI if either catalogue has an untranslated or fuzzy entry, whitelist
intentionally empty. The ja/zh strings themselves are a competent-bilingual,
machine-assisted translation pass, not a native-speaker-reviewed one --
flagged here for the owner to spot-check before treating any string as
final. Closes the "Multi-language support" entry the onboarding build
deliberately scoped out and logged on 2026-07-20.

### Onboarding and untouched-pages build (2026-07-20)

Shipped as: the last user-facing surfaces the reconciliation never covered,
built from the `dekimasen-onboarding-demo.html` concept, on the shipped design
system. The signed-out home became a real landing page (hero, value prop, a
"how it works", an illustrative campaign board, a live Discover taste with tag
chips, and a Sign-in CTA) while the signed-in board stayed unchanged. The
five-step `/welcome` wizard was rebuilt on the card/chip vocabulary and now
flows seamlessly into `/setup`: its default-reminders step is the settled
cards-plus-sentence-fine-tune design over all five anchors (Opens/Closes/
Results/Payment/Show; default reminds once for Opens/Results/Payment, Closes
gets a couple, nothing on Show), materialising a real `ReminderPreset` through a
single new `create_preset_from_rules` service helper (no second write path), and
the timezone step gained the browser-detection select reusing the existing
`tz_auto` routes. Import preview was rebuilt in the day-card/round-card/leg-chip
vocabulary with a Kind selector and an editable Details/links fold, and
`import_commit` now binds a round to several legs via the same `round_legs`/
`day_key`/`parse_round_legs` path as `create_concert` -- a multi-leg round at
import, which the old flat form could not express at all. Import form,
retroactive-apply, and the privacy/terms pages were reframed in the design
system (routes, SSRF guard, and legal wording unchanged). Review caught and
fixed an empty-default-preset bug (a wizard submit with no rules would have
created a default that never reminds -- now rejected 422 server-side and blocked
client-side). Real multi-language i18n was deliberately scoped out and logged as
its own Proposed entry. No schema change.

### Add-concert page refactor, incl. multi-leg rounds in one pass (2026-07-20)

Shipped as: `concert_new.html` rebuilt on the editor's card/fold/chip
language (identity-first - title, event id and the first performance open at
the top, details and tags folded, since creation must fill the required
spine before anything else), and - the correctness half - the create form's
round-to-leg binding migrated off the single `round_leg` `<select>` +
`resolve_round_leg` text-matcher onto the editor's leg chips. On the create
form every leg is id-less until save, exactly the case the chips' `day_key`
scheme was built for, so `create_concert` now mirrors `edit_concert`'s
post-flush `key_to_day_id` / `parse_round_legs` (and `parse_round_qualifiers`
for upgrade rounds) resolution, and a genuinely multi-leg round is
expressible in one pass instead of having to be created then edited. This is
the former "Let the creation form express a multi-leg round in one pass"
proposal, shipped - and it closed the same data-loss shape on the create
door that the editor redesign fixed on the edit door (the old select could
only ever store one leg). Retired the now-dead `resolve_round_leg` and
`_leg_picker_script.html` (create was their last caller). Invariants 1/3/6/7
intact; no schema change.

### Demo reconciliation: theming and view parity (2026-07-20)

Shipped as: the full design-token layer ported into `style.css` (the
`*-wash` set, `--raise`, `--chip`, `--shadow`, ...) plus dark mode via both
`prefers-color-scheme` (OS default) and a persisted `data-theme` header
toggle stamped before first paint (no flash); a two-line dual-time render
(`fmt_dual_lines`/the `dual_lines` Jinja global) replacing the flat
one-line web format, with `fmt_day_month`/`day_month` kept separate for
performance dates; and the per-view component gaps the six-branch UI/UX
refactor had dropped against the frozen concept demo -- Home's peek grid,
foot-note and board accents, the concert page's follow toggle and centered
performer chips, Discover's chip counts and tile minichips, Tags' dialog
shadow and button alignment, Setup's reveal-stat overflow fix, and a
rebuilt Preferences on the demo's vocabulary. Also closed the one gap that
was backend, not presentation: `POST /me/delete`, the self-serve GDPR
erasure the demo showed but shipped as a manual-request placeholder, now
wired to the existing `service.delete_user` behind a heavy confirmation.
No behavior changed outside the delete route and the theme toggle itself;
this was reconciliation against the demo, not new feature work.

### First-run capture flow (2026-07-19)

Shipped as: a three-screen `/setup` flow run AFTER the `/welcome` wizard —
`GET /setup` prunes the concerts a user's tag subscriptions imply (tiles lit
by default; switching one off writes a branch-4 `OPTED_OUT` override, and
re-checking a pruned one CLEARS the override back to the tag default rather
than writing an explicit subscribe), `GET /setup/applications` asks which
still-live rounds — open now, or closed and awaiting a result — the user
already applied to and records `APPLIED` through `record_round_outcome` only
(no second write path), and `GET /setup/ready` reveals the board tallies
(tracking / applied / payment due / next deadline). No new step state: every
screen renders current DB truth, which makes the flow tamper-safe (nothing
reads a step from user input) and re-runnable (Preferences' "Run first-time
setup again" points at `GET /setup`). `POST /welcome/advance` now redirects
into `/setup` when it crosses into done; `skip-all` still lands on `/`.
Backed by `db/service.py`'s `setup_prune_tiles` / `setup_application_rows` /
`setup_tallies` / `apply_prune_selection` / `record_setup_applications` and
the `_round_asks_application` predicate (carrying the documented branch-5
upgrade-round hook). Consumes branch 4's `ConcertSubscription` override model
and adds no schema of its own. Closes the six-branch UI/UX refactor.

### Upgrade rounds and their qualifying-round set (2026-07-19)

Shipped as: a new `RoundKind.UPGRADE` (label "Upgrade round", emoji) for the
Japanese upgrade round - a nested second campaign only holders of a
qualifying round's ticket may enter - modelled end to end. A named
association table `round_qualifiers(upgrade_round_id, qualifying_round_id)`
with both FKs ON DELETE CASCADE (modelled on `TagMember`) records which
rounds qualify; no qualifiers means "any secured (WON/PAID) ticket on this
concert qualifies", mirroring `applies_to`'s empty-means-all. A pure
`domain/upgrades.py:is_eligible` derives per-user eligibility from recorded
`RoundOutcome`s - never stored - and is threaded through the three existing
per-user seams, not the pure planner: `_apply_outcome_suppression` (the
upgrade is exempt from the secured-elsewhere suppression a base ticket would
trigger, then re-suppressed for ineligible users), `_next_round_for_leg` /
`_auto_arm_next_round` (an upgrade neither arms on a base loss nor arms
anything when itself lost), and `column_for` (a won upgrade's payment
outranks a secured base). Discover shows the campaign as its own accent pill
beside the base standing; Home's Coming up gates the upgrade row and its
`Entered upgrade` / `Skipping` capture on eligibility; the concert page shows
an ineligible viewer a `Requires a ticket from: ...` line instead of capture
buttons; the global anonymous deadline list is unchanged (the round's
existence is public). The editor gained a "Qualifies" toggle-chip row inside
an `.upgradebox`, shown only for an UPGRADE round, encoded exactly like the
branch-2 `round_legs` chips (one hidden `round_qualifiers` field per round
row, `parse_round_qualifiers` filtering to surviving round ids and dropping
self). Chips reference only already-saved rounds; a round created in the same
submit cannot be a qualifier until saved once, stated in the UI copy. No new
`RoundOutcome` write path (invariant 2 intact); one nullable-free migration
(the association table), no backfill.

### Tags page redesign, second pass (2026-07-19)

Shipped as: the Tags page rebuilt around counted chips (dashed when unused),
franchise->group->member families, region-bucketed venues with a "No region"
bucket, and a performers-with-no-group section; a per-tag edit dialog with a
usage strip (concerts/followers/members/upcoming) and a per-member
"apply to N upcoming concerts" action reusing the shipped retroactive-apply
route (invariant 3 intact); a kind-conditional new-tag dialog that warns
instead of blocking on a same-name/different-kind tag (`create_tag` drops its
blanket 409 for a kind-scoped one). Adds `Tag.eventernote_url` (mirroring
venue `location_url`, through `form_url`) and wires it onto the concert
page's performer chips, closing the link the branch-2 concert page deferred.
Follow-up noted: the tag directory's per-member eligible-concerts count is an
N+1, immaterial at current scale.

### Concert subscriptions and per-leg opt-out (2026-07-19)

Shipped as: `ConcertSubscription` (state `subscribed`/`opted_out`) and
`LegOptOut`, both OVERRIDE tables -- no row means follow the tag-derived
default, so no backfill (invariant 8). `tracked_concert_ids` now applies
the override; per-leg opt-out folds into `_apply_outcome_suppression`;
writes re-sync via `reinstate_user_rules`. The concert page's Following
toggle and per-leg opt-out, Home's real "skip this concert", and a
rebuilt Preferences (left rail, a Following section with the pruned count
and restore) all became real instead of placeholders. Prunes stick across
re-follow; opting out of a won ticket needs a heavy confirmation and never
deletes the outcome.

### Concert page and editor redesign (2026-07-19)

Shipped as: two surfaces rebuilt around the same fact, that a round belongs
to a set of LEGS. The concert page (`templates/concert_detail.html` +
`_round_rows.html`) now leads with lineage and performers rather than a
metadata header, and replaces the single rounds table with one round-row
group per leg plus an all-legs group, grouped by `service.concert_round_rows`
(cancelled legs included, a round covering every live leg deliberately
landing in the all-legs group rather than being repeated under each). Rounds
on that page carry capture buttons via the `_capture_actions.html` macro
shared with Home's rows, so `POST /rounds/{id}/outcome` now answers two
surfaces - it reads which one from `HX-Current-URL` and sends the concert
page its own rounds region instead of Home's three fragments.

The editor (`templates/concert_edit.html`) puts rounds first, folds the rest,
turns cancelled into a per-leg toggle, and moves duplicate/delete into a
danger row that states what duplicate copies (invariant 3: the pruned tag
set, `expand=False`, no rounds or legs).

The data-loss fix underneath: the editor used to express `applies_to` as one
free-text string per round, matched server-side against each day's city or
label, and pre-filled that box from `applies_to[0]` alone - so opening a
two-leg round and saving it untouched silently narrowed it to one leg. Legs
are now toggle chips submitting real ids, encoded as ONE `round_legs` field
per round row (a flat repeated field could not say which row an id belonged
to). A leg added in the same save has no id yet, so its chip carries the
row's client-generated `day_key`, resolved after the flush.

Branch review on 2026-07-19 then closed a second, larger copy of the same
bug before merge: `round_legs` was end-padded unconditionally, so a browser
still holding the pre-deploy edit page - which posts the old `round_leg`
field and no `round_legs` at all - would have had one Save wipe `applies_to`
on every round of the concert. A whole-array omission now preserves each
round's existing legs; a partial array raises rather than sliding every
later row's selection by one.

### Web-side won/lost for a round with nothing left ahead of it (2026-07-19)

Was proposed on 2026-07-19 (Home/Discover split review) and shipped the same
day by the concert page above, which took the first of the two options it
named: a capture surface on the concert page itself, rather than a grace
window on Home.

The gap was that capture lived only on Home's "Coming up" rows, and a row
exists only while the round has a FUTURE anchor - so a round whose results
had passed with no payment deadline behind it dropped off Home entirely,
stranding anyone sitting on APPLIED with no web-side way to say how it went.
The concert page lists EVERY round of the concert regardless of timing and
runs the same `capture_gates`, so `can_report_result` is true there exactly
when it should be. Nothing was relaxed in `record_round_outcome` to get it
(the gates stay on the read side), and no second write path was added.

### Home and Discover split (2026-07-19)

Shipped as: the old combined index page cut in two by the question each
answers. `/` (`templates/home.html`, handler in `web/app.py`) is Home -
personal, login-gated, four blocks: Up next, the four-column campaign board
(`domain/board.py`'s `column_for` plus `service.board_cards`), Coming up
(`service.my_deadline_rows`, the page's only capture surface), and a teaser
out to Discover. `/discover` (`web/routes/discover.py` +
`templates/discover.html`) is the catalogue - and is public, the only
content page an anonymous visitor can reach - with a round-status facet, a
next-deadline sort, and one merged status pill per card
(`service.discover_statuses`). Capture actions post to
`web/routes/outcomes.py`, which shares `record_round_outcome` with the DM
buttons and swaps three fragments back (the rows, plus the board and its
tally out-of-band).

Branch review on 2026-07-19 then fixed six things before merge: the top
block claimed "Closes next" while showing an open (now "Up next", honest
about which moment it names); every future anchor of one round offered its
own capture buttons, so a user could record APPLIED against a round that
had not opened (now gated on `can_capture`); WON and LOST existed only as
Discord buttons, leaving a `dm_blocked` user or a `bot_enabled=False`
deploy with a four-column board the web could only drive two columns of
(now offered on an APPLIED row once its result moment lands); the teaser
counted every Concert row rather than what `/discover` lists;
`tracked_concert_ids` ran twice per render; and the docs were behind.

### Public terms of service page (2026-07-19)

Shipped as: `GET /terms`, the counterpart to `/privacy` — same shape
(`web/routes/terms.py` + `templates/terms.html`, `current_user` not
`require_user` so Discord's reviewers can read it signed out), filling the
Terms of Service URL field the Developer Portal offers alongside the
Privacy Policy URL. The clause that actually matters is the disclaimer of
warranty: the service tracks real ticketing deadlines and cannot promise a
DM arrives, arrives on time, or that the date is right, so it says so
plainly and puts confirmation with the official source on the user. Also
covers non-affiliation (the app is entirely about other people's events),
Discord's own minimum age rather than a number, acceptable use, editor
contributions staying in the shared catalogue after erasure (matching the
privacy policy and `delete_user`), availability/shutdown, and New Brunswick
governing law. Contacts reuse the existing `PRIVACY_CONTACT_*` settings via
a new shared `web/contact.py`; the two pages cross-link.

### Public privacy policy page (2026-07-19)

Shipped as: `GET /privacy`, a public (deliberately no `require_user`)
long-form policy page in `web/routes/privacy.py` + `templates/privacy.html`,
linked from the `base.html` footer. Required by Discord's Developer Terms
for any app collecting user data, and mandatory at bot verification /
100+ guilds; also covers the GDPR transparency obligation. Content is
written against the actual schema rather than boilerplate, and documents
the erasure semantics the `1384cadd692e` migration enables (personal data
deleted, contributed catalogue kept with the author anonymised). The
operator's contact handle and email come from two new blank-by-default
settings (`PRIVACY_CONTACT_DISCORD` / `PRIVACY_CONTACT_EMAIL`) so real
contact details never enter the repo; the page renders with both, one, or
neither configured.

### First-run guided setup (2026-07-18)

Shipped as: a `/welcome` wizard route that sequences new logins through
tag subscriptions (follow artists), default reminder preset selection,
timezone confirmation, a test DM to verify delivery, and calendar feed
subscription. Backed by an `onboarding_step` column on `User` to track
progress, a new-user redirect in the OAuth callback, and a `next`
redirect parameter added to the five existing routes the wizard reuses
(concerts, presets, tags, preferences, calendar) to stitch them together
into one seamless flow.

### Corrected and expanded round-kind taxonomy (2026-07-18)

Shipped as: two new `RoundKind` enum values, `FCFS_SALE` and `TOUR_PACKAGE`,
splitting out first-come-first-served sales from the previously-conflated
general-sale kind and adding support for overseas tour package sales. A
centralized `LABEL_BY_ROUND_KIND` dict in `db/service.py` now provides
display labels for all nine kinds (replacing six inline label derivations
scattered across templates), with corrected emoji assignments (removing
the racing emoji misapplied to `GENERAL_SALE`, adding the appropriate
emoji for the two new kinds). Also updated `ingest.py`'s ramen.events
keyword table to recognize FCFS and tour-package keywords in HTML parsing.

### Per-round personal lottery outcome tracking (applied / won / lost / paid) (2026-07-18)

Shipped as: state-aware DM buttons (Applied/Not applied, Won/Lost, Paid)
on the reminder embed, backed by a new `RoundOutcome` table and
`record_round_outcome`/`_apply_outcome_suppression` in `db/service.py` —
losing suppresses that round's payment reminder and arms the next
round's opens reminder; winning skips the general-sale ping. Also
generalized `snooze_reminder` to accept a `days` parameter and added a
"Remind me later" modal-driven button (replacing the plain 24h snooze)
on the CLOSES-anchor reminder specifically, plus relabeled that anchor's
link button to "Apply here".

### Free-text search matches artists, groups, and venues (2026-07-18)

Shipped as: a centralized `concert_search_text` helper (`web/app.py`) that
is now the single source of truth for what search matches — title,
title_en, every attached tag's name across all four kinds
(franchise/group/artist/venue), and a free-text `Concert.venue` fallback
only when no VENUE tag is attached. Used at all three call sites: the
server-side `matches_query` fallback, the tile grid's `data-search`
attribute, and the "Coming up soon" deadline list's `data-search`
attribute (previously title-only).

### Surface undeliverable DMs to the user (2026-07-18)

Shipped as: a sitewide `dm_blocked` banner (driven by `SessionUser` /
`users.dm_blocked_since`, set by the scheduler's delivery-outcome
tracking) plus a synchronous `POST /me/test-dm` diagnostic route and
"Send test DM" button on the preferences page.

## Rejected

### Catalogue free public appearances (rejected 2026-08-02)

餅まき at a department store, a 1日駅長就任式 at JR山口駅, アニソン盆踊り at
神田明神, a トークショー at 松山競輪場. The 2026-08-01 taxonomy read found a
steady trickle of these among the 443 leads.

Rejected as a permanent class rather than deferred to #8, and the distinction is
the point: the others are things we decided not to do yet, while this one has
nothing for the app to say. **There is no ticket, so there is no deadline, so
there is no reminder.** A concert row for one would carry no round, arm no rule,
and announce nothing -- the same shape as a concert whose every leg is cancelled,
which the app already goes out of its way to keep silent.

`DismissReason.FREE` exists so waving one off is one click and stays counted.
That is the whole support this class should ever have.

### Per-rung cancelled marking on a dead board card (rejected 2026-07-28)

Was: Proposed #6, filed 2026-07-27 by the dead-concerts branch's final
review as an owner eyeball rather than as work. A dead concert keeps its
board card when the reader has standing on it (that branch's own ruling),
badged Cancelled, with its rungs built from the concert's FULL round set --
so every rung on such a card is a round that is not happening, while the
only thing saying so is one badge in the header. On a multi-rung card that
badge reads like "one leg was cancelled", which is what it means everywhere
else in the app. The alternatives were a per-rung marker (closest to the
concert page and `ShowDeadlinesButton`, which both label every round),
dimming the ladder as a whole, or leaving it.

Rejected by the owner (2026-07-28) in favour of leaving it exactly as
shipped: **a board card is a scanning surface, one badge per card is what a
badge is for, and the concert page one click away labels every round.** The
entry was explicitly a question of which reading he wanted rather than of
how to build it -- cheap either way -- so the answer closes it rather than
deferring it. Filed here rather than deleted because the ambiguity it
describes is real and someone will notice it again; the record is that it
was noticed, put to the owner, and accepted.

### Daily digest mode (rejected 2026-07-18)

Was: an opt-in "one morning DM listing everything due" to reduce
per-deadline ping fatigue for multi-subscription users. Rejected by the
owner during design review — not worth the scheduling complexity it
would have needed (a per-user local-morning gate layered onto
`due_reminders()`/`tick()`) for a noise problem the owner doesn't
consider significant enough to solve right now.
