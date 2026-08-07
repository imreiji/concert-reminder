"""The self-populating round-label phrase library.

Typed triples become one-click suggestions, because real round labels do not
decompose into a taxonomy -- they are phrases people actually write.
"""


from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import _now
from app.db.models import (
    RoundLabelPhrase,
)

# ── Round-label phrases ───────────────────────────────────────────────────


async def record_round_label_phrase(
    session: AsyncSession, label: str, label_en: str, label_zh: str
) -> None:
    """Remember a trilingual round label so later concerts can reuse it.

    Only a COMPLETE triple is recorded: a suggestion that fills two of three
    boxes still leaves the editor typing, which is the cost this exists to
    remove. Reusing an existing triple bumps its count rather than inserting
    a duplicate -- that count is what ranks the picker.
    """
    label, label_en, label_zh = label.strip(), label_en.strip(), label_zh.strip()
    if not (label and label_en and label_zh):
        return

    # One statement, used by both the pre-check and the post-race re-select:
    # if the two ever drifted apart, the re-select would stop finding the
    # winner's row and the bump would be silently skipped.
    lookup = select(RoundLabelPhrase).where(
        RoundLabelPhrase.label == label,
        RoundLabelPhrase.label_en == label_en,
        RoundLabelPhrase.label_zh == label_zh,
    )

    existing = (await session.execute(lookup)).scalar_one_or_none()
    if existing is None:
        # The only try/except IntegrityError in the app -- everywhere else
        # pre-checks instead, and the pre-check above is still the normal
        # path. The catch exists ONLY for the race: two editors saving the
        # same never-before-seen triple in one flush window, where the loser
        # hits the unique index. What makes this site different from the
        # others is the cost of not catching. This runs inside the
        # transaction of whatever concert save triggered it, so an escaping
        # IntegrityError rolls back that editor's ENTIRE save -- their real
        # work destroyed by a convenience feature. A savepoint keeps the blast
        # radius to this insert -- SQLAlchemy flushes the session when the
        # nested transaction opens, so the caller's pending rows are already
        # persistent and ROLLBACK TO SAVEPOINT cannot reach them (pinned by
        # test_a_lost_race_does_not_take_the_callers_pending_work_with_it).
        # And losing the race means someone else already remembered this
        # triple, so falling through to the bump is the correct outcome, not
        # a consolation prize.
        try:
            async with session.begin_nested():
                session.add(
                    RoundLabelPhrase(label=label, label_en=label_en, label_zh=label_zh)
                )
        except IntegrityError:
            existing = (await session.execute(lookup)).scalar_one_or_none()

    if existing is not None:
        existing.used_count += 1
        existing.last_used_at = _now()
    await session.flush()


async def round_label_phrases(
    session: AsyncSession, limit: int = 50
) -> list[RoundLabelPhrase]:
    """The picker's list: most-used first, most-recent breaking ties."""
    return list((await session.execute(
        select(RoundLabelPhrase)
        .order_by(RoundLabelPhrase.used_count.desc(), RoundLabelPhrase.last_used_at.desc())
        .limit(limit)
    )).scalars())


async def forget_round_label_phrase(session: AsyncSession, phrase_id: int) -> bool:
    """Stop offering a phrase. Returns False when it was already gone.

    Deliberately does NOT touch rounds that used it -- a phrase is a
    suggestion, not a foreign key, so forgetting a typo leaves the concerts
    that carry it exactly as they are.
    """
    existing = await session.get(RoundLabelPhrase, phrase_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True
