"""Taxonomy tests for the GOODS_SALE round kind: the new enum member,
ITEM_SALE_KINDS, ingest keyword mapping, and label/emoji coverage. Also
covers Round.required_item_round_id, the self-FK an item-sale round's
dependents point at (SET NULL on the target's delete)."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, ReminderRule, Round, User
from app.db.service import concert_round_rows, due_reminders, ensure_user, sync_rule
from app.db.session import get_session
from app.domain.ingest import _guess_kind
from app.domain.types import ITEM_SALE_KINDS, Anchor, RoundKind
from app.domain.yaml_import import parse_draft
from app.i18n import reset_catalog_cache, set_locale
from app.web import auth
from app.web.app import create_app
from app.web.routes.concerts import RoundRequiresError, resolve_round_requires

EDITOR_ID = 42


def test_goods_sale_is_a_round_kind():
    assert RoundKind.GOODS_SALE.value == "goods_sale"


def test_item_sale_kinds_are_the_two_item_kinds():
    assert ITEM_SALE_KINDS == {RoundKind.ELIGIBILITY_ITEM_SALE, RoundKind.GOODS_SALE}


def test_guess_kind_maps_goods_keywords():
    assert _guess_kind("グッズ販売") is RoundKind.GOODS_SALE
    assert _guess_kind("Tour Goods Pre-order") is RoundKind.GOODS_SALE
    assert _guess_kind("会場物販") is RoundKind.GOODS_SALE
    # The serial-code sale stays what it was:
    assert _guess_kind("シリアル対象CD発売") is not RoundKind.GOODS_SALE


def test_label_and_emoji_cover_every_kind():
    from app.bot.messages import KIND_EMOJI
    from app.db.service import LABEL_BY_ROUND_KIND

    for kind in RoundKind:
        assert kind in LABEL_BY_ROUND_KIND
        assert kind.value in KIND_EMOJI


def test_requires_script_item_kinds_mirrors_item_sale_kinds():
    """_requires_select_script.html's JS ITEM_KINDS literal is the client-side
    mirror of ITEM_SALE_KINDS (domain/types.py) -- the two other tests above
    only assert the JS list is PRESENT on the rendered page, never that it is
    still EQUAL to the Python set, so a kind added to ITEM_SALE_KINDS without
    updating this template would silently desync the requires <select> and
    every render test would keep passing."""
    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "app" / "web" / "templates" / "_requires_select_script.html"
    )
    text = path.read_text(encoding="utf-8")
    match = re.search(r"const ITEM_KINDS = (\[[^\]]*\]);", text)
    assert match, "ITEM_KINDS literal not found in _requires_select_script.html"
    js_kinds = json.loads(match.group(1))
    assert js_kinds == sorted(kind.value for kind in ITEM_SALE_KINDS)


async def test_required_item_round_set_null_on_target_delete(session):
    # Build a concert with an item-sale round and a lottery round that
    # requires it, straight through the models (write-boundary validation is
    # a route concern, Tasks 3-5).
    await ensure_user(session, 1, "reiji")
    concert = Concert(title="t", event_id="t-1", created_by=1)
    session.add(concert)
    await session.flush()
    item = Round(concert_id=concert.id, kind=RoundKind.GOODS_SALE, label="グッズ")
    session.add(item)
    await session.flush()
    lottery = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        required_item_round_id=item.id,
    )
    session.add(lottery)
    await session.flush()

    await session.delete(item)
    await session.flush()
    await session.refresh(lottery)
    assert lottery.required_item_round_id is None


# ── The resolver (unit) ───────────────────────────────────────────────────


def test_resolver_resolves_key_and_id_tokens():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    keys = {"r0": 1}
    assert resolve_round_requires("r0", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("1", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("", keys, kinds, self_id=2) is None


def test_resolver_rejects_missing_wrong_kind_and_self():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("99", {}, kinds, self_id=2)   # not on this concert
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("2", {}, kinds, self_id=3)    # target not an item kind
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("1", {}, kinds, self_id=1)    # itself


# ── create_concert wiring (HTTP) ──────────────────────────────────────────
#
# Same logged-in-editor client shape as tests/test_crud.py: a fresh in-memory
# DB per test, get_session dependency-overridden, login simulated by
# monkeypatching the auth module's Discord calls.


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


async def test_create_concert_binds_requires_by_round_key(client):
    # Two rounds in one submit: row 0 is the goods sale (key g1), row 1
    # requires it by key. Assert the saved lottery round points at the saved
    # goods round's real id.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title_en": "Requires Test", "title_zh": "Requires Test",
            "title": "Requires Test", "event_id": "requires-test",
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_key": ["g1", "x2"],
            "round_requires": ["", "g1"],
        },
    )
    assert r.status_code == 303, r.text

    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "requires-test")
        )).scalar_one()
        rounds = (await s.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars().all()
    goods = next(r for r in rounds if r.label == "Goods sale")
    lottery = next(r for r in rounds if r.label == "Lottery")
    assert lottery.required_item_round_id == goods.id


async def test_create_concert_422_on_wrong_kind_target(client):
    # round_requires names the OTHER lottery round -> 422, nothing persisted.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title_en": "Bad Requires", "title_zh": "Bad Requires",
            "title": "Bad Requires", "event_id": "bad-requires",
            "round_label": ["L1", "L2"],
            "round_label_en": ["L1", "L2"],
            "round_label_zh": ["L1", "L2"],
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_key": ["k1", "k2"],
            "round_requires": ["", "k1"],  # L2 requires L1, a LOTTERY_ROUND
        },
    )
    assert r.status_code == 422, r.text

    async with client.db() as s:
        result = await s.execute(
            select(Concert).where(Concert.event_id == "bad-requires")
        )
        assert result.scalar_one_or_none() is None


# ── edit_concert wiring (HTTP) ────────────────────────────────────────────
#
# Same client/login_as/db trio as the create_concert tests above --
# test_round_requires.py has no client_editor fixture; login_as(client, ...)
# is how every test in this file signs in as an editor.


def _create_goods_and_lottery(
    client,
    event_id: str,
    *,
    goods_label: str = "Goods sale",
    lottery_label: str = "Lottery",
) -> tuple[int, int]:
    """Seeds one concert with a GOODS_SALE round and a LOTTERY_ROUND that
    requires it (bound by round_key, exactly like
    test_create_concert_binds_requires_by_round_key). Returns event_id, which
    the tests below re-fetch via _rounds_by_label to get real round ids.

    The labels are overridable because "Goods sale" is ALSO the GOODS_SALE
    kind's own display label: a render test asserting that string proves
    nothing about the requires line, since the kind label puts it on the page
    regardless."""
    r = client.post(
        "/concerts",
        data={
            "title_en": "Requires Edit", "title_zh": "Requires Edit",
            "title": "Requires Edit", "event_id": event_id,
            "round_label": [goods_label, lottery_label],
            "round_label_en": [goods_label, lottery_label],
            "round_label_zh": [goods_label, lottery_label],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_key": ["g1", "x2"],
            "round_requires": ["", "g1"],
        },
    )
    assert r.status_code == 303, r.text
    return event_id


async def _rounds_by_label(client, event_id: str) -> dict[str, Round]:
    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == event_id)
        )).scalar_one()
        rounds = (await s.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars().all()
    return {r.label: r for r in rounds}


async def test_edit_preserves_requires_when_field_omitted(client):
    # Seed via create (same shape as test_create_concert_binds_requires_by_
    # round_key). Then POST an edit whose form omits round_key/round_requires
    # entirely (an old browser) -- both existing rounds are re-posted by id
    # with their other fields unchanged. Assert the link survives.
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-edit-omit")
    by_label = await _rounds_by_label(client, "requires-edit-omit")
    goods, lottery = by_label["Goods sale"], by_label["Lottery"]
    assert lottery.required_item_round_id == goods.id

    r = client.post(
        "/concerts/requires-edit-omit/edit",
        data={
            "title": "Requires Edit", "event_id": "requires-edit-omit",
            "round_id": [str(goods.id), str(lottery.id)],
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            # round_key / round_requires deliberately absent.
        },
    )
    assert r.status_code == 303, r.text

    by_label = await _rounds_by_label(client, "requires-edit-omit")
    assert by_label["Lottery"].required_item_round_id == goods.id


async def test_edit_clears_requires_on_empty_value(client):
    # POST an edit with round_requires=["", ""] -- present, not omitted --
    # so the lottery row's own blank token clears its link instead of being
    # read back from the existing row.
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-edit-clear")
    by_label = await _rounds_by_label(client, "requires-edit-clear")
    goods, lottery = by_label["Goods sale"], by_label["Lottery"]
    assert lottery.required_item_round_id == goods.id

    r = client.post(
        "/concerts/requires-edit-clear/edit",
        data={
            "title": "Requires Edit", "event_id": "requires-edit-clear",
            "round_id": [str(goods.id), str(lottery.id)],
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_requires": ["", ""],
        },
    )
    assert r.status_code == 303, r.text

    by_label = await _rounds_by_label(client, "requires-edit-clear")
    assert by_label["Lottery"].required_item_round_id is None


async def test_edit_drops_preserved_link_when_target_deleted(client):
    # Omit round_requires (preserved=True for both surviving rows) AND drop
    # the goods round's row from the submit entirely -- the same as an
    # editor deleting it. The preserved link resolves against surviving
    # rounds only, fails, and drops to None -- 303, never a 422 for a value
    # the submitter never sent.
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-edit-deleted")
    by_label = await _rounds_by_label(client, "requires-edit-deleted")
    goods, lottery = by_label["Goods sale"], by_label["Lottery"]
    assert lottery.required_item_round_id == goods.id

    r = client.post(
        "/concerts/requires-edit-deleted/edit",
        data={
            "title": "Requires Edit", "event_id": "requires-edit-deleted",
            # Only the lottery round is re-posted -- goods is gone.
            "round_id": [str(lottery.id)],
            "round_label": ["Lottery"],
            "round_label_en": ["Lottery"],
            "round_label_zh": ["Lottery"],
            "round_kind": ["lottery_round"],
            "round_opens_at": ["2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59"],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
            "round_notes": [""],
            "round_legs": [""],
            # round_key / round_requires deliberately absent.
        },
    )
    assert r.status_code == 303, r.text

    by_label = await _rounds_by_label(client, "requires-edit-deleted")
    assert "Goods sale" not in by_label  # deleted, as submitted
    assert by_label["Lottery"].required_item_round_id is None


async def test_import_commit_binds_requires_by_round_key(client):
    # Same two-round shape as test_create_concert_binds_requires_by_round_key,
    # but through /concerts/import/commit -- all rounds are new here too, so
    # round_key/round_requires wiring is identical, just via the import route.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Import Requires Test", "title_en": "Import Requires Test",
            "title_zh": "Import Requires Test", "event_id": "import-requires-test",
            "day_label": ["Day 1"], "day_label_en": ["Day 1"], "day_label_zh": ["Day 1"],
            "day_starts_at": ["2099-06-01T18:00"],
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_key": ["g1", "x2"],
            "round_requires": ["", "g1"],
        },
    )
    assert r.status_code == 303, r.text

    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "import-requires-test")
        )).scalar_one()
        rounds = (await s.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars().all()
    goods = next(r for r in rounds if r.label == "Goods sale")
    lottery = next(r for r in rounds if r.label == "Lottery")
    assert lottery.required_item_round_id == goods.id


async def test_import_commit_422_on_wrong_kind_target(client):
    # round_requires names the OTHER lottery round -> 422, nothing persisted.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Import Bad Requires", "title_en": "Import Bad Requires",
            "title_zh": "Import Bad Requires", "event_id": "import-bad-requires",
            "day_label": ["Day 1"], "day_label_en": ["Day 1"], "day_label_zh": ["Day 1"],
            "day_starts_at": ["2099-06-01T18:00"],
            "round_label": ["L1", "L2"],
            "round_label_en": ["L1", "L2"],
            "round_label_zh": ["L1", "L2"],
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_key": ["k1", "k2"],
            "round_requires": ["", "k1"],  # L2 requires L1, a LOTTERY_ROUND
        },
    )
    assert r.status_code == 422, r.text

    async with client.db() as s:
        result = await s.execute(
            select(Concert).where(Concert.event_id == "import-bad-requires")
        )
        assert result.scalar_one_or_none() is None


async def test_edit_422_when_posted_target_rekinded(client):
    # Explicitly post round_requires pointing at the goods round, but this
    # same submit re-kinds it from goods_sale to lottery_round -> 422.
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-edit-rekind")
    by_label = await _rounds_by_label(client, "requires-edit-rekind")
    goods, lottery = by_label["Goods sale"], by_label["Lottery"]
    assert lottery.required_item_round_id == goods.id

    r = client.post(
        "/concerts/requires-edit-rekind/edit",
        data={
            "title": "Requires Edit", "event_id": "requires-edit-rekind",
            "round_id": [str(goods.id), str(lottery.id)],
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            # The goods round is re-kinded away from an item-sale kind here.
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            # Explicitly posted (present, non-omitted array) -- the lottery
            # row's token names the now-re-kinded goods round by real id.
            "round_requires": ["", str(goods.id)],
        },
    )
    assert r.status_code == 422, r.text

    by_label = await _rounds_by_label(client, "requires-edit-rekind")
    # Nothing committed -- both rounds keep their pre-submit state.
    assert by_label["Goods sale"].kind is RoundKind.GOODS_SALE
    assert by_label["Lottery"].required_item_round_id == goods.id


# ── Editor UI render tests (Task 6) ───────────────────────────────────────
#
# test_round_requires.py has no client_editor fixture -- login_as(client, ...)
# is how every test in this file signs in as an editor.


async def test_edit_page_renders_requires_select(client):
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-edit-render")
    by_label = await _rounds_by_label(client, "requires-edit-render")
    goods, lottery = by_label["Goods sale"], by_label["Lottery"]

    r = client.get("/concerts/requires-edit-render/edit")
    assert r.status_code == 200
    assert 'name="round_requires"' in r.text
    # The load-bearing arg: round_key renders each SAVED row's real id, not
    # blank -- if this regresses, keyOf() mints "nk1" client-side, the
    # server-rendered selection silently resets, and the next save drops an
    # existing requires-link with no error anywhere.
    assert f'name="round_key" value="{goods.id}"' in r.text
    assert f'name="round_key" value="{lottery.id}"' in r.text
    # The goods round's label appears inside a <select selected> option.
    assert 'selected>Goods sale</option>' in r.text
    # An item-sale round exists on this concert, so neither RENDERED row's
    # requires box is hidden (the blank <template> clone at the bottom is
    # hidden regardless, so scope the check to before it).
    rendered_rows = r.text.split('<template id="round-row-template">')[0]
    assert 'data-requires-box hidden' not in rendered_rows
    # The client script actually shipped on this page.
    assert 'ITEM_KINDS = ["eligibility_item_sale", "goods_sale"]' in r.text


async def test_new_page_renders_requires_field(client):
    login_as(client, EDITOR_ID, "reiji")

    r = client.get("/concerts/new")
    assert r.status_code == 200
    assert 'name="round_requires"' in r.text
    assert 'name="round_key"' in r.text
    # No item-sale round exists on a brand-new concert, so the box starts
    # hidden (contrast the edit-page test above, seeded with a goods round).
    assert 'data-requires-box hidden' in r.text
    # The client script actually shipped on this page.
    assert 'ITEM_KINDS = ["eligibility_item_sale", "goods_sale"]' in r.text


async def test_import_preview_renders_requires_script(client):
    # A minimal pasted draft (title is the only required key) is enough to
    # render import_preview.html and confirm the script include made it onto
    # this third surface too.
    login_as(client, EDITOR_ID, "reiji")

    r = client.post("/concerts/import/draft", data={"draft": "title: Requires Script Test"})
    assert r.status_code == 200
    assert 'ITEM_KINDS = ["eligibility_item_sale", "goods_sale"]' in r.text


# ── Draft vocabulary round-trip (Task 7) ──────────────────────────────────
#
# export -> parse -> preview -> commit, the `requires:` link riding a draft
# by ja LABEL (never an id -- a draft has no ids) exactly the way
# `applies_to` already rides a leg by label.


async def test_draft_preview_resolves_requires_to_keys(client):
    # Two rounds in one draft: the goods sale first (becomes round_key "r0"),
    # the lottery second ("r1") naming the goods round by its ja label. The
    # preview must resolve that label to "r0" and pre-select it in the
    # lottery row's requires <select>.
    login_as(client, EDITOR_ID, "reiji")
    draft = (
        "title: Requires Draft Test\n"
        "rounds:\n"
        "  - label: グッズ販売\n"
        "    kind: goods_sale\n"
        "  - label: 最速先行\n"
        "    kind: lottery_round\n"
        "    requires: グッズ販売\n"
    )
    r = client.post("/concerts/import/draft", data={"draft": draft})
    assert r.status_code == 200
    assert 'name="round_key" value="r0"' in r.text
    assert 'name="round_key" value="r1"' in r.text
    assert '<option value="r0" selected>グッズ販売</option>' in r.text


async def test_draft_preview_warns_on_unmatched_requires(client):
    # requires: names a label no round in this draft carries -- the link is
    # dropped (nothing to pre-select) and a warning says so, but the page
    # still renders (a bad requires reference must not cost the whole
    # preview, same governing rule as every other warn-and-continue path
    # in this parser).
    login_as(client, EDITOR_ID, "reiji")
    draft = (
        "title: Requires Draft Test\n"
        "rounds:\n"
        "  - label: 最速先行\n"
        "    kind: lottery_round\n"
        "    requires: ノーグッズ\n"
    )
    r = client.post("/concerts/import/draft", data={"draft": draft})
    assert r.status_code == 200
    assert "no other round labelled" in r.text
    assert "&#39;ノーグッズ&#39;" in r.text  # Jinja-escaped repr() quotes


async def test_draft_preview_self_reference_warns_and_drops_selection(client):
    # An item-kind round whose requires: names ITSELF by label -- there is no
    # OTHER round with that label, so this must warn (the generic "no other
    # round labelled" message: self-reference is not a distinct warning
    # branch, same as before this fix) and select nothing. Also exercises
    # the round_key self-exclusion fix: this round's own option (r0) must
    # not even be OFFERED in its own card's <select>, let alone selected.
    login_as(client, EDITOR_ID, "reiji")
    draft = (
        "title: Requires Draft Test\n"
        "rounds:\n"
        "  - label: グッズ販売\n"
        "    kind: goods_sale\n"
        "    requires: グッズ販売\n"
    )
    r = client.post("/concerts/import/draft", data={"draft": draft})
    assert r.status_code == 200
    assert "no other round labelled" in r.text
    assert "&#39;グッズ販売&#39;" in r.text
    # r0 is the only round on the page, so ANY "<option value="r0"" would
    # have to be this round listing itself -- there must be none.
    assert '<option value="r0"' not in r.text


async def test_draft_preview_warns_on_non_item_target(client):
    # requires: names a label that IS a round in this draft, but that round
    # (最速先行, r0) is a LOTTERY_ROUND, not an item/goods-sale kind -- the
    # select never offers a non-item round as a target (server- or
    # client-side), so this must get its own warning distinct from "no such
    # round", reusing resolve_round_requires' "is not an item or goods sale"
    # wording, and select nothing. A real item round (グッズ販売, r2) is also
    # in this draft so requires_options is non-empty -- proving the drop is
    # a deliberate refusal, not just an empty options list with nothing to
    # select in the first place.
    login_as(client, EDITOR_ID, "reiji")
    draft = (
        "title: Requires Draft Test\n"
        "rounds:\n"
        "  - label: 最速先行\n"
        "    kind: lottery_round\n"
        "  - label: 二次先行\n"
        "    kind: lottery_round\n"
        "    requires: 最速先行\n"
        "  - label: グッズ販売\n"
        "    kind: goods_sale\n"
    )
    r = client.post("/concerts/import/draft", data={"draft": draft})
    assert r.status_code == 200
    assert "is not an item or goods sale" in r.text
    assert "&#39;最速先行&#39;" in r.text
    assert "no other round labelled" not in r.text
    # The real item round (r2) is offered as an option somewhere on the
    # page, but never pre-selected -- the rejected link left every round's
    # requires_key unresolved.
    assert 'value="r2"' in r.text
    assert '<option value="r2" selected>' not in r.text


async def test_export_round_trip_keeps_requires(client):
    # Create a linked concert (same shape _create_goods_and_lottery seeds for
    # the create/edit tests above), fetch its YAML export, and re-parse it --
    # the lottery round's requires_label must name the goods round's ja label.
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(client, "requires-export-roundtrip")

    r = client.get("/concerts/requires-export-roundtrip/export.yaml")
    assert r.status_code == 200

    parsed = parse_draft(r.text)
    lottery = next(rr for rr in parsed.rounds if rr.label == "Lottery")
    assert lottery.requires_label == "Goods sale"


# ── Concert page requires/needed-for lines (Task 8) ───────────────────────

NOW = datetime(2026, 6, 1, tzinfo=UTC)


async def _seed_goods_and_lottery_rows(session, *, goods_closes: datetime | None):
    """One concert, no legs (falls into concert_round_rows' dateless list):
    a GOODS_SALE round and a LOTTERY_ROUND that requires it."""
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Requires Rows", event_id="requires-rows-test", created_by=42)
    session.add(concert)
    await session.flush()
    goods = Round(
        concert_id=concert.id, kind=RoundKind.GOODS_SALE, label="グッズ販売",
        closes_at_utc=goods_closes,
    )
    session.add(goods)
    await session.flush()
    lottery = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        required_item_round_id=goods.id,
    )
    session.add(lottery)
    await session.flush()
    await session.commit()
    return concert, goods, lottery


async def test_round_rows_carry_requires_and_needed_for(session):
    concert, goods, lottery = await _seed_goods_and_lottery_rows(
        session, goods_closes=NOW + timedelta(days=10),
    )
    _legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)

    goods_row = next(row for row in fallback if row.round_.id == goods.id)
    lottery_row = next(row for row in fallback if row.round_.id == lottery.id)

    assert lottery_row.requires_label == goods.label
    assert lottery_row.requires_closes_at_utc == goods.closes_at_utc
    assert goods_row.needed_for_labels == (lottery.label,)


async def test_requires_close_time_hidden_once_sale_over(session):
    concert, goods, lottery = await _seed_goods_and_lottery_rows(
        session, goods_closes=NOW - timedelta(days=1),
    )
    _legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)

    lottery_row = next(row for row in fallback if row.round_.id == lottery.id)
    assert lottery_row.requires_label == goods.label
    assert lottery_row.requires_closes_at_utc is None


async def test_concert_page_renders_requires_line(client):
    """Both directions of the link, rendered. The labels are deliberately NOT
    "Goods sale"/"Lottery": those are the two kinds' own display labels, so a
    page that had dropped the requires line entirely would still contain them
    and the assertion would pass for the wrong reason."""
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(
        client, "requires-page-render",
        goods_label="Tour merch preorder", lottery_label="Fastest presale draw",
    )

    r = client.get("/concerts/requires-page-render")
    assert r.status_code == 200
    # Forward line, on the round that needs the item.
    assert "Requires: Tour merch preorder" in r.text
    # The sale is still open (it closes in 2099), so its close time rides
    # along -- the actionable half of the line.
    assert "sale ends" in r.text
    # Reverse line, on the item sale itself.
    assert "Needed for: Fastest presale draw" in r.text


async def test_concert_page_drops_sale_ends_once_the_sale_has_closed(client):
    """Same page, sale in the past: the label stays (what you needed) and the
    close time goes (a closed sale's deadline is history, not an action)."""
    login_as(client, EDITOR_ID, "reiji")
    _create_goods_and_lottery(
        client, "requires-page-closed",
        goods_label="Tour merch preorder", lottery_label="Fastest presale draw",
    )
    by_label = await _rounds_by_label(client, "requires-page-closed")
    async with client.db() as s:
        goods = await s.get(Round, by_label["Tour merch preorder"].id)
        # Both ends moved into the past, not just the close: a sale that
        # opened in 2099 and closed in 2020 is impossible, and a fixture that
        # only holds together because nothing currently reads `opens_at_utc`
        # is a proxy waiting to happen.
        goods.opens_at_utc = datetime(2019, 12, 1, tzinfo=UTC)
        goods.closes_at_utc = datetime(2020, 1, 1, tzinfo=UTC)
        await s.commit()

    r = client.get("/concerts/requires-page-closed")
    assert r.status_code == 200
    assert "Requires: Tour merch preorder" in r.text
    assert "sale ends" not in r.text


# ── DueReminder.requires_label / requires_closes_at_utc (Task 9) ──────────


async def test_due_reminder_carries_requires(session):
    """Linked rounds + a rule whose queue row is due on the lottery round:
    due_reminders() must carry the goods round's label in the RECIPIENT's
    language -- not the ambient locale, which is set to something else here
    on purpose -- and its close time only while that sale is still open."""
    await ensure_user(session, 42, "reiji")
    user = await session.get(User, 42)
    user.language = "en"
    await session.flush()

    concert = Concert(title="Requires DM", event_id="requires-dm-test", created_by=42)
    session.add(concert)
    await session.flush()
    goods_closes = NOW + timedelta(days=10)
    goods = Round(
        concert_id=concert.id, kind=RoundKind.GOODS_SALE, label="グッズ",
        label_en="Goods sale", closes_at_utc=goods_closes,
    )
    session.add(goods)
    await session.flush()
    lottery = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        required_item_round_id=goods.id, closes_at_utc=NOW + timedelta(hours=1),
    )
    session.add(lottery)
    await session.flush()
    rule = ReminderRule(
        user_id=42, round_id=lottery.id, anchor=Anchor.CLOSES, offset_days=0
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    set_locale("ja")  # ambient locale deliberately NOT the recipient's language
    try:
        (due,) = await due_reminders(session, NOW + timedelta(hours=1))
    finally:
        set_locale("en")
        reset_catalog_cache()
    assert due.requires_label == "Goods sale"
    assert due.requires_closes_at_utc == goods_closes

    # Once the goods sale itself has closed, the time drops -- a closed
    # sale's deadline is history, not something to act on -- but the label
    # (what you needed) stays.
    (due_after,) = await due_reminders(session, goods_closes + timedelta(days=1))
    assert due_after.requires_label == "Goods sale"
    assert due_after.requires_closes_at_utc is None
