# WISHLIST.md

Every potential feature raised in roadmap and UX discussions, ordered by
user impact (highest first). Maintained per the "Feature wishlist" section
in CLAUDE.md: fully re-evaluated and re-ranked every time a feature ships.
Each entry notes impact and effort so re-ranking has a basis. Shipped and
rejected ideas move to the bottom sections instead of being deleted.

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

## Proposed (highest impact first)


### 1. The scrape-to-agent workflow: a `triage-leads` skill

Impact: high - effort: small-to-medium. Raised: 2026-07-31 (owner, immediately
after the manual sweep button merged: "then we can start building the actual
scrape -> agent workflow").

Discovery now produces leads and a manual button to produce them on demand. What
it does NOT do is close the loop: a lead becomes a tracked concert only when
somebody finds the official ticket page, extracts the rounds, groups the legs and
writes the trilingual titles. That half is deliberately agent work -- eventernote
carries no ticket information at all, so no amount of scraping can supply it --
and it is currently improvised fresh in each session.

**The durable form is a skill, not a schedule.** `.claude/skills/triage-leads/`,
invoked with the DM's copy block (or the page's) pasted as arguments. A file, so
it survives sessions, gets reviewed in PRs, and sharpens as the real leads teach
what they look like. It should CALL the existing `add-concert` skill for the
draft itself rather than duplicate it -- that one already owns the schema and is
pinned to the parser by a test.

Two things worth settling in its design rather than during it:

- **Grouping is the judgment the app deliberately refuses.** One eventernote
  event is one LEG; a tour is one concert with several. The DM's copy block
  already carries the instruction, but the skill is where the heuristics live
  (title stem, adjacent dates, same venue).
- **The commit stays manual.** `import_commit` is the only write path into
  `concerts` and a lead must never bypass it. The goal is fewer steps, not an
  agent writing to the catalogue unattended.

A second, cheaper shortening is available and needs no skill: after building a
draft, drive the owner's signed-in browser to `/concerts/import`, paste it, and
leave the filled preview on screen. That removes a copy-paste and a tab hunt.

Ranked #1 on merit rather than by removal: it is what makes a feature that
shipped today actually pay off, and without it every lead costs the same manual
research it always did. Note the honest caveat -- its value is in the specifics
of what real leads look like, so it is worth writing AFTER a few production
sweeps rather than before, and the first version should expect revision.

### 2. Minute-level reminder offsets

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

### 4. Nine of ten `RoundKind` members are purely cosmetic

Impact: low (code health, no user-visible change) - effort: medium. Raised:
2026-07-22 (surfaced during i18n phase 2 design and deliberately not acted on).

Exactly one `RoundKind` member carries behaviour: `UPGRADE`, which drives the
eligibility gate, the suppression exemption, the auto-arm guards, the board
column rank and the capture gating (invariant 2). The other nine differ from
each other in a label string and an emoji and nothing else -- `LOTTERY`,
`FCFS_SALE` and `TOUR_PACKAGE` take identical paths through the planner, the
queue and every read surface. That is worth knowing before anyone adds a tenth
kind expecting it to mean something, and it is an argument for collapsing the
cosmetic nine into data (a label/emoji table) with `UPGRADE` kept as the one
real branch.

Ranked here -- below the user-facing entries above, above the pure-plumbing
ones -- because it is the highest-impact item still standing once the trilingual
arc shipped its user-facing work, but acting on it changes a persisted enum for
zero user-visible benefit, and the taxonomy was corrected as recently as
2026-07-18, so the risk of churning it again outweighs the tidiness. Logged
rather than done, on purpose, so the observation is not rediscovered a third
time.

### 5. PWA / installability

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

### 6. In-app LLM extraction behind the same draft seam

Impact: low-medium - effort: medium, BLOCKED on API budget. Raised and
deliberately deferred 2026-07-22 (owner: no budget for per-import API calls).

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

### 7. Minor demo-parity cosmetics

Impact: low - effort: small. Raised: 2026-07-20 (demo-reconciliation
re-review).

Cosmetic gaps the 2026-07-20 reconciliation left unbatched because they are
pure polish, not correctness: Preferences' "Follow another tag" is a
disclosure fold rather than the demo's footer `.bar` + button, and its
second toggle reads "Auto-apply" where the demo says "Auto-apply preset";
the Tags edit dialog lists every member instead of the demo's "+N more"
truncation, and its new-tag dialog footer sits slightly detached (nested in
a grid rather than a sibling of the body); Setup's pick tiles are
keyboard-reachable now but use a visually-hidden checkbox where the demo
uses a real `<button aria-pressed>`, and `.lede h1` lacks `text-wrap:
balance`. The cheapest of the open items - one small pass closes all of it.

Re-reviewed 2026-07-20 (i18n build): every string this entry touches or adds
now needs both catalogues updated (`tests/test_i18n_catalogues.py` fails
otherwise), a small but real addition to "small" effort that didn't exist
when this was raised.

Grew one item on 2026-07-21 (signed-out redirect): the `.signin-note` that
explains a bounce to the landing page is a new component with no counterpart
in `dekimasen-onboarding-demo.html`, whose signed-out Home has no such
state. Per the CLAUDE.md rule that a deliberate move should update the demo
so it stays the reference, the demo owes this frame -- fold it into this
entry's single polish pass rather than treating it as its own task.

Grew a second of exactly the same kind on 2026-07-29 (documentation pass):
the 403/404/422/500 pages shipped on 2026-07-28 as a genuinely new SURFACE --
a full-page state with its own copy per status code -- and no demo has a
frame for any of them. That is a bigger gap than the `.signin-note` above,
because there is no existing frame to amend: whoever next reworks error-page
copy or chrome has no reference to work against and will invent one. Same
resolution though -- one frame per code in `dekimasen-demo.html`, folded into
this entry's single pass, not its own task. Both gaps are now also named in
CLAUDE.md's demo inventory, so the next person meets them where they look for
the reference rather than only here.

### 8. Discover sort in the content head, plus the catalogue-count note

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

### 9. Name the destination on the sign-in bounce

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
five chances to get it wrong for no gain.)

### 10. Nothing caps the discovery review path

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

(The former "`/admin/discoveries` row height wants a real viewport" entry
(2026-07-31) was closed the same day by measuring it -- see its Shipped entry.
The measurement moved the answer: the hint banner was not the cause.)

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
viewport at 1440px instead of reasoning from the CSS.

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
