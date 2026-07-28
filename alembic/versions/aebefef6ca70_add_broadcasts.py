"""add broadcasts

Revision ID: aebefef6ca70
Revises: 4d208d277be7
Create Date: 2026-07-28 12:11:45.869406
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = 'aebefef6ca70'
down_revision = '4d208d277be7'
branch_labels = None
depends_on = None


# This is an add-column migration, but one of the columns carries a FOREIGN
# KEY, and Alembic refuses `ALTER ... ADD CONSTRAINT` on SQLite -- so batch
# mode is not optional here. Two alternatives were tried and rejected:
#
#   - Raw `ALTER TABLE notifications ADD COLUMN broadcast_id INTEGER
#     CONSTRAINT ... REFERENCES broadcasts(id) ON DELETE SET NULL`. SQLite
#     accepts it and the FK really fires, but reflection cannot recover a name
#     from a COLUMN-level constraint (it only parses the table-level
#     `CONSTRAINT x FOREIGN KEY (...)` form), so `alembic check` and every
#     future autogenerate would emit a phantom remove_fk/add_fk pair on
#     notifications forever -- and applying that pair unedited is the
#     `No such constraint` failure CLAUDE.md records as already having shipped
#     once. It defers the table rebuild rather than avoiding it, and makes it
#     accidental.
#   - Adding the column with no FK at all. Test DBs are built from
#     Base.metadata and would keep passing while the server silently lost
#     ON DELETE SET NULL -- exactly the divergence CLAUDE.md warns about.
#
# Batch mode on `notifications` is precedented and server-proven: c680f2fbb288
# added `concert_id` + `kind` and a FK to this same table the same way, and it
# has shipped. The naming convention is passed per CLAUDE.md so that any
# anonymous constraint on the live server (which predates the convention, and
# which no test DB can see) is named during reflection rather than tripping the
# rebuild.
def upgrade() -> None:
    op.create_table('broadcasts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.Column('mode', sa.Enum('batch', 'all', 'explicit', name='broadcastmode'), nullable=False),
    sa.Column('mode_param', sa.Text(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('recipient_count', sa.Integer(), nullable=False),
    sa.Column('send_after_utc', sa.DateTime(), nullable=False),
    sa.Column('cancelled_at_utc', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.discord_id'], name=op.f('fk_broadcasts_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_broadcasts'))
    )
    with op.batch_alter_table(
        'notifications', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('send_after_utc', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('broadcast_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_notifications_broadcast_id_broadcasts'), 'broadcasts', ['broadcast_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table(
        'notifications', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_notifications_broadcast_id_broadcasts'), type_='foreignkey')
        batch_op.drop_column('broadcast_id')
        batch_op.drop_column('send_after_utc')

    op.drop_table('broadcasts')
