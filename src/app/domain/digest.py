"""The admin delivery digest's body, as a pure function.

Deliberately impersonal. The digest groups by what was sent and counts the
recipients rather than naming them, for three reasons: identity in a DM builds
a permanent record of who follows which artists in a place POST /me/delete
cannot reach; a 100-reminder tick would blow Discord's 2000-character ceiling;
and the recipient COUNT is the actual anomaly detector -- a group reading x40
on a three-user app is the tell that something fanned out wrongly, which a
per-recipient list would bury.

English-only and not wrapped in _(): the body is composed at queue time,
before any recipient is known, so translating it would mean gettext_in per
admin for operational copy only admins read.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from app.domain.types import Anchor, DeliveryOutcome, DeliverySource

# Separate caps rather than one shared line budget: failures are the reason
# this message exists, so they get their own allowance and can never be
# squeezed out by a large batch of successful sends.
MAX_FAILURE_LINES = 10
MAX_SENT_GROUPS = 10


@dataclass(frozen=True)
class DeliveryFact:
    """One attempted delivery, flattened out of a DeliveryLog row. A plain
    dataclass so this module stays pure -- no ORM, no session."""

    source: DeliverySource
    outcome: DeliveryOutcome
    user_id: int
    concert_title: str | None
    leg_label: str | None
    round_label: str | None
    anchor: Anchor | None
    note_kind: str | None
    # Grouping keys. Present because the LABELS above are per-recipient: the
    # titles and labels on a reminder row come from due_reminders, which
    # resolves them with loc_field(..., user.language). Grouping on the label
    # text would therefore split one concert's fan-out across languages --
    # x25 "Snow Miku 2027" plus x15 "スノーミク2027" instead of x40 -- and the
    # recipient count is the whole anomaly signal this digest exists to carry.
    # Ids are language-independent, so they group correctly; the label is only
    # ever used for DISPLAY, taken from the first fact in the group.
    concert_id: int | None = None
    round_id: int | None = None
    day_id: int | None = None


def _group_key(fact: DeliveryFact) -> tuple:
    """What makes two deliveries "the same thing". Ids only, never labels --
    see the note on DeliveryFact's id fields."""
    if fact.source is DeliverySource.NOTIFICATION:
        return (DeliverySource.NOTIFICATION, fact.concert_id, fact.note_kind)
    return (DeliverySource.REMINDER, fact.concert_id, fact.round_id, fact.day_id, fact.anchor)


def _describe(fact: DeliveryFact) -> str:
    """Human label for one group, rendered from any member of it. A reminder
    is identified by its anchor and the round/leg it names; a notification has
    neither, only its kind."""
    if fact.source is DeliverySource.NOTIFICATION:
        head = fact.note_kind or "notice"
        return f"{head} · {fact.concert_title or '(no concert)'}"
    parts = [p for p in (fact.concert_title, fact.leg_label, fact.round_label) if p]
    anchor = fact.anchor.value if fact.anchor else "?"
    return f"{anchor} · {' / '.join(parts) if parts else '(no concert)'}"


def build_digest(facts: list[DeliveryFact], batch_at_utc: datetime) -> str:
    """Render the digest, or "" when there is nothing to report.

    Returning "" rather than a "nothing happened" line is what keeps a quiet
    app quiet: the caller queues no notification at all for an empty result.
    """
    if not facts:
        return ""

    failures = [f for f in facts if f.outcome is not DeliveryOutcome.SUCCESS]
    sent = [f for f in facts if f.outcome is DeliveryOutcome.SUCCESS]
    users = len({f.user_id for f in facts})
    stamp = batch_at_utc.strftime("%Y-%m-%d %H:%M UTC")

    head = f"{len(sent)} sent · {users} users · batch {stamp}"
    if failures:
        head = f"⚠ {len(failures)} failed / {head}"
    lines = [head]

    if failures:
        lines += ["", "FAILED"]
        for fact in failures[:MAX_FAILURE_LINES]:
            lines.append(f"  {fact.outcome.value} · {_describe(fact)}")
        if len(failures) > MAX_FAILURE_LINES:
            lines.append(f"  +{len(failures) - MAX_FAILURE_LINES} more failures")

    if sent:
        # Count by id-tuple, label from the first member. Counter alone cannot
        # do this -- it would key on the label and re-split what the key just
        # unified -- so keep a parallel first-seen map for display.
        counts: Counter = Counter(_group_key(f) for f in sent)
        labels: dict[tuple, str] = {}
        for fact in sent:
            labels.setdefault(_group_key(fact), _describe(fact))
        lines += ["", "SENT"]
        for key, count in counts.most_common(MAX_SENT_GROUPS):
            lines.append(f"  ×{count}  {labels[key]}")
        if len(counts) > MAX_SENT_GROUPS:
            lines.append(f"  +{len(counts) - MAX_SENT_GROUPS} more groups")

    return "\n".join(lines)
