"""Task 2: PendingDraft persists a multi-draft paste as a work batch.

Not step state -- /setup deliberately holds none, since every screen there
re-derives current DB truth. A pending draft is different: fifty to a hundred
concerts, each needing a human to read a preview, is not one sitting. Rows
outlive the request that created them so a closed tab never loses the batch.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, PendingDraft, User
from app.db.service import (
    create_pending_drafts,
    delete_user,
    discard_pending_draft,
    mark_pending_committed,
    pending_drafts,
)
from app.domain.draft import ParsedConcert
from app.domain.yaml_import import DraftBatch, ParsedDraft

USER_A = 111
USER_B = 222
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _batch(*titles: str) -> DraftBatch:
    drafts = tuple(
        ParsedDraft(
            text=f"title: {title}\n",
            parsed=ParsedConcert(title=title, venue_name=None),
        )
        for title in titles
    )
    return DraftBatch(drafts=drafts)


async def _seed_user(session, discord_id: int) -> None:
    session.add(User(discord_id=discord_id, username=f"user{discord_id}"))
    await session.flush()


async def test_create_pending_drafts_makes_one_row_per_document_with_own_text(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A", "B", "C"), USER_A)

        assert len(rows) == 3
        assert [r.title for r in rows] == ["A", "B", "C"]
        assert [r.draft_text for r in rows] == [
            "title: A\n", "title: B\n", "title: C\n",
        ]
        assert all(r.created_by == USER_A for r in rows)


async def test_pending_drafts_scoped_to_pasting_user(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        await _seed_user(s, USER_B)
        await create_pending_drafts(s, _batch("mine"), USER_A)
        await create_pending_drafts(s, _batch("theirs"), USER_B)

        mine = await pending_drafts(s, USER_A)
        theirs = await pending_drafts(s, USER_B)

        assert [r.title for r in mine] == ["mine"]
        assert [r.title for r in theirs] == ["theirs"]


async def test_pending_drafts_excludes_committed_and_discarded_rows(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(
            s, _batch("keep", "commit-me", "discard-me"), USER_A
        )
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()

        assert await mark_pending_committed(s, rows[1].id, concert.id, NOW) is True
        assert await discard_pending_draft(s, rows[2].id, NOW) is True

        remaining = await pending_drafts(s, USER_A)
        assert [r.title for r in remaining] == ["keep"]


async def test_mark_pending_committed_stamps_committed_at_and_concert_id(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()

        ok = await mark_pending_committed(s, rows[0].id, concert.id, NOW)
        assert ok is True

        row = await s.get(PendingDraft, rows[0].id)
        assert row.committed_at == NOW
        assert row.concert_id == concert.id


async def test_discard_pending_draft_stamps_discarded_at_leaves_concert_id_null(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)

        ok = await discard_pending_draft(s, rows[0].id, NOW)
        assert ok is True

        row = await s.get(PendingDraft, rows[0].id)
        assert row.discarded_at == NOW
        assert row.concert_id is None


async def test_committing_already_committed_row_returns_false_without_restamping(db):
    """The same double-submit rule `dismiss_lead` follows: a second commit
    must not silently rewrite which concert a draft claims to have produced.
    Asserting only "still committed" would pass even if the implementation
    overwrote the row -- capture the ORIGINAL values and compare."""
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert1 = Concert(title="X", event_id="x-1")
        concert2 = Concert(title="Y", event_id="y-1")
        s.add_all([concert1, concert2])
        await s.flush()

        assert await mark_pending_committed(s, rows[0].id, concert1.id, NOW) is True

        row = await s.get(PendingDraft, rows[0].id)
        original_committed_at = row.committed_at
        original_concert_id = row.concert_id
        assert original_concert_id == concert1.id

        second = await mark_pending_committed(s, rows[0].id, concert2.id, LATER)
        assert second is False

        await s.refresh(row)
        assert row.committed_at == original_committed_at
        assert row.concert_id == original_concert_id


async def test_deleting_the_concert_leaves_the_row_with_concert_id_null(db):
    """Requires PRAGMA foreign_keys=ON: without it this cascade never fires
    and the row would keep pointing at a concert that no longer exists."""
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()
        assert await mark_pending_committed(s, rows[0].id, concert.id, NOW) is True

        row = await s.get(PendingDraft, rows[0].id)
        await s.delete(concert)
        await s.flush()
        await s.refresh(row)

        assert row is not None, "deleting the concert must not delete the pending draft"
        assert row.concert_id is None


async def test_deleting_the_user_removes_their_pending_drafts(db):
    """Self-serve erasure (invariant 5, POST /me/delete -> service.delete_user)
    is a bare `session.delete(user)` relying entirely on ondelete= clauses to
    do the right thing. A pending draft is the pasting editor's own
    un-actioned working text -- personal data, not shared catalogue -- so it
    must go with them the way reminder_rules/web_sessions/etc. do, rather than
    dangle or block the delete outright.

    Requires PRAGMA foreign_keys=ON: without it SQLite enforces no FK action
    at all, `created_by` would happily reference a deleted user, and this
    test would pass for the wrong reason -- the exact trap the fixture rule
    exists for.
    """
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A", "B"), USER_A)
        row_ids = [r.id for r in rows]

        assert await delete_user(s, USER_A) is True

        remaining = await s.execute(
            select(PendingDraft).where(PendingDraft.id.in_(row_ids))
        )
        assert remaining.scalars().all() == []
