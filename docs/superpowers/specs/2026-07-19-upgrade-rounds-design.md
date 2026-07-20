# Upgrade rounds

Date: 2026-07-19

Branch 5 of the UI/UX refactor. Branch 1 (Home / Discover split) and branch 2
(concert page and editor) have shipped; branch 4 (`ConcertSubscription`) is in
progress and this branch must not depend on it.

## Problem

In Japanese ticketing an **upgrade round** (アップグレード) is a second, nested
campaign riding on a ticket you already hold: seat upgrades, premium packages,
and similar offers you may enter **only if you secured a ticket in one of a set
of qualifying rounds** — often 最速先行, but there can be several. Winning the
upgrade yields its own payment deadline; losing it leaves you exactly where you
were, holding your original secured ticket.

The data model cannot say any of that today:

- A `Round` knows its concert and (via `applies_to`) its legs, but has no way
  to reference **other rounds**. "You must hold a ticket from 最速先行 or
  先行抽選 R1" is not expressible.
- Every round's availability is a **global fact** — open for everyone or for
  no one. An upgrade round is the first whose availability is **per user**:
  it is only meaningfully "open" for users holding a WON/PAID outcome in a
  qualifying round.
- Worse, the current per-user suppression actively fights the concept.
  `_apply_outcome_suppression`'s cross-round pass (`db/service.py:182`) drops
  any round whose legs are all "secured elsewhere" via another round's
  WON/PAID — which silences an upgrade round for **precisely the users who
  are eligible for it**. A secured ticket is the upgrade's prerequisite, not
  a substitute for it.
- The board (`domain/board.py`) and the Discover pill
  (`discover_statuses`, `db/service.py:1453`) both collapse a concert to one
  standing. "Secured, and separately in an upgrade lottery" is two facts, and
  a won upgrade owes money even though the base ticket is fully paid — under
  today's rank (`PAID > WON`) that concert would sit in Secured while a
  payment deadline burns down.

## Approach

Make the upgrade round a real `Round` with a new kind and a reference to the
rounds that qualify a user for it, then thread one derived boolean —
*does this user qualify?* — through the same three seams every previous
per-user concept has used: the suppression filter in front of the reminder
planner, the board precedence, and the status pills. The pure planner
(`domain/reminders.py`) learns nothing; like cancellation and outcomes before
it, eligibility just means it sees fewer candidate rounds.

### The qualifying-round set (schema)

New nullable JSON column on `rounds`:

```python
# db/models.py, class Round
qualifies_round_ids: Mapped[list | None] = mapped_column(JSON)  # optional round ids
```

deliberately mirroring `applies_to`, the existing round-to-many-rows link:

- Same editing mechanism — toggle chips writing a set of ids into one hidden
  field per round row, exactly the shape branch 2 built for legs
  (`_round_leg_chips.html` / `parse_round_legs`).
- Same integrity posture — plain JSON with no FK behind it, so ids are
  **validated at the boundary and filtered at read time** (same concert only,
  no self-reference, dangling ids dropped, the same way
  `concert_round_rows` already drops dangling `applies_to` ids).
- Migration is one nullable `ADD COLUMN` in batch mode. **No data migration,
  no backfill, no table rebuild** — existing rows read NULL, meaning "not an
  upgrade round / no qualifier set recorded".

**Rejected alternative:** an association table
`round_qualifiers(upgrade_round_id, qualifying_round_id)` with named FK and
unique constraints. It buys real referential integrity (CASCADE when a
qualifying round is deleted) and SQL-side querying, at the cost of a second
table, an extra relationship to eager-load everywhere `Round` loads, and an
editing pattern that diverges from the `applies_to` chips shipped last branch.
The JSON column matches the codebase's established shape; the table remains
the right move if round-to-round links ever multiply. Flagged for the owner —
if he prefers the table, only the storage layer of this design changes (it is
a plain `CREATE TABLE`, still no data migration).

Semantics:

- `kind == RoundKind.UPGRADE` is what makes a round an upgrade round.
  `qualifies_round_ids` is only meaningful on that kind and is ignored (and
  not editable) on every other kind.
- **Empty/NULL qualifier set on an upgrade round = "any secured ticket on
  this concert qualifies"** — any other round's WON/PAID counts. This mirrors
  `applies_to`'s "empty = every leg" convention and degrades gracefully when
  an editor doesn't know the exact qualifying rounds.

### New round kind

`RoundKind.UPGRADE = "upgrade"` (`domain/types.py`), label **"Upgrade round"**
(`LABEL_BY_ROUND_KIND`, `db/service.py:710`), emoji **"⬆️"** (`KIND_EMOJI`,
`bot/messages.py:16`). Stored as a string like every kind, so — as with
FCFS/tour package — **adding the enum member itself needs no migration**.
The ramen.events import heuristics are NOT taught to detect upgrade rounds in
this branch (rare on ramen.events; editors reclassify by hand — same posture
`OTHER` has always had).

### Per-user eligibility is derived

New pure helper, `domain/upgrades.py`:

```python
def is_eligible(
    upgrade_round_id: int,
    qualifying_ids: list[int] | None,
    outcome_by_round: dict[int, LotteryOutcome],
) -> bool
```

True when the user holds WON or PAID on any round in the qualifying set
(any other round at all, when the set is empty/None), **or when the user has
recorded any advancing outcome (APPLIED/WON/LOST/PAID) on the upgrade round
itself**. The second clause is self-attestation and it is load-bearing:
eligibility is derived from *recorded* outcomes, but a user can genuinely hold
a qualifying ticket the app was never told about. Acting on the upgrade round
is their testimony, and it must count — otherwise recording "applied" to an
upgrade would be silently followed by the suppression filter eating every
reminder for it. For the same reason, **eligibility never gates the write
path**: `record_round_outcome` and `POST /rounds/{id}/outcome` accept upgrade
outcomes from anyone, unchanged. Eligibility gates what is shown and what is
planned, never what a user may record about themselves.

### Suppression and the reminder planner (invariant 2)

`_apply_outcome_suppression` changes in two places, keeping its pattern —
filter the candidate list before the pure planner ever sees it:

1. **Upgrade rounds are exempt from the cross-round "secured elsewhere"
   pass.** A WON/PAID on another round covering the same legs is the
   upgrade's entry requirement, not a reason to silence it. (Upgrade rounds'
   own WON/PAID still contribute to `secured_by` and suppress *other*
   ordinary rounds normally.)
2. **A new eligibility pass drops an upgrade round entirely for users where
   `is_eligible` is false.** The planner plans nothing, and the existing
   "nothing planned -> delete" sync semantics clear any stale queue rows —
   re-planning stays always-safe. When the user later records the qualifying
   WON, `record_round_outcome`'s existing full-concert re-sync
   (`reinstate_user_rules`) makes the upgrade's reminders appear with no new
   machinery.

The same-round pass applies to upgrade rounds unchanged (PAYMENT anchor moot
after LOST/PAID/NOT_APPLIED, and so on).

Auto-arming: `_auto_arm_next_round` returns early when the lost round is an
upgrade — losing an upgrade ends that side campaign *successfully* (you keep
your secured ticket); there is no "next round" to chase. And
`_next_round_for_leg` excludes UPGRADE kinds from its candidates — losing an
ordinary round must never arm an upgrade you just lost the ability to enter.

### A second payment deadline on one ticket

Already structurally supported, and this spec makes it a stated, tested
guarantee rather than an accident: the upgrade is its own `Round` row with its
own `payment_deadline_at_utc`, `plan_for_rule` plans one occurrence per
(rule, round, anchor), and `reminder_queue`'s dedupe index keys on
(rule_id, round_id, day_id, anchor) — so a concert-wide PAYMENT rule yields
**independent queue rows for the base round's payment and the upgrade's
payment**. Recording PAID on the base round suppresses only the base row (the
same-round pass); change 1 above is what keeps the upgrade's payment row alive
through it. No planner or `sync_rule` changes are needed beyond the
suppression edits — a test must pin the sequence: base PAID + upgrade WON
leaves exactly the upgrade's payment reminder in the queue.

### Display

**Two facts, two pills.** Base standing and upgrade standing are separate
states of separate campaigns and render as separate pills on Discover cards —
base pill first, upgrade pill in the **accent** tone beside it (it is neither
"covered" nor "you owe money"; it is an extra shot):

| Base + upgrade state (eligible user) | Pills |
|---|---|
| Secured, upgrade open, no upgrade outcome | `Secured` (ok) + `Upgrade · Closes in 3d` (accent) |
| Secured, upgrade APPLIED | `Secured` (ok) + `Upgrade · Applied` (accent) |
| Upgrade WON, payment pending | `Upgrade won — pay by 24 Jul` (danger) — **one** urgent pill; the money owed replaces both |
| Upgrade PAID | `Secured` (ok) alone |
| Upgrade LOST | base pill alone |
| Not eligible / signed out | no upgrade pill; the upgrade round participates in the neutral event-state pill only via its kind label, and the neutral countdown prefers non-upgrade open rounds |

`DiscoverStatus` grows an optional second pill (`upgrade_text`,
`upgrade_tone`) and the pill tones gain `accent`; the round-status **facet
stays event-only and viewer-independent** (an open upgrade round is factually
open). Signed out, no standing pills render at all, as today.

**Board precedence** (`domain/board.py`): `column_for` becomes upgrade-aware —
its outcomes input carries which are upgrade outcomes. One new rule, from the
module's own rationale ("the money you owe outranks the round you could still
enter"): **upgrade WON outranks base PAID**, placing the concert in *Won —
pay*. Upgrade PAID ranks as Secured; upgrade APPLIED/LOST never demote a
secured base; an open upgrade round does not pull a Secured concert back to
Open (ranked outcomes already win over `has_open_round`). The function stays
pure; `board_cards` and `discover_statuses` feed it the kind information.

**Home "Coming up" rows** (`my_deadline_rows`): rows for an upgrade round
appear only for eligible users — a row whose buttons you cannot truthfully
press is noise. The capture buttons are the existing ones with upgrade-kind
labels: `Entered upgrade` / `Skipping` (mapping to APPLIED / NOT_APPLIED
through the **same** `POST /rounds/{id}/outcome` — labels change in
`_capture_actions.html`, the write path does not). The global chronological
list (`upcoming_deadlines`) keeps upgrade rows for everyone: that a round
exists and closes is a public fact.

**Concert page** (`concert_round_rows`): the upgrade round renders in its leg
group like any round, kind-labelled. For a signed-in ineligible user it shows
"Requires a ticket from: 最速先行, 先行抽選 R1" (the qualifying rounds'
labels) instead of capture buttons; `capture_gates` gains eligibility as an
input for upgrade rounds.

### Editor

The qualifying set is edited as **chips, exactly mirroring how branch 2 edits
`applies_to`**: a "Qualifies" row inside an accent-washed callout under the
round ("Upgrade round. Only people holding a ticket from a qualifying round
can enter."), one toggle chip per *other saved round* on the concert, and one
hidden `round_qualifiers` input per round row — space-separated round ids,
positionally aligned with the other `round_*` parallel lists, parsed by a
`parse_round_qualifiers` mirroring `parse_round_legs`. The row is shown only
while the round's kind select reads "Upgrade round" (client-side toggle;
server-side, qualifiers submitted for a non-upgrade kind are discarded).

Simplification, stated: chips reference **saved** rounds only. A round created
in the same submit has no id yet and cannot be picked as a qualifier until
saved — acceptable because qualifying rounds (最速先行 etc.) all but always
predate the upgrade round announcement. No `day_key`-style provisional keys
for rounds.

Validation at the boundary: ids must belong to the same concert, self-reference
is dropped, dangling ids are dropped on read (never trusted, never persisted
back silently).

## Out of scope

- `ConcertSubscription` / follow state — branch 4, in progress. Eligibility
  here derives purely from `RoundOutcome` and touches no subscription table.
- The onboarding "Do you hold this ticket?" eligibility-capture tile from the
  concept — onboarding is a later branch.
- ramen.events import heuristics for detecting upgrade rounds.
- Any Discord slash-command surface changes beyond the emoji/label maps that
  existing embeds already read.

## Constraints

- **No second `RoundOutcome` write path** — every outcome write, including
  `Entered upgrade` / upgrade won/paid, goes through `record_round_outcome`
  via the existing `POST /rounds/{id}/outcome` (invariant 2). Queue rows are
  only ever reconciled by the `sync_*` functions; re-planning stays
  always-safe; only successful DM delivery marks sent.
- Migration follows CLAUDE.md's SQLite rules exactly: `Base.metadata`'s
  NAMING_CONVENTION stays (batch/table-rebuild mode refuses the legacy
  anonymous constraints); after autogenerate the revision is edited —
  `app.db.models.UTCDateTime()` replaced with `sa.DateTime()` and the
  `import app.db.models` line removed if present (this revision should
  contain neither, but the check is performed, not skipped); config files
  stay ASCII-only for the owner's GBK-locale Windows machine; the
  `coalesce()` dedupe index on `reminder_queue` is not touched.
- Baseline: **638 passed + 1 known-failing local test**
  (`tests/test_crud.py::test_test_dm_when_bot_disabled` — repo-root `.env`
  carries a real `DISCORD_TOKEN`; pre-existing, CI-green, out of scope).
- Times render dual, JST first, via `fmt_dual`. Never store or compare naive
  datetimes.
- Every page touched keeps (or gains) a logged-in GET render test.
- `routes/imports.py` stays registered before `routes/concerts.py`.
- Invariant 7: round labels are user-controlled — chips carry them as text
  content with `data-` attributes, never inline `on*` handlers; any inline
  script data goes through `| tojson`; `data-name` is avoided (collides with
  `base.html`'s `filterChips()`).
- Sentence case throughout.

## Testing

- Eligibility: qualifying WON and PAID each qualify; APPLIED/LOST do not; the
  empty-set fallback accepts any secured round on the concert; self-attestation
  (own APPLIED on the upgrade) qualifies; an unrelated concert's win does not.
- Suppression: an eligible user's upgrade CLOSES reminder **survives** the
  base round being PAID — the regression the unmodified cross-round pass
  would cause, asserted directly. An ineligible user's round-scoped upgrade
  rule plans zero queue rows; recording the qualifying WON afterward makes
  them appear via the existing re-sync.
- Two payment deadlines: one concert-wide PAYMENT rule, base round PAID,
  upgrade WON — the queue holds exactly the upgrade's payment row; recording
  upgrade PAID clears it.
- Auto-arm: LOST on an upgrade arms nothing; LOST on a base round never arms
  an upgrade round.
- Board: base PAID + upgrade WON lands in Won — pay; upgrade PAID in Secured;
  upgrade APPLIED/LOST leave a secured base in Secured; open upgrade round
  does not pull a Secured concert to Open.
- Discover: each pill row of the table above, signed-out renders no standing
  pills, and the facet value is identical signed in and out.
- Editor: a two-qualifier upgrade round survives an edit round-trip with both
  ids; self and cross-concert ids are rejected; qualifiers on a non-upgrade
  kind are discarded; dangling ids are dropped on read.
- Render tests: Home, Discover, concert page, editor, all logged in;
  ineligible user sees the "Requires a ticket from" line and no capture
  buttons.

## Verification

Beyond the suite: seed a concert with 最速先行 and an upgrade round qualified
by it. As a user, record 最速先行 WON then PAID — the upgrade pill appears
beside Secured on Discover and the upgrade row appears in Coming up. Press
`Entered upgrade`, then record the upgrade WON — the card collapses to the
single urgent payment pill and the board card moves to Won — pay. Record PAID
— back to Secured. As a second user with no outcomes, confirm the upgrade row
is absent from Home and the concert page shows the requirement line.
