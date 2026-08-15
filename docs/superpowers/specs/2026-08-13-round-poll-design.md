# Round poll: DeepSeek re-reads a quiet concert's own official page

Design, 2026-08-13. Implements the LARGE shape of WISHLIST #2 ("Round watch:
the two shapes it did not ship"). The small shape — teaching the discovery
matcher a round dimension — is explicitly **not** in scope and stays on the
list; owner ruling, 2026-08-13.

## The problem, and what already defends against it

A concert in the catalogue can grow a round after it was entered. Nothing
re-reads it. Round watch (shipped 2026-08-11) made that failure VISIBLE —
`/admin/quiet-ladders` lists every catalogue concert whose ladder holds no
future deadline, longest-unattended first, and a digest DM names each one
within a minute of going quiet — but the defence stops at visibility. Acting
on the list is the owner remembering to open each concert's official page and
read it.

This automates the reading. It does not automate the deciding.

## The finding that sized this

Entry #2 estimated "a scheduler pass over concerts instead of over pending
drafts". That is accurate, and the pass it names already exists.
`draft_completion.py` (phase 2 of the AI pipeline) already:

- resolves a document's `official_url` and asks `FetchDomain` whether the host
  may be read at all, recording an unknown host as pending and skipping,
- fetches through `app/fetching.py` with `ApprovedPublicHosts` and normalises
  the page with `domain/page_text.py`,
- builds the prompt (`domain/round_completion.py:completion_prompt`) and parses
  the reply (`parse_completion_response`) — both pure,
- and refuses any proposed round whose timestamps are not quoted from the text
  the model was actually given (`domain/round_evidence.py:verify_rounds`,
  `ProposedRound`, `Verdict`) — pure, and the reason the app can promise "a
  deadline it names is real".

`concert_to_yaml` already renders a LIVE concert in the same document shape
that prompt consumes, because that is what `export.zip` writes.

**So a live concert can be handed to the existing completion prompt with no
second prompt and no second safety rule.** What is genuinely new is the run
order, a place to keep proposals, and a review surface. This is why the entry's
"large" is worth re-reading as "moderate, in two phases".

## Scope, in two phases

**Phase 1 — the pass finds rounds and tells you.** The daily run, the
`round_proposals` table, host queueing, the digest DM, and the proposals list.
Useful alone: a DM naming a concert and a round it appears to have grown is
already better than the current state.

**Phase 2 — the draft page and the per-round apply.** Reviewing a proposal
field by field and writing the approved ones onto the live concert.

> **2026-08-14: phase 2 shipped.** Design in
> `docs/superpowers/specs/2026-08-14-round-poll-phase-2-design.md`. This
> section is left as written 2026-08-13 -- the record should show what was
> believed then, including the parts phase 2 changed (a moved date is
> SURFACED, not silently discarded, which needed a third verdict this
> document does not describe; the write path turned out creates-only, an
> owner ruling phase 2's own document explains).

The split is deliberate: phase 2 writes rounds onto concerts users already hold
reminders for, and that is where a defect costs the most. Phase 1 writes
nothing a user can see.

## Architecture

One new module `src/app/round_poll.py`, sitting ABOVE `db/` exactly like
`triage.py`, `discovery.py` and `draft_completion.py`: it imports `domain/`,
`app.llm`, `app.fetching` and `db.service`, and nothing in `db/` imports it.
It is the RUN ORDER only — which concerts, in what sequence, and what a failure
at each step costs.

The judgement stays pure and DB-free:

- `domain/round_proposals.py` (new) — given the rounds a concert already holds
  and the rounds a page proposes, which are NEW. Pure; no session, no network,
  no key. This is the module a test can hammer.
- `domain/round_evidence.py` (existing, unchanged) — whether a proposed round
  may exist at all.
- `domain/round_completion.py` (existing, unchanged) — the prompt and the parse.

`src/app/db/round_proposals.py` (new feature module) owns the table's reads and
writes and is re-exported from `db/service.py`. Per CLAUDE.md's layer rule it
imports `core`, never the facade; every new name must be added to `service.py`
or `tests/test_service_facade.py` fails.

## Data model

One new table, `round_proposals`. **This requires a migration** — the first
since the Following rework's four phases shipped without one.

| column | meaning |
| --- | --- |
| `id` | PK |
| `concert_id` | FK to `concerts.id`, `ondelete="CASCADE"` — a proposal for a deleted concert is meaningless, unlike `PendingDraft.concert_id`'s SET NULL, which preserves provenance |
| `label` | the round name the page gave, verbatim |
| `kind` | the `RoundKind` the model assigned |
| `opens_at_utc` / `closes_at_utc` | aware UTC, nullable (invariant 1) |
| `evidence_yaml` | field → the quoted source line, one small YAML document, BESIDE the proposal rather than inside it — the `PendingDraft.completion_yaml` precedent |
| `source_url` | the page it was read from |
| `first_seen_at` | when the first poll produced it |
| `dismissed_at` | NULL until refused |
| `applied_at` | NULL until written onto the concert (phase 2) |
| `dedupe_key` | see below |

**Pending is both `dismissed_at` and `applied_at` NULL** — the nullable-timestamp
idiom `FetchDomain` and `PendingDraft` already use, rather than a status string
with its own vocabulary.

**`dedupe_key` is what makes a dismissal stick.** Daily polling means the same
page yields the same proposal tomorrow. The key is derived from what the round
NAMED and when it opens — normalised label plus `opens_at_utc` — and a unique
index on `(concert_id, dedupe_key)` means a re-poll updates the existing row
rather than adding a second. A dismissed row therefore stays dismissed and is
never re-proposed, which is the same grammar as a pruned group member staying
pruned (invariant 3) and a declined `FetchDomain` host never being proposed
again.

Deriving the key rather than storing an opaque hash is deliberate: a key you
can read is a key you can debug, and normalisation (fold width, strip spacing)
belongs in the pure module where it is testable.

Two cases the key must answer explicitly, because leaving them implicit is how
a dedupe rule quietly becomes wrong:

- **No `opens_at_utc`.** A page can name a round without giving an open time.
  The key uses the normalised label alone in that case, so the same unnamed-date
  round does not accumulate one row per poll.
- **A date that CHANGES between polls.** The key includes `opens_at_utc`, so a
  round whose open time moved produces a NEW key and therefore a new proposal,
  even if an earlier one was dismissed. This is intended: "1次先行 opens Sept 3"
  and "1次先行 opens Sept 10" are different claims about the world, and a
  dismissal of the first is not a judgement on the second. The cost is that a
  page which keeps editing a date re-proposes; the alternative — keying on the
  label alone — would let a dismissal silently swallow a corrected deadline,
  which is the failure this feature exists to prevent.

## The daily pass

**Gated.** `settings.round_poll_enabled: bool = False`, the same shape as
`discovery_enabled` — one config value switching the whole subsystem, absent
from production until deliberately set.

**Clocked.** Its own daily stamp (`round_poll_due` / `stamp_round_poll_run`),
modelled on the discovery sweep's, including the lesson recorded there: the
stamp is written even when the run FAILS, or a crashing pass re-runs every 60
seconds forever.

**Candidates.** Every quiet concert, longest-unattended first —
`quiet_ladder_rows(session)` already returns exactly that set in exactly that
order (never-checked before ever-checked, then oldest check, then longest
quiet). Owner ruling, 2026-08-13: no per-run cap.

Consuming that query rather than re-deriving "which concerts are worth
re-reading" is the entry's own instruction, and the reason is drift: a second
definition of quiet is a second thing to keep in step.

**One prompt per concert.** This is what makes the uncapped run safe, and it is
worth stating because the obvious precedent misleads. The triage classify pass
failed on a real 511-lead queue because it put N items in ONE prompt and the
call outgrew its context. Here each concert is its own call, so a failure is
per-concert and the run continues. The wall-clock precedent also exists: the
discovery sweep already performs ~95 third-party fetches inside a single
scheduler tick.

**Per concert, in order:**

1. No `official_url` → skip, counted in the report. A quiet concert nobody gave
   a page is a fact worth reporting, not an error.
2. Host unknown → `note_fetch_domain` records it pending, concert skipped. Host
   declined → skipped, silently, because a human already refused it.
3. Fetch (`ApprovedPublicHosts`) → `normalize_page_text`.
4. Render the concert with `concert_to_yaml`, build the completion prompt, call
   `llm.chat`, parse.
5. `verify_rounds` — anything ungrounded is REJECTED WITH ITS REASON, recorded,
   never silently dropped. A real deadline quietly discarded is exactly as
   harmful as a fake one quietly kept: in both cases nobody knows to look.
6. Diff against the concert's existing rounds (`domain/round_proposals.py`).
   Survivors are upserted by `dedupe_key`; dismissed keys are skipped.

**Failure is per step and never fatal to the run.** A fetch error, an
unparseable reply, a refused merge — each records why against that concert and
the pass moves to the next, the warns-and-skips habit every parser in `domain/`
already follows.

**The digest.** One DM to `ADMIN_WHITELIST` through the notifications outbox
(invariant 4 — never send from a route or a pass directly), `concert_id = NULL`
so the drain renders plain text. It is **not** added to `UNREPORTED_NOTE_KINDS`:
that set is only for notices that report on DELIVERIES, and this reports on a
third-party page — exactly the `discovery` notice's precedent. An admin who has
never signed in must be `ensure_user`d first, but only when `session.get(User,
admin_id)` returns None, or every run overwrites a real admin's username with a
placeholder.

## Review

**Phase 1** adds `/admin/quiet-ladders/proposals` — the concerts holding
pending proposals, each with its count and how long it has waited. Read-only in
phase 1.

**Phase 2** makes each concert open a DRAFT PAGE: every proposed round rendered
with its quoted source line, and every round approved or dismissed
INDIVIDUALLY. Nothing applies wholesale. Owner ruling, 2026-08-13: one-click
apply is too dangerous for a write onto a live concert.

The grammar is the tags importer's, deliberately: it PLANS before it writes,
every default changes nothing, and the only destructive act happens solely when
explicitly chosen.

**Approving writes through the existing round-creation service path**, never by
inserting a `Round` row directly. Invariant 2: `reminder_queue` is a
materialized outbox and any edit to rounds must call the relevant `sync_*`. A
round conjured straight into the table is a deadline nobody gets reminded about
— the exact failure this feature exists to prevent, reintroduced by the fix.

Dismissing sets `dismissed_at`; the next poll recognises the key and skips it.

## Testing

- **`domain/round_proposals.py` is tested without a DB, a network or a key** —
  the diff, the key derivation and its normalisation. This is where the
  behaviour lives, so this is where the tests concentrate.
- **The pass** is tested with a fake LLM and a fake fetch, the way `triage` and
  `draft_completion` already are: no DeepSeek call in the suite.
- **Every host branch** — unknown, declined, approved, missing URL — has a test,
  because the trust decision is the part that must not regress quietly.
- **Dismissal survives a re-poll**: the same page, polled twice, must not
  re-propose a dismissed round. The mutation that would leave a naive test
  green is dropping the dedupe check while the fixture only ever polls once.
- **Phase 2's apply** must be pinned to the queue: applying a round with an
  open deadline must produce queue rows. Asserting only that a `Round` row
  exists would pass with `sync_*` deleted.

Per this repo's standing discipline: for every assertion, name the single edit
that would make the feature wrong while leaving it green.

## Decisions recorded

- **Reuse the completion prompt** via `concert_to_yaml` rather than authoring a
  second prompt for the same job. Two prompts asking the same question drift,
  and only one of them would keep `verify_rounds`.
- **Off by default**, behind `ROUND_POLL_ENABLED`.
- **Uncapped daily run** (owner, 2026-08-13), safe because the unit of work is
  one concert per call.
- **Cascade, not SET NULL**, on `concert_id` — a proposal is about a concert,
  not a record of where a concert came from.

## Out of scope

- The small shape of entry #2 (a round-gap dimension on the discovery matcher).
  It catches a case this cannot: a concert holding a future anchor AND a missing
  round is never quiet, so it never becomes a candidate here.
- Polling concerts that are NOT quiet.
- Any change to what `is_quiet` means.
