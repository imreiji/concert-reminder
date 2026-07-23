"""Cache-busting for static assets.

`base.html` links `/static/style.css` and `/static/favicon.ico`. Without a
version marker Cloudflare serves the OLD cached file after a deploy ships new
CSS, so new markup renders unstyled (this broke three deploys). The fix
appends `?v=<content-hash>` so a changed file gets a new cache key.

These tests assert the SHAPE (`?v=` + non-empty hex) and the memoize/
missing-file behavior -- never a specific hash value, which changes whenever
style.css legitimately changes.
"""

import re

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_session
from app.web import auth, static_assets
from app.web.app import create_app
from app.web.static_assets import _hash_bytes, static_url

USER = 4242


# -- unit tests: the helper itself ------------------------------------------


def test_static_url_appends_hex_version():
    url = static_url("style.css")
    m = re.fullmatch(r"/static/style\.css\?v=([0-9a-f]+)", url)
    assert m, url
    assert m.group(1)  # non-empty hash


def test_static_url_is_stable_within_a_run():
    # Memoized: a given file is hashed at most once per process, so two calls
    # return byte-identical URLs.
    assert static_url("style.css") == static_url("style.css")


def test_static_url_is_per_file_not_a_global_version():
    # Changing one file must not change another's URL -- so the two assets
    # carry DIFFERENT hashes (their contents differ).
    css = static_url("style.css").split("?v=")[1]
    ico = static_url("favicon.ico").split("?v=")[1]
    assert css != ico


def test_static_url_missing_file_degrades_without_query():
    # A missing asset must not 500 the whole page: no `?v=`, just the bare path.
    url = static_url("does-not-exist-9f8a7b.css")
    assert url == "/static/does-not-exist-9f8a7b.css"
    assert "?v=" not in url


def test_hash_is_content_determined():
    # Different bytes -> different hash; same bytes -> same hash. Hex, non-empty.
    assert _hash_bytes(b"alpha") != _hash_bytes(b"beta")
    assert _hash_bytes(b"alpha") == _hash_bytes(b"alpha")
    assert re.fullmatch(r"[0-9a-f]+", _hash_bytes(b"alpha"))


def test_memoization_reads_the_file_at_most_once(monkeypatch):
    # Prove the read happens once: after the first call caches the hash, the
    # second call does NOT read the file again.
    calls = {"n": 0}
    real_read = static_assets.Path.read_bytes
    static_assets._hash_cache.pop("style.css", None)

    def counting_read(self):
        calls["n"] += 1
        return real_read(self)

    monkeypatch.setattr(static_assets.Path, "read_bytes", counting_read)
    first = static_url("style.css")
    second = static_url("style.css")
    assert first == second
    assert calls["n"] == 1  # hashed once, then served from cache
    static_assets._hash_cache.pop("style.css", None)


# -- integration: the rendered page carries the version ---------------------


def _client():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    import asyncio

    asyncio.new_event_loop().run_until_complete(_create_schema(engine))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()

    async def override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    c = TestClient(app, follow_redirects=False)
    c.maker = maker
    return c


async def _create_schema(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _login(client, monkeypatch):
    async def fake_exchange(code):
        return "tok"

    async def fake_identity(token):
        return {"id": str(USER), "username": "reiji", "global_name": "reiji", "avatar": None}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def test_rendered_page_versions_the_stylesheet_and_favicon(monkeypatch):
    client = _client()
    _login(client, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    # base.html links both with a content-hash version param now.
    assert re.search(r"/static/style\.css\?v=[0-9a-f]+", r.text), r.text[:2000]
    assert re.search(r"/static/favicon\.ico\?v=[0-9a-f]+", r.text)
    # The old bare, unversioned links are gone (the stale-cache bug).
    assert 'href="/static/style.css"' not in r.text
    assert 'href="/static/favicon.ico"' not in r.text
