"""tag eventernote url

Revision ID: 08aa8b07ad1b
Revises: ce1a80bb66f4
Create Date: 2026-07-19 21:24:14.529054
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = '08aa8b07ad1b'
down_revision = 'ce1a80bb66f4'
branch_labels = None
depends_on = None


# The real `tags` table predates the naming convention, so its constraints
# are anonymous on disk. Batch (table-rebuild) mode copies those constraints
# and refuses to name them itself unless handed the convention -- pass it so
# the rebuild succeeds against the production-shaped table, not only a
# metadata-built one.
def upgrade() -> None:
    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('eventernote_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('eventernote_url')
