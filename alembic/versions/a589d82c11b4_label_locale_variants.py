"""label locale variants

Revision ID: a589d82c11b4
Revises: 789bbcc95bc3
Create Date: 2026-07-22 11:00:45.435372
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = 'a589d82c11b4'
down_revision = '789bbcc95bc3'
branch_labels = None
depends_on = None


# `concert_days` and `rounds` predate the naming convention, so their
# constraints are anonymous on the live server. Batch mode copies them and
# refuses to name them itself unless handed the convention.
#
# There is no backfill: rounds.label_en already holds English values and keeps
# them untouched; the three new columns start NULL and fall through to the
# original column via i18n.loc_field.
def upgrade() -> None:
    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('label_en', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('label_zh', sa.String(length=100), nullable=True))

    with op.batch_alter_table(
        'rounds', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('label_zh', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        'rounds', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('label_zh')

    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('label_zh')
        batch_op.drop_column('label_en')
