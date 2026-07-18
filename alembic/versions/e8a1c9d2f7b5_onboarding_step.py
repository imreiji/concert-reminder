"""onboarding step

Revision ID: e8a1c9d2f7b5
Revises: 5ea945b713c4
Create Date: 2026-07-18 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e8a1c9d2f7b5'
down_revision = '5ea945b713c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('onboarding_step', sa.Integer(), server_default='0', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('onboarding_step')
