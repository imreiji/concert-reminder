"""ops check state

Plain CREATE TABLE with no drop_constraint, so unlike 1384cadd692e this does
NOT need the legacy-schema fixture in
tests/test_migration_legacy_anonymous_constraints.py -- there is no existing
constraint to reflect and rename.

Revision ID: ce1a80bb66f4
Revises: 1384cadd692e
Create Date: 2026-07-19 03:04:30.891664
"""
from alembic import op
import sqlalchemy as sa


revision = 'ce1a80bb66f4'
down_revision = '1384cadd692e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ops_check_state',
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=True),
    sa.Column('changed_at', sa.DateTime(), nullable=True),
    sa.Column('last_notified_at', sa.DateTime(), nullable=True),
    sa.Column('pending_ok', sa.Boolean(), nullable=True),
    sa.Column('pending_since', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_ops_check_state'))
    )


def downgrade() -> None:
    op.drop_table('ops_check_state')
