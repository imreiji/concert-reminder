"""`ConcertAudit`: a deliberately lightweight per-concert edit history.

Only the concert's own top-level scalar fields -- NOT day/round/tag changes.
`snapshot_concert` must be called BEFORE mutating and `record_concert_edit`
AFTER, or every diff reads as unchanged.
"""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Concert,
    ConcertAudit,
)

# ── Concert edit history ──────────────────────────────────────────────────

# Deliberately just the concert's own top-level fields -- day/round/tag
# adds-removes-edits are NOT tracked here, that's a much bigger feature than
# "lightweight". event_id is included since renaming a concert's URL handle
# is exactly the kind of quiet, easy-to-miss edit an audit log is for.
TRACKED_CONCERT_FIELDS = [
    "event_id", "title", "title_en", "title_zh", "kind", "organizer", "categories",
    "eventernote_url", "official_url", "source_url", "performers_text", "notes",
    "notes_en", "notes_zh",
]


def _audit_value(v: object) -> object:
    """Enum members (e.g. ConcertKind) aren't JSON-serializable -- store
    their plain .value instead. Everything else here is already a
    JSON-safe str/None."""
    return v.value if hasattr(v, "value") else v


def snapshot_concert(concert: Concert) -> dict:
    """A before/after comparison point for record_concert_edit -- call once
    before mutating the concert, once after."""
    return {f: _audit_value(getattr(concert, f)) for f in TRACKED_CONCERT_FIELDS}


async def record_concert_edit(
    session: AsyncSession, concert: Concert, edited_by: int, before: dict
) -> ConcertAudit | None:
    """Diffs `before` (from snapshot_concert, taken pre-mutation) against the
    concert's current state and inserts one audit row -- ONE row per edit
    covering every field that changed, not one row per field. Returns None
    (and inserts nothing) when nothing tracked actually changed, so a no-op
    resubmit of the edit form doesn't pollute the history."""
    after = snapshot_concert(concert)
    changes = [
        {"field": f, "before": before[f], "after": after[f]}
        for f in TRACKED_CONCERT_FIELDS if before[f] != after[f]
    ]
    if not changes:
        return None
    audit = ConcertAudit(concert_id=concert.id, edited_by=edited_by, changes=changes)
    session.add(audit)
    await session.flush()
    return audit


async def concert_audit_log(
    session: AsyncSession, concert_id: int, limit: int = 20
) -> list[ConcertAudit]:
    res = await session.execute(
        select(ConcertAudit)
        .where(ConcertAudit.concert_id == concert_id)
        .order_by(ConcertAudit.edited_at_utc.desc())
        .limit(limit)
    )
    audits = list(res.scalars())
    for a in audits:
        await session.refresh(a, ["editor"])
    return audits
