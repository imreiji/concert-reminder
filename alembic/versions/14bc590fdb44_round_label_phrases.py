"""round label phrases

Revision ID: 14bc590fdb44
Revises: a589d82c11b4
Create Date: 2026-07-22 15:46:23.146917
"""
from alembic import op
import sqlalchemy as sa


revision = '14bc590fdb44'
down_revision = 'a589d82c11b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'round_label_phrases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('label_en', sa.String(length=200), nullable=False),
        sa.Column('label_zh', sa.String(length=200), nullable=False),
        sa.Column('used_count', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_round_label_phrases')),
    )
    op.create_index(
        'uq_round_label_phrase', 'round_label_phrases',
        ['label', 'label_en', 'label_zh'], unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_round_label_phrase', table_name='round_label_phrases')
    op.drop_table('round_label_phrases')
