"""The shared DB fixtures must be indistinguishable from what they replaced.

`conftest._schema_ddl` builds the schema from precompiled statements rather
than calling `Base.metadata.create_all`, purely for speed. That is only safe
while the two produce the SAME schema, and "same" has to mean compared, not
assumed -- a missing index or a subtly different constraint would not fail
loudly, it would make some OTHER test pass for the wrong reason, somewhere
else, months later.

So the first test below diffs every object SQLite ends up storing. The rest pin
the two properties the 81 hand-copies were relied upon for: real cascade
enforcement, and no state surviving between tests.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from conftest import _schema_ddl  # tests/ is not a package; pytest puts it on sys.path
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, User

_MASTER = (
    "select type, name, coalesce(sql, '') from sqlite_master "
    "where name not like 'sqlite_%' order by type, name"
)

WHEN = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)  # UTCDateTime rejects naive values


@pytest_asyncio.fixture()
async def create_all_db():
    """The OLD fixture, verbatim, kept as the reference to diff against."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_precompiled_ddl_builds_the_same_schema_as_create_all(db, create_all_db):
    async with db() as fast, create_all_db() as reference:
        got = (await fast.execute(text(_MASTER))).all()
        want = (await reference.execute(text(_MASTER))).all()

    assert got == want, (
        "the precompiled DDL in conftest._schema_ddl has drifted from "
        "Base.metadata.create_all -- do not 'fix' the test, fix the builder"
    )
    # A guard on the guard: `[] == []` would pass vacuously, and a builder that
    # emitted only tables (dropping every index -- including the functional
    # dedupe index invariant 2 depends on) would still satisfy the diff above
    # if the reference were somehow empty too.
    #
    # The floor excludes SQLite's implicit `sqlite_autoindex_*` rows, which the
    # query filters out: those are created for UNIQUE constraints and are not
    # in metadata.indexes, so counting them here would compare two different
    # populations.
    assert len(want) == len(Base.metadata.sorted_tables) + sum(
        len(t.indexes) for t in Base.metadata.sorted_tables
    )
    assert len(want) > 40


def test_every_ddl_statement_is_create_ddl_only():
    """The builder must never emit DML or a DROP. Cheap, and it is the shape a
    careless edit to _schema_ddl would take."""
    for statement in _schema_ddl():
        head = statement.strip().upper()
        assert head.startswith("CREATE TABLE") or head.startswith("CREATE "), statement
        assert "DROP " not in head


async def test_foreign_keys_are_enforced(db):
    """CLAUDE.md requires the PRAGMA by name: SQLite defaults FKs OFF, and two
    of the copies this fixture replaced had lost it. Without enforcement a
    cascade test passes while deleting nothing, which is invisible."""
    async with db() as s:
        s.add(User(discord_id=1, username="reiji"))
        concert = Concert(title="Live", event_id="live", created_by=1)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=WHEN))
        await s.commit()

        await s.delete(concert)
        await s.commit()
        remaining = (await s.execute(select(ConcertDay))).scalars().all()

    assert remaining == [], "ON DELETE CASCADE did not fire -- PRAGMA foreign_keys is off"


@pytest.mark.parametrize("run", [1, 2])
async def test_each_test_gets_an_empty_database(db, run):
    """Parametrised so the SECOND execution is what actually asserts isolation:
    it only sees an empty table if the first one's row did not survive."""
    async with db() as s:
        assert (await s.execute(select(User))).scalars().all() == []
        s.add(User(discord_id=run, username="leftover"))
        await s.commit()


async def test_session_and_db_address_the_same_database(session, db):
    """Several web tests seed through `session` and assert through a route that
    goes via `db`; that only works because `session` is derived from `db`."""
    session.add(User(discord_id=7, username="reiji"))
    await session.commit()

    async with db() as other:
        assert (await other.get(User, 7)) is not None
