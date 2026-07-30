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
import sys

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


def _console_safe(text: str) -> str:
    """A tag name that the terminal cannot encode, rendered as escapes instead.

    The report below prints Japanese names. The deploy runs on the UTF-8 server,
    where they render properly, but the owner's Windows console is GBK -- and a
    diagnostic print must never be the thing that aborts a migration. Escape
    rather than raise.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode("unicode_escape").decode("ascii")


# A handle this short says nothing about the tag. It happens when a mostly-CJK
# name contains one stray Latin letter -- "Kアリーナ横浜" survives as "k" -- which
# the "use whatever survives" rule accepts. Reported, not corrected: the
# threshold would be arbitrary, and the real cause is a missing name_en.
_WEAK_HANDLE_LENGTH = 2


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
    weak: list[tuple[int, str, str, str]] = []      # id, handle, why, name
    no_english: list[tuple[int, str]] = []          # id, name
    for row in rows:
        from_english = _slug_core(row.name_en)
        from_name = _slug_core(row.name)
        base = from_english or from_name or row.kind
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE tags SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row.id},
        )
        if not from_english:
            # The owner's expectation is that this list comes back EMPTY: the
            # trilingual rule has made name_en mandatory at every tag create
            # boundary for a while. A non-empty list means older rows predate it.
            no_english.append((row.id, row.name))
        if not from_english and not from_name:
            weak.append((row.id, candidate, "no usable characters in either name", row.name))
        elif len(base) <= _WEAK_HANDLE_LENGTH:
            weak.append(
                (row.id, candidate, f"only {len(base)} usable character(s)", row.name)
            )

    _report(len(rows), weak, no_english)

    # 3. One rebuild for all three structural changes: drop the unique on name,
    #    make the handle NOT NULL, make the handle unique.
    #
    # THIS MUST STAY INSIDE upgrade(). It briefly lived after _report's body
    # instead, which meant it ran only when there was something to report --
    # _report returns early on clean data -- so a database whose handles all came
    # out well got a HALF-MIGRATED schema: slug added and nullable, the unique on
    # name still there, and the revision stamped as applied. Every fixture
    # happened to contain a row worth reporting, so the tests passed for the
    # wrong reason. test_the_report_says_so_when_there_is_nothing_to_review now
    # asserts the schema too, which is what catches this.
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint("uq_tags_name", type_="unique")
        batch.alter_column("slug", existing_type=sa.String(length=100), nullable=False)
        batch.create_unique_constraint("uq_tags_slug", ["slug"])


def _report(
    total: int,
    weak: list[tuple[int, str, str, str]],
    no_english: list[tuple[int, str]],
) -> None:
    """Print what the backfill had to guess at, so it can be corrected.

    Handles are editable on the Tags page, and a bad one is invisible until
    somebody trips over it -- so the one moment worth naming them is the moment
    they are minted. Nothing here changes the outcome; it only reports it.
    """
    print(f"\ntag handles: {total} tag(s) backfilled.")
    if not weak and not no_english:
        print("  every handle came from an English name. Nothing to review.\n")
        return

    if weak:
        print(f"  {len(weak)} handle(s) worth renaming on the Tags page:")
        for tag_id, handle, why, name in weak:
            print(f"    id {tag_id:<5} handle {handle!r:<18} {why}: {_console_safe(name)}")
    if no_english:
        print(
            f"  {len(no_english)} tag(s) have no English name, so the handle came "
            f"from the Japanese one:"
        )
        for tag_id, name in no_english:
            print(f"    id {tag_id:<5} {_console_safe(name)}")
        print(
            "  Filling in those English names then renaming the handles gives the "
            "best result;\n  nothing is broken either way."
        )
    print()


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
