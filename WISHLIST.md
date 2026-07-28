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
stand unchanged -- #3 (opt-out snapping the folds shut) is a sibling defect on
the same page but purely an htmx fold-state problem, and #5 (minute-level
offsets) is if anything slightly less pressing, since a dead concert now plans
no reminders at any offset at all. The sign-in-bounce entry's demo-parity/
Discover-head pointer was bumped in place once more.

## Proposed (highest impact first)


### 1. Admin-only catalogue export (never any user data)

Impact: medium - effort: small-medium. Raised: 2026-07-26 (owner).

One download of the shared catalogue -- concerts, days, rounds,
qualifiers, tags, memberships -- with user-specific data excluded by
construction: build it from the catalogue tables only (never JOIN a user
table) and strip `created_by`, so there is nothing to leak rather than a
filter to get wrong. Natural shape: reuse the draft YAML vocabulary --
one `yaml_export` draft per concert, which already round-trips through
`POST /concerts/import/draft`, so the export doubles as a rebuild path --
plus a tags file for what drafts don't carry (kind, parent, members,
region/city/address, urls), zipped under `GET /admin/export.zip` behind
`require_admin`. Streaming a zip at request time has precedent
(`/concerts/import/skill.zip`). Decide with the owner whether
re-importability matters or a read-only JSON dump is enough -- the YAML
shape costs a little more and pays only if it does.

### 2. event_id slugs should prefer title_en

Impact: low-medium - effort: small. Raised: 2026-07-23 (assistant, while
verifying the import path).

`generate_event_id` slugifies the Japanese title, and `slugify` strips
everything outside `[a-z0-9]` -- so a Japanese-only title collapses to the
`"concert"` fallback and imports mint ids like `concert-2`, `concert-3`:
unique, but meaningless in URLs that are supposed to be the human-readable
identity (invariant 6). Since the trilingual rule made `title_en` mandatory
at every create boundary, the fix is one line of preference -- slug from
`title_en` when present, fall back to `title` -- plus tests. Existing
concerts keep their ids (event_id is editor-owned after creation; no
backfill).

### 3. Opting a leg out snaps the concert page's round folds shut

Impact: low - effort: small-medium. Raised: 2026-07-27 (final review of the
ladder-declutter branch).

`POST /concerts/{event_id}/legs/{day_id}/opt-out` (`routes/subscriptions.py`)
re-renders `_round_rows.html` as a whole-region outerHTML swap, and passes no
fold state -- so a reader who had expanded a leg's "+N more rounds" history to
check what they missed, then toggled a leg off, watches every fold on the page
snap shut and loses their place.

`open_round_id` is the WRONG instrument here, and reaching for it is the trap
this entry exists to flag: it reopens the fold that OWNS a specific round, and
an opt-out writes no round -- there is nothing to reopen around. The honest fix
is general expanded-state preservation across an htmx swap of this region (the
open `<details>` collected client-side before the request and restored after,
or the fold state hoisted somewhere a re-render can read), which would also
cover the outcome routes' folds without their per-round special case. Sized
small-medium for that reason: the mechanism is the work, not the caller.

### 4. Agent-import review-debt batch (deferred minors)

Impact: low (code health) - effort: small. Raised: 2026-07-23 (final
whole-branch review of the agent-import build; the first three triaged
defer-with-reason there).

One tidy pass over the review leftovers: (a) `yaml_import.py`'s DraftError
message uses `{exc or 'nesting too deep'}` -- exceptions are always truthy,
the fallback is dead code; (b) `_text`'s container guard blanks silently
where `_dt`'s warns, so a container value for organizer/notes/labels/urls
leaves no drift warning -- warn WITHOUT stringifying the value (the
stringify is what the DoS fix removed); (c) `match_tag_ids_by_name`'s
docstring doesn't state first-tag-wins collision order or that blank names
drop from both output lists; (d) `preferences.html`'s preset-item edit form
writes its action with BACKSLASHES (`action="\presets\{{ p.id }}\items\...`)
where the sibling forms use `/` -- browsers fold `\` to `/` in URL paths so
it works today, but it's a typo waiting to confuse someone (spotted
2026-07-23 while scoping the sentence-builder i18n problem). All
behavior-safe today; batched so they stop being rediscovered by every
future reviewer.

### 5. Minute-level reminder offsets

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

### 6. Eventernote actor-page discovery

Impact: medium - effort: small, now that the skill exists. Raised: 2026-07-22
(during the agent-import design discussion). Buildable as of 2026-07-23, when
the draft seam and the add-concert skill shipped.

A concert nobody has added to the app has NO deadline tracking at all -- the
worst failure the app has, worse than a mistimed reminder, because the user
never learns there was a deadline to miss. The skill (or a scheduled agent) can
close that gap: walk each followed artist's Eventernote `/actors/<id>/events`
page and flag concerts not yet in the catalogue. Cheap now that the pieces
exist -- discovery produces a paste-ready YAML draft through the exact
`POST /concerts/import/draft` path the skill already builds, so the "add it"
half is done; what remains is the walk-and-diff (mapping each followed artist to
its actor id, deduping candidates against existing concerts by title/date).
Ranked directly under the established minute-offset entry: it is the
highest-impact NET-NEW capability the import build unlocked, but it sits below
that one because that need is proven while this is unbuilt and unproven -- the
actor-id mapping is manual today and a scraped page's structure can drift.

### 7. Franchise-aware round-label suggestions

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

### 8. Nine of ten `RoundKind` members are purely cosmetic

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

Ranked here -- below the three user-facing entries above, above the pure-plumbing
ones -- because it is the highest-impact item still standing once the trilingual
arc shipped its user-facing work, but acting on it changes a persisted enum for
zero user-visible benefit, and the taxonomy was corrected as recently as
2026-07-18, so the risk of churning it again outweighs the tidiness. Logged
rather than done, on purpose, so the observation is not rediscovered a third
time.

### 9. Pin the Python version across dev, CI and the server

Impact: low (risk mitigation, not user-visible) - effort: small. Raised:
2026-07-21 (PR #57 CI failure post-mortem).

CI went red on the i18n branch with an `UnboundLocalError` that no local
run could reproduce: Ubuntu 24.04's system Python is CPython 3.12.3, whose
PEP 709 inlined comprehensions leak the iteration variable into the
enclosing scope's symbol table (fixed in later 3.12.x), while the dev
machine runs 3.13.1 where the same code is legal. Nothing pins a version
anywhere - no `.python-version`, no `python-version:` in `ci.yml` - so dev,
CI and the production server (also Ubuntu 24.04, so also 3.12.3-eligible)
can all resolve different interpreters, and `requires-python = ">=3.11"`
makes every one of them fair game. The immediate bug was fixed in code
(`f41b847` renames the throwaway `_` bindings), but the drift remains.

Fix is one file: a `.python-version` (e.g. `3.13`) at the repo root, which
`uv sync` honors everywhere. Deliberately NOT done as part of the CI fix
because it changes the production interpreter on the next deploy (uv would
download 3.13 to Lightsail and rebuild the venv) - that is an operational
call the owner should make consciously, ideally timed with a deploy he can
watch. Until then, any new code that behaves differently across 3.11-3.13
will only be caught if CI's particular interpreter happens to object.

### 10. PWA / installability

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

### 11. In-app LLM extraction behind the same draft seam

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

### 12. Minor demo-parity cosmetics

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

### 13. Discover sort in the content head, plus the catalogue-count note

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

### 14. Name the destination on the sign-in bounce

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

Ranked below the demo-parity batch (#12) and the Discover head (#13) because
those close several visible gaps each; this refines one sentence that is
already correct.

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

The entry named three surfaces. The branch touched nine, and two of the six
were beyond it in ways worth recording. **The planner was pulled in
deliberately at spec time** -- a general round on a dead concert still planned
DMs saying "apply now", which is the worst instance of the lie the entry
describes, and leaving it out would have fixed the screens while the scheduler
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
near. Entries renumbered 1-14; the sign-in-bounce entry's demo-parity/
Discover-head pointer was bumped in place again. Two of the remaining entries
were re-read against what shipped and both stand: #3 (opt-out snapping the
concert page's folds shut) is a sibling defect on the same page but an
htmx-fold-state problem with nothing to do with cancellation, and #5
(minute-level offsets) is untouched, since a dead concert now plans no
reminders at any offset. Worth recording for whoever takes the list next: with
this gone, every remaining entry is assistant-raised or review-raised except
the admin export, and the two defects the ladder-declutter review filed
together are now one shipped and one still open at #3.

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
NOT_APPLIED. The review's other two findings were pre-existing and are filed
in Proposed (#2, #4).

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

### Daily digest mode (rejected 2026-07-18)

Was: an opt-in "one morning DM listing everything due" to reduce
per-deadline ping fatigue for multi-subscription users. Rejected by the
owner during design review — not worth the scheduling complexity it
would have needed (a per-user local-morning gate layered onto
`due_reminders()`/`tick()`) for a noise problem the owner doesn't
consider significant enough to solve right now.
