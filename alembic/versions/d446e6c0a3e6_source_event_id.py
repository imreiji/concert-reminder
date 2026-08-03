"""source event id

Revision ID: d446e6c0a3e6
Revises: f846bca262ad
Create Date: 2026-08-02 23:45:58.372625
"""
from alembic import op
import sqlalchemy as sa


revision = 'd446e6c0a3e6'
down_revision = 'f846bca262ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HAND-WRITTEN, deliberately: --autogenerate reads a column rename as
    # drop + add, which would destroy every existing lead's external id -- the
    # one column the whole discovery diff keys on. alter_column with
    # new_column_name is the only shape that moves the DATA across.
    #
    # `discovered_events` was created whole by 48cd59cae5d7 with NAMED
    # constraints, so batch-mode reflection needs no naming_convention
    # treatment here; nothing below calls drop_constraint.
    with op.batch_alter_table('discovered_events', schema=None) as batch_op:
        batch_op.alter_column(
            'eventernote_event_id',
            new_column_name='source_event_id',
            type_=sa.String(length=200),
            existing_type=sa.String(length=20),
            existing_nullable=False,
        )
        # server_default, not just a Python-side default: the rows already in
        # production are rewritten by this migration and must land on a real
        # value, and both columns are NOT NULL.
        batch_op.add_column(sa.Column(
            'source', sa.String(length=40), nullable=False,
            server_default='eventernote',
        ))
        batch_op.add_column(sa.Column(
            'date_is_deadline', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))

    # Batch mode carries the reflected UNIQUE across the rename but keeps its
    # OLD NAME, leaving the server with `uq_discovered_events_eventernote_
    # event_id UNIQUE (source_event_id)` while Base.metadata's convention says
    # `uq_discovered_events_source_event_id`. Every test DB is built from the
    # metadata, so that divergence is invisible to the whole suite and would
    # surface only as a future migration's `ValueError: No such constraint` on
    # the live DB -- the exact failure CLAUDE.md's naming-convention section
    # records having shipped once. Renaming it now costs one more rebuild of a
    # table with a few hundred rows.
    #
    # Safe to drop by name here precisely because this table post-dates the
    # convention: 48cd59cae5d7 created it with named constraints, so reflection
    # finds this one. A legacy table would need the naming_convention= fixture.
    with op.batch_alter_table('discovered_events', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_discovered_events_eventernote_event_id', type_='unique'
        )
        batch_op.create_unique_constraint(
            'uq_discovered_events_source_event_id', ['source_event_id']
        )


def downgrade() -> None:
    with op.batch_alter_table('discovered_events', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_discovered_events_source_event_id', type_='unique'
        )
        batch_op.create_unique_constraint(
            'uq_discovered_events_eventernote_event_id', ['source_event_id']
        )

    with op.batch_alter_table('discovered_events', schema=None) as batch_op:
        batch_op.drop_column('date_is_deadline')
        batch_op.drop_column('source')
        batch_op.alter_column(
            'source_event_id',
            new_column_name='eventernote_event_id',
            type_=sa.String(length=20),
            existing_type=sa.String(length=200),
            existing_nullable=False,
        )
