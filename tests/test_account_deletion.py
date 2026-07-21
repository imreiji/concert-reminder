"""Self-serve account deletion (Task 9).

POST /me/delete is GDPR erasure wired to the web: it runs the existing
`service.delete_user` for the AUTHENTICATED CALLER ONLY (id from the session,
never request input), revokes the session the same way logout does, and lands
the visitor on the signed-out home. The catalogue the user authored survives
with an anonymised author -- that is delete_user's SET NULL guarantee, pinned
here at the route boundary. The Account danger card gates the POST behind a
heavy confirmation naming the specific loss (the invariant-8 opt-out weight).
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, User, WebSession
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

USER_A = 5151
USER_B = 6262


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def seed_user(db, discord_id: int, name: str, with_concert: bool = False) -> SimpleNamespace:
    async with db() as s:
        s.add(User(discord_id=discord_id, username=name))
        await s.flush()
        concert_id = None
        if with_concert:
            c = Concert(
                title="Authored Show", event_id=f"auth-{discord_id}", created_by=discord_id
            )
            s.add(c)
            await s.flush()
            concert_id = c.id
        await s.commit()
        return SimpleNamespace(concert_id=concert_id)


async def test_delete_erases_caller_and_revokes_session(client):
    """The happy path: a logged-in POST erases the user, revokes the session
    (a later request no longer resolves), and redirects to the signed-out home."""
    await seed_user(client.db, USER_A, "reiji")
    login_as(client, USER_A, "reiji")
    assert client.get("/preferences").status_code == 200  # session resolves before

    r = client.post("/me/delete")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/")

    async with client.db() as s:
        assert await s.get(User, USER_A) is None  # user row gone
        rows = list((await s.execute(select(WebSession))).scalars())
        assert rows == []  # session cascaded away: no live token remains

    # The session no longer resolves -- require_user now sends the caller home.
    assert client.get("/preferences").status_code == 303


async def test_authored_concert_survives_anonymised(client):
    """delete_user's SET NULL guarantee, asserted at the route: the shared
    catalogue this user authored stays, with the author field blanked."""
    seeded = await seed_user(client.db, USER_A, "reiji", with_concert=True)
    login_as(client, USER_A, "reiji")

    r = client.post("/me/delete")
    assert r.status_code == 303

    async with client.db() as s:
        assert await s.get(User, USER_A) is None
        concert = await s.get(Concert, seeded.concert_id)
        assert concert is not None  # community content is not deleted with the user
        assert concert.created_by is None  # author anonymised


async def test_delete_ignores_user_id_from_request(client):
    """Scoped to the caller ONLY: an attacker-style payload naming another
    user is ignored -- the id comes from the session, never the form."""
    await seed_user(client.db, USER_A, "reiji")
    await seed_user(client.db, USER_B, "other")
    login_as(client, USER_A, "reiji")

    # Try to steer the erasure at USER_B via every plausible field name.
    r = client.post(
        "/me/delete",
        data={"discord_id": str(USER_B), "user_id": str(USER_B), "id": str(USER_B)},
    )
    assert r.status_code == 303

    async with client.db() as s:
        assert await s.get(User, USER_A) is None  # the caller was erased
        assert await s.get(User, USER_B) is not None  # the form-named victim untouched


async def test_delete_requires_login(client):
    """require_user: an anonymous POST is redirected home, never a silent no-op."""
    await seed_user(client.db, USER_A, "reiji")
    r = client.post("/me/delete")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    async with client.db() as s:
        assert await s.get(User, USER_A) is not None  # nothing erased


async def test_account_card_gates_delete_behind_heavy_confirmation(client):
    """The Account danger card renders a real delete control behind a heavy
    confirmation naming the specific loss (mirrors privacy.html's wording)."""
    await seed_user(client.db, USER_A, "reiji")
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert 'action="/me/delete"' in r.text  # the real POST target
    # A deliberate second action: the POST lives inside a confirmation dialog,
    # not a one-click button.
    assert 'id="deleteAccountConfirm"' in r.text
    # Loss-naming copy: what goes vs what stays.
    assert "Your reminders, subscriptions, and preferences are deleted." in r.text
    assert "Concerts and tags you authored stay, with your name removed." in r.text
