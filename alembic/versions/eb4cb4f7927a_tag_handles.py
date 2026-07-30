"""tag handles: add slug, drop the unique on name

Revision ID: eb4cb4f7927a
Revises: aebefef6ca70
Create Date: 2026-07-30 00:47:14.249952

A tag's name stops being its identity. Two performers may genuinely share a
name, and a venue may share one with a group (owner ruling, 2026-07-29), so
`UNIQUE (name)` goes and a unique `slug` takes over.

`tags` is one of the two legacy tables this project has been bitten by:
migrations built it up with ANONYMOUS constraints, and batch mode reflects the
real table rather than the metadata, so a `drop_constraint` by conventional
name aborted a deploy once with "No such constraint". Passing
naming_convention into batch_alter_table is what lets reflection name the
constraint so it can be found. As of aebefef6ca70 the live DB happens to have
`CONSTRAINT uq_tags_name` (an earlier batch rebuild re-emitted it named), so
the drop would resolve either way -- but both vintages are covered by
tests/test_migration_legacy_anonymous_constraints.py and the argument is kept
because it costs nothing and the anonymous shape is the one that broke.

The slug rule is INLINED below rather than imported from app.domain.slugs: a
revision has to keep working after the application changes underneath it. It
must stay in step with service.assign_tag_slug, and the notable part is the
fallback -- the KIND, not slugify()'s "concert", which would be a lie on a tag
and indistinguishable from a tag really named that.
"""
import re

import sqlalchemy as sa
from alembic import op

from app.db.models import NAMING_CONVENTION

revision = 'eb4cb4f7927a'
down_revision = 'aebefef6ca70'
branch_labels = None
depends_on = None


def _slug_core(text: str | None) -> str:
    """Frozen copy of app.domain.slugs.slug_core. Deliberately duplicated.

    Note this runs in PYTHON, not SQL: SQLite's lower() and trim() are
    ASCII-and-U+0020-only, and this table is full of Japanese.
    """
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


def upgrade() -> None:
    # 1. Nullable first -- the values do not exist yet.
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as batch:
        batch.add_column(sa.Column("slug", sa.String(length=100), nullable=True))

    # 2. Backfill. Ordered by id so the numeric suffixes are deterministic:
    #    the older of two colliding rows keeps the bare handle.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, name, name_en, kind FROM tags ORDER BY id")
    ).fetchall()
    used: set[str] = set()
    for row in rows:
        base = _slug_core(row.name_en) or _slug_core(row.name) or row.kind
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE tags SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row.id},
        )

    # 3. One rebuild for all three structural changes: drop the unique on name,
    #    make the handle NOT NULL, make the handle unique.
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint("uq_tags_name", type_="unique")
        batch.alter_column("slug", existing_type=sa.String(length=100), nullable=False)
        batch.create_unique_constraint("uq_tags_slug", ["slug"])


def downgrade() -> None:
    """WILL FAIL if the new freedom has been used, and that is correct.

    Two tags sharing a name cannot go back to a unique name column -- there is
    no answer to which one keeps it. Restore from the pre-migration backup
    instead of trying to force this through.
    """
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint("uq_tags_slug", type_="unique")
        batch.drop_column("slug")
        batch.create_unique_constraint("uq_tags_name", ["name"])
