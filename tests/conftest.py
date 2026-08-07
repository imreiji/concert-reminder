"""Suite-wide fixtures.

Both exist to decouple tests from ambient state -- one from each other, one
from the developer's own machine.

The first: the scheduler keeps its health cadence in a module-level counter,
and several unrelated test files call `tick()`. Without a reset, whether a
given test's tick lands on a multiple of HEALTH_EVERY_N_TICKS depends on how
many ticks ALL the earlier files happened to run -- so a test that never meant
to touch monitoring can suddenly run the real health evaluation (real
disk_usage, a real marker read, real admin ids, real Notification rows)
against its own session. That is a mystery failure waiting to happen; pin the
counter instead.

The second: `settings` is a pydantic-settings object that reads `.env` at
import, so config values leak in from whatever machine the suite runs on. See
`_pin_discord_token`.
"""

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _reset_tick_count():
    import app.scheduler.loop as loop_mod

    loop_mod._tick_count = 0
    yield
    loop_mod._tick_count = 0


@pytest.fixture(autouse=True)
def _pin_discord_token(monkeypatch):
    """Force `bot_enabled` off by default instead of inheriting it from `.env`.

    Three places used to assert that `discord_token` "defaults to ''" in
    tests. It does not: `Settings` reads `.env`, and on the owner's machine
    that file holds a real token, so `bot_enabled` was True and any route
    short-circuiting on it instead fell through into live discord.py calls
    (`/me/test-dm` died on `_MissingSentinel.is_set`, since the bot is never
    logged in). CI has no `.env`, so it passed there and failed only locally
    -- the worst shape for a test failure, because the suite disagrees with
    itself depending on who runs it.

    Pin it rather than assume it. Tests that need the bot ON set it
    themselves; test_ops_alerts.py already pins it in both directions and
    explains why plain attribute assignment works on a pydantic model.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "discord_token", "")


class RealNetworkAttempt(BaseException):
    """A test tried to open a real socket. Always a bug in the test.

    BaseException, not AssertionError, and deliberately: this is not an
    application error and no application `except Exception` may swallow it.
    It shipped as AssertionError first and `sweep_one_tag`'s `except Exception`
    -- which is correct and load-bearing, since a `2026年2月30` page raises
    ValueError from the parse -- duly caught it, turning a test that reached
    the real internet into a quiet "the sweep failed" assertion several frames
    away from the cause. Same category as KeyboardInterrupt and SystemExit:
    control flow that application code has no business handling. `pytest.raises`
    accepts BaseException, so the guard's own tests are unaffected, and nothing
    in production changes -- this class exists only under the test fixture."""


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Make a test that would actually hit the internet FAIL, loudly.

    The suite's protection against reaching ramen.events and eventernote.com
    was, until this fixture, every author remembering to stub the fetch. That
    is the worst shape of test safety: forgetting it does not fail, it
    SUCCEEDS -- on any machine with a working connection -- and fails only on
    an offline laptop or a sandboxed CI runner, i.e. it disagrees with itself
    depending on who runs it, exactly like `_pin_discord_token` above. It also
    silently makes a third party's uptime a dependency of `pytest -q`, and
    sends real traffic to sites this project scrapes by permission.

    The seam is `transport`. Every legitimate test either patches the named
    wrapper (`fetch_actor_events`, `fetch_ramen_html`) or passes an explicit
    `httpx.MockTransport`; only a `transport=None` call constructs a real
    connection pool. So the rule is exactly "no client without a transport",
    which needs no allow-list and stays correct as tests are added.

    Guarded in two places, and the second is NOT a second safety net:

    * `httpx.AsyncClient.__init__` is the CHOKEPOINT, and it alone is what
      makes the guard sound -- `fetching.fetch_html` and `web/auth.py`'s two
      OAuth calls all end there, so nothing routes around it, including code
      written later. Remove the walk below and every network path is still
      blocked.
    * The `fetch_html` walk exists for the MESSAGE. The chokepoint can only say
      "someone built a real client"; this says WHICH fetch went unstubbed, with
      the URL, which is the difference between a one-line fix and a hunt.
      `fetch_html` is re-exported by name into `app.discovery` and
      `app.web.routes.imports` (`from app.fetching import fetch_html`), so
      every module holding a reference is found by walking `sys.modules`
      rather than by keeping a list here that would rot the next time it is
      imported somewhere new. Patching `app.fetching` itself is also what makes
      the walk self-healing: a module imported AFTER this fixture runs binds
      the already-guarded function. `test_network_guard.py` pins the message,
      not just the raise, or this half would be untested by construction.
    """
    import sys

    import httpx

    from app import fetching

    real_init = httpx.AsyncClient.__init__
    real_fetch = fetching.fetch_html

    def guarded_init(self, *args, transport=None, **kwargs):
        if transport is None:
            raise RealNetworkAttempt(
                "a test constructed a real httpx.AsyncClient. Pass "
                "transport=httpx.MockTransport(...), or patch the fetch "
                "wrapper (fetch_actor_events / fetch_ramen_html) instead."
            )
        return real_init(self, *args, transport=transport, **kwargs)

    async def guarded_fetch(url, *, transport=None, **kwargs):
        if transport is None:
            raise RealNetworkAttempt(
                f"a test called fetch_html({url!r}) with no transport, which "
                "would hit the real host. Patch the caller's fetch wrapper, or "
                "pass transport=httpx.MockTransport(...)."
            )
        return await real_fetch(url, transport=transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", guarded_init)
    for module in list(sys.modules.values()):
        if getattr(module, "fetch_html", None) is real_fetch:
            monkeypatch.setattr(module, "fetch_html", guarded_fetch)


# ── The database fixtures ────────────────────────────────────────────────
#
# These replaced 81 byte-identical copies of the same fixture, spread across 79
# test files. Two things came of that duplication, and the second is the reason
# this lives here now rather than being left alone:
#
#   * test_venue_regions.py and test_web.py had quietly dropped the
#     `PRAGMA foreign_keys=ON` listener -- which the testing conventions in
#     CLAUDE.md require BY NAME, because production registers it and cascades
#     silently do not fire without it. A convention enforced by 81 hand-copies
#     is a convention with a 2-in-81 defect rate, and nothing could have
#     reported it: a missing cascade makes a test pass, not fail.
#   * There was nowhere to make a change that applied to all of them --
#     including the one below, which is worth ~50 seconds of every full run.
#
# Isolation semantics are EXACTLY as before: one fresh in-memory database per
# test, disposed after. Nothing is shared between tests. A session-scoped
# engine would be faster still, and was rejected: aiosqlite binds its
# connection to the event loop that created it, pytest-asyncio gives each test
# its own loop, and buying ~30 more seconds by introducing cross-test state and
# loop-affinity hazards into a 2,474-test suite is a bad trade.


def _schema_ddl() -> tuple[str, ...]:
    """`Base.metadata.create_all` compiled ONCE per process instead of per test.

    Nearly all of create_all's per-call cost is work whose answer never changes
    between tests: compiling 29 CREATE TABLEs and 29 CREATE INDEXes from the
    metadata, and (with the default checkfirst=True) asking SQLite whether each
    table already exists -- in a database that was created empty microseconds
    earlier and cannot possibly have any.

    Measured, per schema build in isolation:

        create_all(checkfirst=True)   32.3 ms   <- what all 81 copies did
        create_all(checkfirst=False)  16.7 ms
        precompiled statements        11.7 ms   <- this

    End to end, both figures below are BACK-TO-BACK on one machine, because
    this box's absolute timings drift by >50% across a session and a number
    quoted from an earlier run is worth nothing:

        200 identical tests, old fixture 18.6s  -> shared fixture 10.5s
        full suite, HEAD in a clean worktree 470s -> this tree 405s

    The output is verified identical to create_all's, not assumed to be:
    test_conftest_fixtures.py diffs every resulting sqlite_master row -- name
    AND stored SQL text -- including the expression-based
    `uq_reminder_queue_dedupe`, which is the one object here a naive
    reimplementation would get wrong.
    """
    global _DDL_CACHE
    if _DDL_CACHE is None:
        from sqlalchemy.dialects import sqlite
        from sqlalchemy.schema import CreateIndex, CreateTable

        from app.db.models import Base

        dialect = sqlite.dialect()
        statements: list[str] = []
        # sorted_tables is create_all's own ordering: FK dependencies first.
        for table in Base.metadata.sorted_tables:
            statements.append(str(CreateTable(table).compile(dialect=dialect)))
            for index in table.indexes:
                statements.append(str(CreateIndex(index).compile(dialect=dialect)))
        _DDL_CACHE = tuple(statements)
    return _DDL_CACHE


_DDL_CACHE: tuple[str, ...] | None = None


@pytest_asyncio.fixture()
async def db():
    """A fresh in-memory database; yields an `async_sessionmaker`.

    The dominant shape by far -- ~64 of the copies wanted the MAKER, because a
    web test's `get_session` override opens a new session per request and a
    scheduler test wants one per tick.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # StaticPool means one connection for the whole engine, so ":memory:" is a
    # single database rather than a new empty one per checkout.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _record):
        # Production registers this too (db/session.py). SQLite defaults FKs
        # OFF, and without it every ON DELETE CASCADE in the schema silently
        # does nothing -- a deletion test then passes for the wrong reason.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        for statement in _schema_ddl():
            await conn.exec_driver_sql(statement)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def session(db):
    """One open `AsyncSession` on that database -- what service-layer tests want.

    Derived from `db` rather than building its own engine, so the two fixtures
    cannot drift, and so a test may request BOTH and have them address the same
    database (several web tests seed through `session` and then assert through
    a route that goes via `db`).
    """
    async with db() as s:
        yield s
