# Per-round lottery outcome tracking design

## Context

The top-ranked WISHLIST.md item (raised 2026-07-18 UX review, impact:
high, effort: large — the largest-effort item tackled this session).
Reminders currently fire regardless of the user's actual situation:
someone who lost a lottery round still gets its payment reminder; someone
who already won a leg via lottery still gets nudged about that leg's
general sale. The wishlist entry's claim that this has "a natural
surface: the existing state-aware DM buttons" is **inaccurate** — verified
against the current code (`bot/views.py`): every existing `DynamicItem`
button (`ApplyDefaultButton`, `RemoveRemindersButton`,
`ReinstateRemindersButton`, `SnoozeButton`) manages *reminder rules*, not
lottery outcomes. No "did you apply?" UI exists anywhere today; this spec
builds it from scratch, reusing the existing button *pattern*, not
existing buttons.

**Domain clarification that shapes this whole spec** (confirmed with the
owner): a single named lottery item ("最速先行 Round 1") is frequently
represented as *multiple separate `Round` rows*, one per leg, because each
leg's lottery is drawn independently — a user can win one leg and lose
another for what looks like "the same round." All suppression and
sequencing logic below is leg-scoped (via `Round.applies_to`), not
round-name-scoped.

A round-model refactor is planned separately, later, outside this spec.
This design is built against the *current* `Round`/`applies_to` shape;
adapting it to a future refactor is out of scope here.

## Non-goals

- **A web UI for setting outcomes.** DM buttons only (confirmed) — no
  control on the concert detail page or preferences.
- **Auto-arming being deferred.** Both halves (outcome-tracking +
  suppression, and auto-arming the next round) are in this one spec,
  by explicit choice, despite the size difference — see the two answers
  in the session that settled this.
- **Retroactive outcome inference.** This only affects reminders going
  forward from when a user actually clicks a button; it never guesses an
  outcome for a round that already fully played out before this feature
  shipped.
- **Changing cancellation-based suppression.** The existing
  `is_round_cancelled`/cancelled-leg mechanism is untouched; this spec
  adds an orthogonal, per-user suppression layer alongside it.
- **The round-model refactor** mentioned above — explicitly out of scope,
  a separate future effort.

## Section 1: Data model

```python
class RoundOutcome(Base):
    """One user's recorded progress through a specific round's lottery:
    NOT_APPLIED (explicitly opted out) / APPLIED / WON / LOST / PAID.
    Strict sequence enforced in record_round_outcome, not at the DB layer:
    APPLIED -> (WON | LOST) -> PAID (PAID only reachable from WON)."""

    __tablename__ = "round_outcomes"
    __table_args__ = (Index("uq_round_outcome", "user_id", "round_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    outcome: Mapped[LotteryOutcome] = mapped_column(
        Enum(LotteryOutcome, values_callable=lambda e: [m.value for m in e])
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)
```

`LotteryOutcome` (new enum in `domain/types.py`, alongside the existing
`Anchor`/`TagKind`/etc.): `NOT_APPLIED`, `APPLIED`, `WON`, `LOST`, `PAID`.

**Which state suppresses which of *that same round's* own remaining
reminders** (RESULTS/PAYMENT only — OPENS/CLOSES are already in the past
by the time any outcome is recordable):

| Outcome | Suppresses |
|---|---|
| `NOT_APPLIED` | RESULTS, PAYMENT (not in the running) |
| `APPLIED` | nothing (still waiting on results) |
| `WON` | nothing (still needs to pay) |
| `LOST` | PAYMENT (nothing to pay) |
| `PAID` | PAYMENT (already done) |

## Section 2: DM buttons

Four new `discord.ui.DynamicItem` buttons in `bot/views.py`, following the
existing `custom_id` pattern: `dk:outcome:{round_id}:{new_state}`
(`new_state` one of `applied`/`not_applied`/`won`/`lost`/`paid`).

Attached to the reminder DM (`build_reminder_message` in
`bot/messages.py`) based on the reminder's anchor and the round's current
outcome state for that user:

- **CLOSES reminder**, no outcome recorded → "I applied" / "Didn't apply"
- **RESULTS reminder**, outcome is `APPLIED` (or unset — see backfill
  below) → "Won" / "Lost"
- **PAYMENT reminder**, outcome is `WON` → "Paid"

Requires adding `round_id: int | None` to the `DueReminder` dataclass
(`db/service.py`) — it doesn't carry this today — threaded through
`due_reminders()`'s query and into `build_reminder_message`'s
button-attachment logic.

Every button's callback calls a new service function:

```python
async def record_round_outcome(
    session: AsyncSession, user_id: int, round_id: int, outcome: LotteryOutcome
) -> None: ...
```

Responsibilities, in order:
1. Enforce the sequence, permissively: `NOT_APPLIED` and `APPLIED` may
   only be set when no row exists yet (they're starting states); `WON`
   and `LOST` may be set regardless of the current state, including when
   no row exists at all — clicking "Won"/"Lost" without ever having
   clicked "I applied" just works (per your answer), it doesn't error or
   require a separate backfill write. `PAID` may only be set when the
   current state is `WON`.
2. Upsert the `RoundOutcome` row.
3. Re-sync the user's own rules on this round (applies Section 1's
   same-round suppression).
4. If the new state is `WON`, re-sync the user's rules across the whole
   concert (Section 3's cross-round suppression may now apply to other
   rounds).
5. If the new state is `LOST`, attempt to auto-arm the next round for
   that leg (Section 4).

Per this project's established convention (every existing `DynamicItem`
button callback), these callbacks are not independently unit-tested —
`record_round_outcome` is tested at the service layer, and the buttons
themselves are reviewed by inspection.

## Section 3: Suppression mechanism

Hooks into `sync_rule` (`db/service.py`) at exactly the point where
cancelled rounds already get filtered out of the candidate list *before*
it reaches `plan_for_rule` — which stays completely unchanged, still
ignorant of outcomes exactly as it's already ignorant of cancellation.

**Cross-round suppression (the "leg already secured" rule):** compute the
set of leg (`ConcertDay`) ids the calling rule's user has secured — the
union of `applies_to` across every round where they have `WON` or `PAID`
(empty `applies_to` on a won round means *every* leg of that concert is
secured). Any other round whose own `applies_to` (empty treated as "every
leg of the concert") is a *subset* of that secured-legs set is dropped
from the candidate list entirely, for any anchor — matching the
confirmed rule precisely: a round covering even one leg the user hasn't
won stays fully active; losing one leg while winning another leaves the
lost leg's own rounds reminding as normal.

**Same-round, anchor-specific suppression:** after the cross-round pass,
for each remaining round, drop it from the candidate list *for this
specific rule* if the rule's own `anchor` is RESULTS and the user's
outcome for that round is `NOT_APPLIED`, or if the rule's anchor is
PAYMENT and the outcome is `LOST`, `PAID`, or `NOT_APPLIED` (per Section
1's table). This must be anchor-aware because a single rule only ever
targets one anchor (`plan_for_rule`'s existing contract) — a
concert-wide PAYMENT-anchored rule should still cover every *other*
round's payment reminder even while skipping this one.

Since `RoundOutcome` is per-user state (unlike cancellation, which is
global), the trigger is explicit: `record_round_outcome`'s steps 3-4 call
`sync_rule` directly for the affected user's rules. `sync_concert`
(triggered by editor edits) doesn't need to change for this half — only
for auto-arm's catch-up path (Section 4).

## Section 4: Auto-arm the next round

When a user marks a round `LOST`:

1. Find the next round for the same leg(s): among rounds for the same
   concert with `opens_at_utc` set (rounds with no opens time can't be
   ordered chronologically and are skipped from this search) whose
   `applies_to` overlaps the lost round's `applies_to` (or is empty — a
   General round covers every leg), take the one with the earliest
   `opens_at_utc` that is strictly after the lost round's own
   `closes_at_utc` (fall back to its `opens_at_utc` if `closes_at_utc` is
   unset).
2. If found, and the user doesn't already have a `ReminderRule` targeting
   that round's OPENS anchor: look up their default preset
   (`get_default_preset`, already exists) for an OPENS-anchor item's
   offset; if none, use `offset_days=0` (fire right at open). Create a
   real `ReminderRule` (round-scoped, OPENS anchor, that offset) and call
   `sync_rule` on it — a genuine rule from here on, participating in the
   same re-planning/postponement/deletion machinery as any
   manually-created rule, not a parallel ad-hoc mechanism.
3. If no qualifying round exists yet, nothing happens now — it isn't lost.
   `sync_concert` gets one added responsibility: whenever it re-syncs a
   concert (already triggered on every edit, including adding a new
   round), it also checks every user with a `LOST` outcome on an affected
   leg who doesn't yet have a rule on the newly-relevant round, and
   auto-arms it the same way. This reuses the existing "re-sync on edit"
   trigger rather than adding a new one on a timer.

## Testing

- **Service-layer** (new `tests/test_lottery_outcomes.py`, matching this
  project's per-feature test-file convention): `record_round_outcome`
  permissively allows `WON`/`LOST` with no prior `APPLIED` row, rejects
  `PAID` unless the current state is `WON`; is idempotent on a second
  call; each of the 5 same-round suppression rules from Section
  1's table; the secured-legs subset rule in both directions (partial win
  doesn't suppress a shared General round; full win does; losing one leg
  while winning another leaves the lost leg's own rounds un-suppressed);
  auto-arm creates a real `ReminderRule` using the default preset's offset
  or immediate; auto-arm doesn't duplicate an existing rule; the
  `sync_concert` catch-up path for a next round that didn't exist at
  lose-time.
- **Message-building**: `build_reminder_message` attaches the correct
  button pair (or none) for a CLOSES/RESULTS/PAYMENT reminder depending on
  the round's current outcome state — pure embed/view construction, no
  Discord network needed.
- **Button callbacks**: not independently unit-tested, per this project's
  established convention for every other `DynamicItem` — reviewed by
  inspection.
