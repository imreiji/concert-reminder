"""user welcomed_at

Revision ID: aba3e97e4467
Revises: d446e6c0a3e6
Create Date: 2026-08-03 23:22:42.670063
"""
from alembic import op
import sqlalchemy as sa


revision = 'aba3e97e4467'
down_revision = 'd446e6c0a3e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("welcomed_at", sa.DateTime(), nullable=True))
    # Backfill every existing row as already welcomed, from created_at:
    # copying a column the same TypeDecorator wrote sidesteps every datetime
    # string-format question, and at migration time "existing row" cannot be
    # split into onboarded-web-user vs bot-first bare row anyway (both are
    # onboarding_step 0), so everyone is grandfathered as done.
    op.execute("UPDATE users SET welcomed_at = created_at")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("welcomed_at")
