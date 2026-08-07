"""The header nav: Home - Discover - Tags.

Three items, nothing else. The current page is marked with aria-current="page"
so it is announced, not merely coloured. Discover is public, so it appears
signed out too -- it is the only content the header can honestly offer an
anonymous visitor.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.models import User
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

USER = 6161


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


async def seed_user(db):
    async with db() as s:
        s.add(User(discord_id=USER, username="reiji"))
        await s.commit()


def nav_of(html: str) -> str:
    start = html.index('<nav class="main"')
    return html[start:html.index("</nav>", start)]


async def test_nav_renders_the_three_items_on_home(client):
    await seed_user(client.db)
    login(client)

    nav = nav_of(client.get("/").text)
    assert ">Home<" in nav
    assert ">Discover<" in nav
    assert ">Tags<" in nav


async def test_nav_renders_on_discover(client):
    await seed_user(client.db)
    login(client)

    nav = nav_of(client.get("/discover").text)
    assert ">Home<" in nav and ">Discover<" in nav and ">Tags<" in nav


async def test_the_active_item_is_marked_on_home(client):
    await seed_user(client.db)
    login(client)

    nav = nav_of(client.get("/").text)
    assert 'href="/" aria-current="page"' in nav
    assert nav.count('aria-current="page"') == 1


async def test_the_active_item_is_marked_on_discover(client):
    await seed_user(client.db)
    login(client)

    nav = nav_of(client.get("/discover").text)
    assert 'href="/discover" aria-current="page"' in nav
    assert nav.count('aria-current="page"') == 1


def test_signed_out_nav_still_offers_discover(client):
    """Discover is the one content page an anonymous visitor can reach, so
    the header must say so rather than dead-ending on the hero."""
    nav = nav_of(client.get("/").text)
    assert ">Discover<" in nav
