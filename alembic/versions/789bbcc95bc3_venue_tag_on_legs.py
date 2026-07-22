"""venue tag on legs

Revision ID: 789bbcc95bc3
Revises: 4d5a2d834b3a
Create Date: 2026-07-22 00:53:44.963634
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = '789bbcc95bc3'
down_revision = '4d5a2d834b3a'
branch_labels = None
depends_on = None


# SQLite's single-argument trim() strips only U+0020 SPACE. Python's
# str.strip() (what find_venue_tag actually calls) is Unicode-aware and also
# strips tab/newline/CR and U+3000 IDEOGRAPHIC SPACE -- very plausible in
# venue text pasted from a Japanese ticketing site. Passing this explicit
# character set to every trim() call below keeps the SQL backfill matching
# find_venue_tag's Python semantics exactly; drift here would silently widen
# the "unmatched venue" report with false negatives.
_TRIM_CHARS = "' ' || char(9) || char(10) || char(13) || char(12288)"


# `tags` and `concert_days` predate the naming convention, so their constraints
# are anonymous on the live server. Batch mode copies them and refuses to name
# them itself unless handed the convention (see CLAUDE.md's migration notes).
def upgrade() -> None:
    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('city', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('city_en', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('city_zh', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('address', sa.String(length=300), nullable=True))

    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('venue_tag_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_concert_days_venue_tag_id_tags', 'tags',
            ['venue_tag_id'], ['id'], ondelete='SET NULL',
        )
        batch_op.create_index(
            'ix_concert_days_venue_tag_id', ['venue_tag_id'], unique=False
        )

    # Backfill using the SAME rule find_venue_tag applies today: trimmed,
    # lowercased, exact. Anything that does not match is left NULL and reported
    # below -- the free-text columns stay in place this deploy so a miss is
    # recoverable.
    conn = op.get_bind()
    conn.execute(sa.text(f"""
        UPDATE concert_days
           SET venue_tag_id = (
               SELECT t.id FROM tags t
                WHERE t.kind = 'venue'
                  AND lower(trim(t.name, {_TRIM_CHARS}))
                      = lower(trim(concert_days.venue, {_TRIM_CHARS}))
                ORDER BY t.id
                LIMIT 1
           )
         WHERE venue IS NOT NULL AND trim(venue, {_TRIM_CHARS}) <> ''
    """))

    unmatched = conn.execute(sa.text(f"""
        SELECT id, venue FROM concert_days
         WHERE venue IS NOT NULL AND trim(venue, {_TRIM_CHARS}) <> ''
           AND venue_tag_id IS NULL
         ORDER BY id
    """)).fetchall()
    if unmatched:
        print(f"\n  {len(unmatched)} leg(s) had a venue with no matching VENUE tag:")
        for day_id, venue in unmatched:
            print(f"    concert_days.id={day_id}  venue={venue!r}")
        print("  Create those tags and set the legs' venue before phase 5 drops "
              "the free-text columns.\n")


def downgrade() -> None:
    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_index('ix_concert_days_venue_tag_id')
        batch_op.drop_constraint('fk_concert_days_venue_tag_id_tags', type_='foreignkey')
        batch_op.drop_column('venue_tag_id')

    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('address')
        batch_op.drop_column('city_zh')
        batch_op.drop_column('city_en')
        batch_op.drop_column('city')
