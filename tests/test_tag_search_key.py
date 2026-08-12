"""A tag must be findable by whatever name the viewer is shown.

`data-name` is filterChips()'s hook. It carried `Tag.name` (Japanese) while the
chip rendered `loc(t, "name")` (the viewer's locale), so on the live catalogue
681 of 735 tags could not be found by the name they displayed.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app, search_key

EDITOR_ID = 42


class _T:
    def __init__(self, name, name_en=None, name_zh=None):
        self.name = name
        self.name_en = name_en
        self.name_zh = name_zh


def test_search_key_joins_all_three_names_lowercased():
    key = search_key(_T("相羽あいな", "Aina Aiba", "相羽爱菜"))
    assert "相羽あいな" in key
    assert "aina aiba" in key, "an EN viewer types what the chip shows them"
    assert "相羽爱菜" in key


def test_search_key_drops_missing_names_rather_than_writing_none():
    """109 live tags have no name_zh. Jinja renders None as 'None', which would
    both add a junk token and make every one of them match a search for 'none'.

    Mutation this must fail against: joining the fields without filtering, e.g.
    f"{o.name} {o.name_en} {o.name_zh}".lower().
    """
    key = search_key(_T("蓮ノ空", "Hasunosora", None))
    assert "none" not in key
    assert key == "蓮ノ空 hasunosora"


def test_search_key_tolerates_an_object_without_the_optional_fields():
    """Not every object rendered through a chip is a Tag -- Discover's region
    links carry a bare `.name`. The helper must not raise on them."""
    class _Region:
        name = "Kanto"
    assert search_key(_Region()) == "kanto"


# ── HTTP-level: proves the helper is WIRED into the page, not just unit-tested ──


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
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


async def test_tags_page_data_name_carries_the_english_name(client):
    """The unit test above proves the helper. This proves it is WIRED -- the
    mutation being a template left on `t.name | lower`, which no unit test can
    see.

    /tags renders every tag TWICE: once as a chip (tag_chip macro) and again
    as a hidden `#tag-table-wrap` row. A page-wide `"aina aiba" in r.text`
    assertion is satisfied by either site alone, so it would pass even if the
    chip -- the actual interactive element -- regressed back to `t.name |
    lower` and only the hidden row carried the fix. Each site is asserted on
    independently so a regression in either one fails on its own.

    `data-name` lives on the chip's `.chipform` wrapper, not on the `<button>`
    inside it -- that's what `filterChips()` hides (it resolves
    `.closest(".chipform")`), and every chip is a follow `<form>` now, not a
    bare button/span.
    """
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        s.add(Tag(name="相羽あいな", name_en="Aina Aiba", name_zh="相羽爱菜",
                  kind=TagKind.ARTIST, slug="aina-aiba"))
        await s.commit()
    r = client.get("/tags")
    chips = re.findall(r'<form[^>]*class="chipform"[^>]*data-name="([^"]*)"', r.text)
    rows = re.findall(r'<tr data-name="([^"]*)"', r.text)
    assert any("aina aiba" in c for c in chips), (
        "the CHIP must be findable by the name an EN viewer is shown"
    )
    assert any("aina aiba" in row for row in rows), (
        "and so must the table row -- asserting on r.text as a whole passes "
        "when either one alone is correct, which is what made this a proxy"
    )
