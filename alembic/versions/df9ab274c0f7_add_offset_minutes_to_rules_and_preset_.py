"""add offset_minutes to rules and preset items

Revision ID: df9ab274c0f7
Revises: fc4a98ad678a
Create Date: 2026-08-19 00:26:50.407516
"""
from alembic import op
import sqlalchemy as sa


revision = 'df9ab274c0f7'
down_revision = 'fc4a98ad678a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only: no drop_constraint, so the legacy anonymous-constraint
    # hazard (CLAUDE.md, Migrations) does not apply and no naming_convention
    # needs threading through. Existing rows are already canonical -- whole
    # hours, zero minutes -- so there is nothing to backfill.
    for table in ('reminder_rules', 'preset_items'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column('offset_minutes', sa.Integer(), nullable=False, server_default='0')
            )


def downgrade() -> None:
    for table in ('reminder_rules', 'preset_items'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('offset_minutes')
