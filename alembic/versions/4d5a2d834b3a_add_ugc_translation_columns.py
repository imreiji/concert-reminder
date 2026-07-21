"""add ugc translation columns

Revision ID: 4d5a2d834b3a
Revises: f5e514a2daac
Create Date: 2026-07-20 21:12:43.318923
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = '4d5a2d834b3a'
down_revision = 'f5e514a2daac'
branch_labels = None
depends_on = None


# Both `concerts` and `tags` predate the naming convention, so their
# constraints are anonymous on disk. Batch (table-rebuild) mode copies those
# constraints and refuses to name them itself unless handed the convention --
# pass it so the rebuild succeeds against the production-shaped table, not only
# a metadata-built one (see CLAUDE.md's migration notes).
def upgrade() -> None:
    with op.batch_alter_table(
        'concerts', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('title_zh', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('venue_en', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('venue_zh', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('notes_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('notes_zh', sa.Text(), nullable=True))

    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('name_en', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('name_zh', sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('name_zh')
        batch_op.drop_column('name_en')

    with op.batch_alter_table(
        'concerts', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('notes_zh')
        batch_op.drop_column('notes_en')
        batch_op.drop_column('venue_zh')
        batch_op.drop_column('venue_en')
        batch_op.drop_column('title_zh')
