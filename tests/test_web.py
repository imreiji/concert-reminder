"""Web skeleton smoke tests."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_session
from app.scheduler import heartbeat
from app.web.app import create_app


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_healthz(client, monkeypatch):
    """The freshness this asserts must be pinned, not inherited from how long
    the suite has been running. `heartbeat.status()` measures against
    MAX_AGE_SECONDS = 180 and nothing beats during tests, so once the whole
    suite takes longer than three minutes -- it does on Windows -- the app is
    legitimately unhealthy by the time this executes and the assert flips.
    A race against our own runtime, and it went red only on the slower
    machine."""
    monkeypatch.setattr(heartbeat, "last_tick", datetime.now(UTC))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_index_renders_landing_for_anonymous(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "dekimasen.app" in r.text
    assert "Sign in with Discord" in r.text


def test_robots_txt_blocks_query_stringed_discover_only(client):
    """The 2026-08-04 crawl outage: Discover's ?tag= filter URL space is
    combinatorially infinite and every hit was a full render. robots.txt
    disallows the query-stringed URLs by literal prefix ('?' is not a
    metacharacter in any robots grammar) while the bare catalogue page --
    and everything else -- stays crawlable."""
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = [line.strip() for line in r.text.splitlines()]
    assert "User-agent: *" in lines
    assert "Disallow: /discover?" in lines
    # The bare page must stay crawlable: no broader disallow may appear.
    assert "Disallow: /discover" not in lines
    assert "Disallow: /" not in lines
