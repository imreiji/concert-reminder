"""discovery manual sweep request

`sweep_requested_at` is an operator pressing "Sweep now" on /admin/discoveries.
A REQUEST, not a run: the scheduler's next tick sees it and calls the one
`run_sweep` implementation, because a sweep is bounded at four minutes and no
HTTP request may hold that.

Nullable, and NULL means "nothing pending" -- which is what every existing row
means, so there is no backfill. Cleared by `stamp_discovery_run` on every sweep,
successful or not.

Additive only, so the normal deploy order applies (upgrade, then restart).

Revision ID: 34179560cec0
Revises: d4dac7d14230
Create Date: 2026-07-31 08:46:59.331036
"""
from alembic import op
import sqlalchemy as sa


revision = '34179560cec0'
down_revision = 'd4dac7d14230'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('discovery_state', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sweep_requested_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('discovery_state', schema=None) as batch_op:
        batch_op.drop_column('sweep_requested_at')
