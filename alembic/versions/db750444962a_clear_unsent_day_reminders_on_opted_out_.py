"""clear unsent day reminders on opted out legs

Data migration only, no schema change. sync_rule now plans no day rows for
a leg its user opted out of, but reminder_queue is a materialized outbox:
rows planned before that fix stay queued until some unrelated write resyncs
the rule, and the scheduler delivers them meanwhile. This is the owner's own
repro, so it must not survive the deploy.

Revision ID: db750444962a
Revises: aba3e97e4467
Create Date: 2026-08-04 01:57:02.719817
"""
from alembic import op

revision = 'db750444962a'
down_revision = 'aba3e97e4467'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deletes exactly the rows sync_rule would no longer plan: UNSENT,
    # day-anchored, where the rule's own user holds a LegOptOut on that day.
    # Rows planned before the fix otherwise sit queued until some unrelated
    # write resyncs the rule, and the scheduler delivers them meanwhile.
    # Unsent-only: sent rows are history (the delivery already happened);
    # deleting unsent rows is always safe (invariant 2 -- re-planning is
    # safe, and opting back in re-plans them). Round-anchored rows are not
    # stale: the round-suppression pass ran at write time since per-leg
    # opt-outs shipped.
    op.execute(
        """
        DELETE FROM reminder_queue
        WHERE sent_at_utc IS NULL
          AND day_id IN (
            SELECT lo.concert_day_id
            FROM leg_opt_outs lo
            JOIN reminder_rules rr ON rr.user_id = lo.user_id
            WHERE rr.id = reminder_queue.rule_id
          )
        """
    )


def downgrade() -> None:
    # Nothing to restore: the deleted rows are exactly what any resync
    # re-plans (invariant 2), and after downgrading the code re-plans them.
    pass
