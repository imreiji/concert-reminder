"""drop legacy venue columns

Revision ID: ce43bfcfcae3
Revises: 14bc590fdb44
Create Date: 2026-07-22 21:40:58.481394
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION

revision = 'ce43bfcfcae3'
down_revision = '14bc590fdb44'
branch_labels = None
depends_on = None


# concerts and concert_days predate the naming convention, so their
# constraints are anonymous on the live server. Batch mode copies them and
# refuses to name them itself unless handed the convention -- without this a
# drop can die on the server with "No such constraint" (has shipped once).
def upgrade() -> None:
    with op.batch_alter_table('concerts', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_column('venue_zh')
        b.drop_column('venue_en')
        b.drop_column('venue')
    with op.batch_alter_table('concert_days', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_column('venue_address')
        b.drop_column('venue')
        b.drop_column('city')


def downgrade() -> None:
    # Recreates the columns empty -- the free-text data is gone by design.
    with op.batch_alter_table('concert_days', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.add_column(sa.Column('city', sa.String(length=100), nullable=True))
        b.add_column(sa.Column('venue', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_address', sa.String(length=300), nullable=True))
    with op.batch_alter_table('concerts', schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.add_column(sa.Column('venue', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_en', sa.String(length=200), nullable=True))
        b.add_column(sa.Column('venue_zh', sa.String(length=200), nullable=True))
