"""windows to rounds

Revision ID: 20b27b1046f4
Revises: 54f9ca25125c
Create Date: 2026-07-17 01:05:17.443219

Hand-written, not the raw autogenerate output: autogenerate saw "windows"
disappear and "rounds" appear and proposed drop_table+create_table (which
would destroy every existing round) plus add_column+drop_column for
window_id->round_id (which would silently orphan every window-scoped
reminder rule, since values are never copied). This version does a real
rename instead -- every existing row and its reminder_rules/reminder_queue
references keep their data and meaning. The two new timestamp columns are
purely additive (nullable, no backfill needed).

FK constraints on reminder_rules/reminder_queue are deliberately left
untouched (no drop_constraint/create_foreign_key): SQLite's
`ALTER TABLE windows RENAME TO rounds` automatically repoints FK
definitions in other tables that reference it, and batch_alter_table's
column rename picks up that already-updated definition when it reflects
the table. Explicitly dropping+recreating the constraint was tried and
silently produced a table with NO foreign key at all (verified against a
minimal repro) -- plain rename is both simpler and the only one that
actually preserves the constraint.
"""
from alembic import op
import sqlalchemy as sa

revision = '20b27b1046f4'
down_revision = '54f9ca25125c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('windows', 'rounds')
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('results_at_utc', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('payment_deadline_at_utc', sa.DateTime(), nullable=True))

    with op.batch_alter_table('reminder_rules', schema=None) as batch_op:
        batch_op.alter_column('window_id', new_column_name='round_id')

    with op.batch_alter_table('reminder_queue', schema=None) as batch_op:
        batch_op.alter_column('window_id', new_column_name='round_id')

    # The dedupe index's coalesce() expression referenced window_id by name.
    # SQLite/Alembic can't reflect expression indexes, so the batch rebuild
    # above already silently dropped it (it couldn't carry it over to the
    # rebuilt table) -- recreate it under the new column name.
    op.create_index(
        'uq_reminder_queue_dedupe', 'reminder_queue',
        ['rule_id', sa.text('coalesce(round_id, 0)'), sa.text('coalesce(day_id, 0)'), 'anchor'],
        unique=True,
    )


def downgrade() -> None:
    with op.batch_alter_table('reminder_queue', schema=None) as batch_op:
        batch_op.alter_column('round_id', new_column_name='window_id')

    with op.batch_alter_table('reminder_rules', schema=None) as batch_op:
        batch_op.alter_column('round_id', new_column_name='window_id')

    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.drop_column('payment_deadline_at_utc')
        batch_op.drop_column('results_at_utc')
    op.rename_table('rounds', 'windows')

    op.create_index(
        'uq_reminder_queue_dedupe', 'reminder_queue',
        ['rule_id', sa.text('coalesce(window_id, 0)'), sa.text('coalesce(day_id, 0)'), 'anchor'],
        unique=True,
    )
