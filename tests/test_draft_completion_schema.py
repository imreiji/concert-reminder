"""The phase-2 columns, and what their defaults mean.

A written 0 on a run means "looked, found none"; NULL means "never got there,
or not this kind's business". That distinction is the whole reason these
columns are nullable, so it is pinned here rather than left to a comment.
"""

from datetime import UTC, datetime

import pytest

from app.db.models import FetchDomain, PendingDraft, TriageRun, User


@pytest.mark.asyncio
async def test_triage_run_defaults_to_the_classify_kind(session):
    # requested_by is a real FK to users.discord_id; the fixture enforces
    # PRAGMA foreign_keys=ON, so a row must exist to satisfy it. Flushed
    # separately: with no ORM relationship() linking the two classes, a
    # single combined flush does not reliably order the INSERTs by table
    # dependency.
    session.add(User(discord_id=1, username="admin"))
    await session.flush()
    run = TriageRun(requested_at=datetime.now(UTC), requested_by=1)
    session.add(run)
    await session.flush()
    assert run.kind == "classify"
    # A classify run never gets there, so the completion counts stay absent.
    assert run.drafts_completed is None
    assert run.rounds_added is None
    assert run.rounds_rejected is None
    assert run.blocked_domains is None


@pytest.mark.asyncio
async def test_pending_draft_starts_with_no_completion_record(session):
    # created_by is a real FK to users.discord_id; same reasoning as above.
    session.add(User(discord_id=1, username="editor"))
    await session.flush()
    row = PendingDraft(draft_text="title: x\nrounds: []\n", title="x", created_by=1)
    session.add(row)
    await session.flush()
    assert row.completion_yaml == ""


@pytest.mark.asyncio
async def test_fetch_domain_pending_is_both_timestamps_null(session):
    row = FetchDomain(
        host="eplus.jp",
        first_seen_at=datetime.now(UTC),
        first_seen_url="https://eplus.jp/sf/detail/1234",
    )
    session.add(row)
    await session.flush()
    assert row.approved_at is None and row.declined_at is None
