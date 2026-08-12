"""A character and her seiyuu are one chip in two halves, each its own follow.

The distinction is real in the data model and had nowhere to be expressed:
following 秋月律子 and following 若林直美 are different subscriptions.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 77


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


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _tag(client, name, kind, **extra):
    """Create a tag through the real route and hand back the row. The name
    trio is filled on every create because `create_tag` calls
    `require_variants(..., mandatory=True)`."""
    from sqlalchemy import select

    from app.db.models import Tag
    client.post("/tags", data={
        "name": name, "name_en": name, "name_zh": name, "kind": kind, **extra,
    })
    async with client.db() as s:
        return (await s.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()


async def _seed_group(client):
    """A GROUP with all three member shapes in one row: a plain ARTIST, a
    CHARACTER whose seiyuu is attached, and a CHARACTER with none -- exactly
    the fixture the brief asks for."""
    imai = await _tag(client, "今井麻美", "artist")
    chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    ritsuko = await _tag(client, "秋月律子", "character")
    plain = await _tag(client, "中村繪里子", "artist")
    group = await _tag(client, "765PRO ALLSTARS", "group")
    for member in (chihaya, ritsuko, plain):
        client.post(f"/tags/{group.id}/members", data={"member_tag_id": member.id})
    return group, imai, chihaya, ritsuko, plain


def _mchips(body):
    return re.findall(
        r'<span class="mchip"[^>]*data-name="([^"]*)"[^>]*>(.*?)</span>', body, re.DOTALL
    )


async def test_a_character_with_a_seiyuu_renders_one_pill_with_two_forms(client):
    """Two forms inside one .mchip -- each half posts on its own tag_id."""
    login_as(client, EDITOR_ID, "editor")
    group, imai, chihaya, ritsuko, plain = await _seed_group(client)
    body = client.get("/tags").text
    mchips = _mchips(body)
    assert len(mchips) == 1, "only the character WITH a seiyuu gets a pill"
    data_name, pill_html = mchips[0]
    assert "如月千早" in data_name and "今井麻美" in data_name, (
        "ONE data-name carrying both tags' search keys -- a per-half "
        "attribute would let filterChips hide only one half of the pill"
    )
    forms = re.findall(r'<form class="half".*?</form>', pill_html, re.DOTALL)
    assert len(forms) == 2
    assert f'name="tag_id" value="{chihaya.id}"' in pill_html
    assert f'name="tag_id" value="{imai.id}"' in pill_html
    # Fold-in from Task 2's review: the hidden inputs that make the form
    # actually work are unguarded by any test elsewhere.
    assert pill_html.count('name="notify" value="true"') == 2
    assert pill_html.count('name="tag_id"') == 2


async def test_a_character_with_no_seiyuu_falls_back_to_a_plain_chip(client):
    """The conditional merge. A one-ended pill would read as inconsistent
    styling; a plain chip reads as an ordinary performer, which is what she is.

    Mutation this must fail against: always rendering .mchip and leaving the
    second half empty."""
    login_as(client, EDITOR_ID, "editor")
    group, imai, chihaya, ritsuko, plain = await _seed_group(client)
    body = client.get("/tags").text
    mchips = _mchips(body)
    assert not any("秋月律子" in name for name, _html in mchips), (
        "a character with no seiyuu must never appear inside an .mchip, "
        "even a broken one with an empty second half"
    )
    chip_forms = re.findall(
        r'<form class="chipform"[^>]*data-name="([^"]*)"[^>]*>(.*?)</form>', body, re.DOTALL
    )
    ritsuko_chip = next((html for name, html in chip_forms if "秋月律子" in name), None)
    assert ritsuko_chip is not None, "she must still render, as a plain chip"
    assert f'name="tag_id" value="{ritsuko.id}"' in ritsuko_chip
    assert 'name="notify" value="true"' in ritsuko_chip
    assert 'name="tag_id"' in ritsuko_chip


async def test_each_half_follows_independently(client):
    """Follow the character; the seiyuu half must still offer follow, and the
    character half must offer unfollow. This is the state the whole design
    exists for."""
    login_as(client, EDITOR_ID, "editor")
    group, imai, chihaya, ritsuko, plain = await _seed_group(client)
    r = client.post(
        "/subscriptions", data={"tag_id": chihaya.id, "notify": "true", "next": "/tags"}
    )
    assert r.status_code == 303
    body = client.get("/tags").text
    mchips = _mchips(body)
    assert len(mchips) == 1
    _data_name, pill_html = mchips[0]
    forms = re.findall(r'<form class="half".*?</form>', pill_html, re.DOTALL)
    assert len(forms) == 2
    cn_form, cv_form = forms
    assert "/subscriptions/" in cn_form and "/delete" in cn_form, (
        "the character half is followed -- its form must unfollow"
    )
    assert 'class="cn on"' in cn_form
    assert 'action="/subscriptions"' in cv_form, (
        "the seiyuu half must still offer follow"
    )
    assert f'name="tag_id" value="{imai.id}"' in cv_form
    assert 'class="cv"' in cv_form and "on" not in cv_form.split('class="cv"')[1].split(">")[0]


async def test_both_halves_followed_together(client):
    """The state where `.on` must beat `.cv` by CSS source order -- only a
    browser session could see that one, so pin the markup here too."""
    login_as(client, EDITOR_ID, "editor")
    group, imai, chihaya, ritsuko, plain = await _seed_group(client)
    client.post("/subscriptions", data={"tag_id": chihaya.id, "notify": "true", "next": "/tags"})
    client.post("/subscriptions", data={"tag_id": imai.id, "notify": "true", "next": "/tags"})
    body = client.get("/tags").text
    _data_name, pill_html = _mchips(body)[0]
    assert 'class="cn on"' in pill_html and 'class="cv on"' in pill_html
