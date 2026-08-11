"""quiet ladder stamps

Revision ID: 0671edabe2ac
Revises: dd8f33a8747c
Create Date: 2026-08-11 03:47:51.227765
"""
import sqlalchemy as sa
from alembic import op

revision = "0671edabe2ac"
down_revision = "dd8f33a8747c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This migration only ADDS columns and touches no constraint, so it does
    # NOT need naming_convention=NAMING_CONVENTION -- that requirement applies
    # only to migrations calling drop_constraint against legacy tables.
    with op.batch_alter_table("concerts") as batch:
        batch.add_column(sa.Column("quiet_since_utc", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("ladder_rechecked_at_utc", sa.DateTime(), nullable=True))
    # BLANKET stamp, not a predicate backfill, and the difference is the whole
    # point. The reconcile pass clears quiet_since_utc for every concert NOT on
    # the list on its very first run, so stamping everything reaches the same
    # steady state as stamping exactly the quiet ones -- without reimplementing
    # a Python predicate (next_anchor_at, is_round_cancelled) in SQL, where it
    # would be free to disagree with the real one.
    #
    # What it buys: no concert is a NEWCOMER on the first pass, so the first
    # tick after deploy DMs nothing instead of announcing the entire back
    # catalogue at once.
    #
    # CURRENT_TIMESTAMP is SQLite's naive UTC 'YYYY-MM-DD HH:MM:SS', which is
    # exactly the on-disk form UTCDateTime writes (it stores naive UTC and
    # re-attaches tzinfo on read), so these rows read back as aware UTC like
    # any other.
    op.execute("UPDATE concerts SET quiet_since_utc = CURRENT_TIMESTAMP")


def downgrade() -> None:
    with op.batch_alter_table("concerts") as batch:
        batch.drop_column("ladder_rechecked_at_utc")
        batch.drop_column("quiet_since_utc")
