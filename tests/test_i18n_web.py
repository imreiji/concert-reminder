"""Locale resolution over HTTP: cookie > Accept-Language > en; POST /language.

Fixtures mirror tests/test_home.py's sync TestClient shape (an app over an
in-memory engine); the brief's async `client`/`logged_in_client`/`session`
placeholders are adapted to that here.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, User
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

USER = 4242


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── locale resolution: cookie > Accept-Language > en ─────────────────────


def test_accept_language_ja_serves_ja_lang_attr(client):
    r = client.get("/", headers={"Accept-Language": "ja,en;q=0.8"})
    assert '<html lang="ja"' in r.text


def test_cookie_overrides_accept_language(client):
    client.cookies.set("lang", "zh")
    r = client.get("/", headers={"Accept-Language": "ja"})
    assert '<html lang="zh"' in r.text


def test_default_is_en(client):
    r = client.get("/")
    assert '<html lang="en"' in r.text


def test_bad_cookie_falls_back_to_header(client):
    client.cookies.set("lang", "xx")
    r = client.get("/", headers={"Accept-Language": "ja"})
    assert '<html lang="ja"' in r.text


# ── POST /language ───────────────────────────────────────────────────────


def test_post_language_sets_cookie_and_redirects(client):
    r = client.post("/language", data={"language": "ja", "next": "/discover"})
    assert r.status_code == 303
    assert r.headers["location"] == "/discover"
    assert "lang=ja" in r.headers.get("set-cookie", "")


def test_post_language_rejects_unknown(client):
    r = client.post("/language", data={"language": "fr", "next": "/"})
    assert r.status_code == 422


def test_post_language_guards_next(client):
    r = client.post("/language", data={"language": "ja", "next": "https://evil.example"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    r = client.post("/language", data={"language": "ja", "next": "//evil.example"})
    assert r.headers["location"] == "/"


async def test_post_language_persists_for_signed_in_user(client):
    login(client)
    r = client.post("/language", data={"language": "zh", "next": "/preferences"})
    assert r.status_code == 303
    async with client.db() as s:
        user = await s.get(User, USER)
    assert user.language == "zh"


# ── OAuth login cookie sync ──────────────────────────────────────────────


def test_login_seeds_language_from_cookie_for_new_user(client):
    client.cookies.set("lang", "ja")
    login(client)
    # New user: the cookie seeds the column at creation.
    r = client.get("/")
    assert '<html lang="ja"' in r.text


async def test_login_sets_cookie_from_column_for_existing_user(client):
    # Pre-existing user whose account language is zh.
    async with client.db() as s:
        s.add(User(discord_id=USER, username="reiji", language="zh"))
        await s.commit()
    # Log in with no lang cookie: the callback must set the cookie from the
    # column, so this browser now matches the account.
    login(client)
    r = client.get("/")
    assert '<html lang="zh"' in r.text


# ── Jinja env wiring: i18n extension + locale-aware globals ─────────────


async def test_template_gettext_available(client):
    # base.html renders _( ) calls once templates convert; until then, prove
    # the machinery: the env has the extension and newstyle callables.
    from app.web.app import templates

    assert "jinja2.ext.InternationalizationExtension" in templates.env.extensions


async def test_label_globals_translate(client):
    from app import i18n
    from app.domain.types import Anchor
    from app.web.app import templates

    i18n._catalog_cache["ja"] = i18n._translations_from_po_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n'
        '"Plural-Forms: nplurals=1; plural=0;\\n"\n\n'
        'msgid "opens"\nmsgstr "受付開始"\n',
        "ja",
    )
    i18n.set_locale("ja")
    try:
        assert templates.env.globals["deadline_label"](Anchor.OPENS) == "受付開始"
    finally:
        i18n.set_locale("en")
        i18n.reset_catalog_cache()
    assert templates.env.globals["deadline_label"](Anchor.OPENS) == "opens"


async def test_date_globals_follow_active_locale(client):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import i18n
    from app.web.app import templates

    dt = datetime(2026, 8, 1, 10, 0, tzinfo=ZoneInfo("UTC"))
    i18n.set_locale("ja")
    try:
        date_line, _t = templates.env.globals["dual_lines"](dt, "UTC")
        assert date_line == "8月1日(土)"
    finally:
        i18n.set_locale("en")
