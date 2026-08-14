# Round poll phase 2: the draft page, and applying a proposal to a live concert

Design, 2026-08-14. Completes the LARGE shape of WISHLIST #2. Phase 1 shipped
as PR #156 (merged 2026-08-13) and is described in
`docs/superpowers/specs/2026-08-13-round-poll-design.md`; that document fixes
phase 2's POLICY, and this one designs its interior.

The small shape of entry #2 — a round-gap dimension on the discovery matcher —
remains out of scope and stays on the list. [Correction, 2026-08-14, added
after this build's final review: shipping the large shape triggered
`WISHLIST.md`'s post-feature revision pass, which moved that small shape onto
its own merits at **#4** and gave old-#2 to a different entry entirely. The
"#2" in this document is the number it carried on the day the design was
written, and is left standing as the dated record it is — read every "#2"
below as today's **#4**.]

## Where phase 1 left it

A daily flag-gated pass re-reads each quiet concert's own official page and
records rounds it appears to have grown as `RoundProposal` rows, reviewable
read-only at `GET /admin/quiet-ladders/proposals`. Nothing can be acted on:
the page has no buttons, and `applied_at` exists but is never written.

## The blocker exploring found

**A proposal cannot reconstruct a round.** The prompt
(`domain/round_completion.py`) asks the model for seven things —
`applies_to`, `apply_opens_jst`, `apply_closes_jst`, `results_jst`,
`payment_deadline_jst`, plus kind and label. `upsert_proposal` persists
**four**: label, kind, opens, closes. `applies_to`, `results_jst` and
`payment_deadline_jst` are parsed, evidence-verified, and then dropped.

Those are not incidental fields. A results announcement and a payment deadline
are two of the anchors this app exists to remind people about, and `applies_to`
decides which legs a round covers at all.

One piece of luck: `evidence_yaml` stores the whole verified evidence dict, so
the QUOTES for those fields already survive — only the parsed values are lost.

## Decisions

**1. Widen the table; fix phase 1's writer.** `round_proposals` gains
`results_at_utc`, `payment_deadline_at_utc` and `applies_to_labels`, and the
poll persists what it already parses and verifies. **One migration, and zero
rows to backfill** — the flag has never been on in production, so the table is
empty. Owner ruling, 2026-08-14.

**2. Fields are editable, pre-filled.** Every field on the draft page is a real
input carrying the model's value, with its quoted source line beside it. You
correct the one timestamp it misread and approve. Without this a round that is
90% right must be dismissed and retyped in the concert editor, which is the
retyping the whole feature exists to remove. Owner ruling, 2026-08-14, and the
reading of "approval for each edit" (2026-08-13) it settles.

**3. A moved date is SURFACED, never applied.** Owner ruling, 2026-08-14. The
diff learns a third answer, but phase 2's write path stays creates-only.
See below — this is the most consequential decision in the document.

## The moved-date case, and why it is not optional

`dedupe_key` is `label | opens` only. So a round the concert ALREADY holds,
whose closing date the page now says has moved, produces a key that matches and
is discarded as "already held".

But **a concert is quiet precisely because its stored deadlines are in the
past.** A postponed closing date is therefore among the most likely true finds
the poll will ever make, and today it is the one thing it throws away.

Phase 2 fixes the discarding, not the applying:

- `domain/round_proposals.py` gains a third verdict. Today it answers
  new-or-held; it will answer **NEW**, **HELD** (identical, drop), or
  **CHANGED** (same key, some other field disagrees). CHANGED proposals are
  stored like any other.
- A CHANGED proposal renders with the stored value beside the proposed one and
  the quote, and **has no Approve button** — only Dismiss, and a link into the
  concert editor. Nothing in phase 2 edits an existing `Round`.

**Change-ness is DERIVED at render time, never stored.** The page asks "does
this concert hold a round with this key, and do the values still differ?"
rather than trusting a flag written days ago. Three consequences, all of them
the reason for the choice:

- a proposal whose round you later fix by hand simply stops being a change and
  resolves itself — which closes the second gap WISHLIST #24 files, where a
  hand-added round leaves its proposal pending forever;
- a proposal cannot go stale against a round that moved again;
- there is no verdict column to drift out of sync with the rounds it describes.

## The apply path

**Approving writes through the existing round-creation seam, never by
constructing a `Round` directly.** `web/routes/concerts.py` already has the
shared constructor — `build_round(...)`, whose own docstring names its callers
("the rich creation form, the edit page's new rows, and the URL-import commit
route") and which delegates to `apply_round_fields(round_, label, kind,
opens_at, closes_at, results_at, payment_at, url, applies_to, label_en, notes,
label_zh)`. Phase 2's apply becomes its **fourth** caller.

Read both before writing the route: `apply_round_fields` takes its timestamps
as STRINGS and does its own JST parsing, so the draft page should hand it form
values rather than pre-parsed datetimes, exactly as the editor does.

**`sync_concert(session, concert.id)` must follow.** Invariant 2:
`reminder_queue` is a materialized outbox and any edit to rounds must call the
relevant `sync_*`. A `Round` row inserted without it is a deadline nobody is
reminded about — the precise failure this feature exists to prevent,
reintroduced by the thing meant to fix it. The two existing call sites in
`concerts.py` are the precedent.

**Legs.** `Round.applies_to` is a JSON list of `ConcertDay` ids, and empty
means ALL — the same empty-means-all convention `round_qualifiers` mirrors. The
page renders one checkbox per leg, pre-ticked from the model's
`applies_to_labels` where they match a real leg label, and a label that matches
nothing is shown as unmatched rather than silently dropped.

Two rules keep the convention intact, and both must be explicit because
"everything ticked" and "nothing recorded" are the same behaviour but not the
same row:

- a proposal whose `applies_to_labels` is empty, or whose labels match no leg,
  renders with **every box ticked** — that is what empty already means, and
  showing it as "no legs" would read as a round that applies to nothing;
- on submit, **every box ticked normalises back to empty**, so the stored round
  keeps the convention rather than freezing today's leg list into an explicit
  array that a later added leg would silently fall outside of.

The editor already parses its own leg chips through `parse_round_legs(value,
valid_day_ids, key_to_day_id)`; reuse it rather than writing a second parser,
for the same reason the apply path reuses `build_round`.

**On success** the proposal's `applied_at` is stamped, and the concert stops
being quiet by the ordinary predicate — it now holds a future deadline — so it
leaves `/admin/quiet-ladders` on its own. Nothing special-cases that.

**Dismiss** sets `dismissed_at`; the next poll recognises the key and skips it,
unchanged from phase 1.

## Review surface

`GET /admin/quiet-ladders/proposals` keeps its list, and each concert row links
to `GET /admin/quiet-ladders/proposals/{event_id}` — the draft page, holding
every pending proposal for that concert, each as its own form. Keyed by
`event_id` rather than the numeric concert id, per invariant 6: URLs use the
editor-chosen `event_id` even though every FK targets `Concert.id`. Apply and
dismiss are POSTs under that same path.

A concert whose proposals have all been applied or dismissed leaves the list by
the ordinary predicate; the draft page for it renders an empty state rather
than 404ing, so a link in a days-old digest DM still lands somewhere sensible.
Nothing applies wholesale — the grammar is the tags importer's: it PLANS before
it writes, **every default changes nothing**, and the only irreversible act
happens when explicitly chosen.

Admin-only (`require_admin`, 403 when signed in and unauthorized). English-only
like every other admin operational surface in this repo — that convention is
real and uniform; `{{ _(` appears zero times in any `admin_*.html`.

`label`, `evidence_yaml` and the model's proposed values are all LLM-authored:
invariant 7 applies in full. No user- or model-controlled text in an inline
`on*` handler; values ride in `data-` attributes read via `dataset`.
`source_url` is the concert's editor-supplied `official_url`, not model text —
it still goes through `clean_url` as defence in depth, and phase 2 must not
quietly start storing the model's own per-round `url:` under that name without
revisiting the comment that says so.

## The digest

Reports NEW and CHANGED separately. Only one of them is actionable on the draft
page, and merging them would send the operator to a page whose Approve button
is missing for half the rows.

## Testing

- **The diff's third verdict is pure** and tested without a DB: NEW, HELD and
  CHANGED, including that a difference in `results_at`/`payment_deadline`/
  `applies_to` alone is enough to make a round CHANGED.
- **The apply path is pinned to the QUEUE, not to the row.** Applying a
  proposal with a live deadline must produce `reminder_queue` rows. The
  mutation that matters is deleting the `sync_concert` call: a test asserting
  only that a `Round` exists passes with the reminder silently never
  scheduled. This is the single most important check in the phase.
- **A CHANGED proposal has no apply route.** POSTing an apply for one must be
  refused, not merely hidden in the template — a hidden button is not an
  authorisation check.
- **Derived change-ness resolves itself**: a proposal whose round is edited by
  hand to match stops rendering as a change, with no write to the proposal.
- Every assertion names the single edit that would make the feature wrong while
  leaving it green. Assertions are scoped to their row or section: `base.html`
  renders nav links and a mobile tab bar, and this repo has shipped a test that
  passed with its whole feature deleted because the chrome carried the string.

## Out of scope

- Applying a CHANGED proposal (owner ruling; the editor link is the path).
- The small shape of entry #2 (a round-gap dimension on the discovery matcher).
  [Correction, 2026-08-14: **#4** today — see the note at the top of this file.]
- Editing anything other than the proposal being applied — no bulk apply, no
  concert-level edits from this page.
- The remaining WISHLIST #24 gap: if one reply proposes the same round twice,
  the new-vs-refreshed tally over-counts against one row. Phase 1's
  `_fold_duplicate_keys` collapses the content loss; the tally remains filed.
