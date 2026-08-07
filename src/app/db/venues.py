"""`sync_concert_venue_tags`: a concert's VENUE tags, derived from its legs.

Venues live on the LEG. This rewrites the concert-level rollup as the union of
its legs' venues, and RETURNS what it newly attached -- every caller must feed
those to `handle_newly_tagged`, since VENUE tags are subscribable
(invariant 4).
"""


from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConcertDay,
    ConcertTag,
    Tag,
)
from app.db.tags import attach_tag
from app.domain.types import (
    TagKind,
)

# ── Venue rollup (legs -> concert) ───────────────────────────────────────


async def sync_concert_venue_tags(session: AsyncSession, concert_id: int) -> list[Tag]:
    """Rewrite a concert's VENUE tag rows as the union of its legs' venues.

    The leg is the single place a venue is entered, so the concert level is
    derived and can never contradict it. Only VENUE rows are touched --
    franchise/group/artist attachment is deliberate and materialized (invariant
    3), and must survive untouched.

    Discover's region filter reads concert_tags client-side off each tile's
    data-tags, so keeping this rollup current is exactly what lets that filter
    stay unchanged while venues live on legs.

    Returns the tags it NEWLY attached, which every caller must hand to
    `handle_newly_tagged`. VENUE tags are subscribable (the tags page lists
    them; POST /subscriptions puts no kind restriction on them), so a user
    following "Zepp Haneda" is owed the same DM notice and preset auto-apply
    a concert-level attach gives them (invariant 4). Attaching through
    `attach_tag` rather than a bare ConcertTag insert is also what makes a
    re-run idempotent instead of a composite-PK IntegrityError.

    `desired` is filtered to Tag.kind == VENUE for the same reason `current`
    always was: the two sets must be defined over the same population. A
    non-VENUE id in the column would otherwise sit in `desired` forever
    without ever reaching `current`, so every save would re-add it and the
    second would die on the primary key -- permanently unsavable. The route
    boundary rejects such an id (see `resolve_day_venue_tags`); this is the
    second, cheaper end of the same guard.
    """
    desired = {t.id: t for t in (await session.execute(
        select(Tag)
        .join(ConcertDay, ConcertDay.venue_tag_id == Tag.id)
        .where(ConcertDay.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars()}

    current = set((await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars())

    for tag_id in current - set(desired):
        await session.execute(
            delete(ConcertTag).where(
                ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
            )
        )
    newly: list[Tag] = []
    for tag_id in sorted(set(desired) - current):
        # expand=False is a no-op for a VENUE tag (only GROUP expands), but
        # stated rather than defaulted: this path must never materialize
        # anything beyond the venue itself.
        newly += await attach_tag(session, concert_id, desired[tag_id], expand=False)
    await session.flush()
    return newly
