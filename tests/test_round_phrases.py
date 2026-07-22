"""The remembered round-label triples behind the phrase picker."""

import re
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ConcertDay, Round, RoundLabelPhrase
from app.db.service import (
    forget_round_label_phrase,
    record_round_label_phrase,
    round_label_phrases,
)
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app
from app.web.routes import imports as import_routes

EDITOR_ID = 42
FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # Production registers this too; cascades silently do not fire without it.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def editor_client(db, monkeypatch):
    """The signed-in-editor HTTP client, same shape as tests/test_crud.py's
    `client` + `login_as` pair / tests/test_venue_rollup.py's `editor_client`
    (this suite has no shared conftest fixture for it), with the login
    already performed. The brief's test code assumed an async httpx client;
    this codebase's web-route tests instead use a sync TestClient wrapped in
    `async def test_...` functions (bodies call `.post()`/`.get()`
    unawaited), so that is the shape used here -- every assertion from the
    brief is kept identical."""
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    async def fake_identity(token):
        return {"id": str(EDITOR_ID), "username": "ed", "global_name": "ed", "avatar": None}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)

    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    c.get(f"/auth/callback?code=x&state={state}")
    c.db = db
    return c


@pytest.fixture()
def client(db, monkeypatch):
    """A signed-OUT client -- for the one test that needs the auth
    invariant (require_editor's LoginRequired -> redirect, never a 403)
    rather than an editor session."""
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


def test_phrase_columns_and_unique_index():
    cols = RoundLabelPhrase.__table__.columns
    for name in ("label", "label_en", "label_zh"):
        assert name in cols, f"RoundLabelPhrase.{name} missing"
        assert not cols[name].nullable, f"{name} must be NOT NULL"
        assert cols[name].type.length == 200
    assert cols["used_count"].default is not None

    # A named unique index over the whole triple: two phrases sharing a
    # Japanese label but differing in translation are distinct rows, which is
    # what lets a corrected phrase coexist with the typo it replaces.
    idx = {i.name: i for i in RoundLabelPhrase.__table__.indexes}
    assert "uq_round_label_phrase" in idx, f"got {list(idx)}"
    target = idx["uq_round_label_phrase"]
    assert target.unique
    assert [c.name for c in target.columns] == ["label", "label_en", "label_zh"]


async def test_a_phrase_round_trips(db):
    async with db() as session:
        session.add(RoundLabelPhrase(
            label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行",
        ))
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 1
        assert row.created_at.tzinfo is not None, "timestamps are aware UTC"


async def test_recording_a_phrase_twice_bumps_its_use_count(db):
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 2, "the second save reuses the row, not a duplicate"


async def test_a_partial_triple_is_not_recorded(db):
    """A phrase is only worth remembering when all three languages are there —
    a half-filled triple would be offered as a suggestion that leaves the
    editor with blanks to fill anyway."""
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "")
        await record_round_label_phrase(session, "", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "  ", "  ", "  ")
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_phrases_rank_by_use_then_recency(db):
    async with db() as session:
        for _ in range(3):
            await record_round_label_phrase(session, "A", "A", "A")
        await record_round_label_phrase(session, "B", "B", "B")
        await record_round_label_phrase(session, "C", "C", "C")
        await session.commit()

    async with db() as session:
        rows = await round_label_phrases(session)
        assert rows[0].label == "A", "most-used first"
        assert [r.label for r in rows[1:]] == ["C", "B"], "then most-recent first"


async def test_forgetting_a_phrase_removes_only_the_suggestion(db):
    async with db() as session:
        await record_round_label_phrase(session, "typo", "Offical", "官方")
        await session.commit()
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert await forget_round_label_phrase(session, row.id) is True
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_forgetting_an_unknown_phrase_is_false_not_an_error(db):
    async with db() as session:
        assert await forget_round_label_phrase(session, 9999) is False


# ── Forgetting a phrase (web) ─────────────────────────────────────────────


async def test_forgetting_a_phrase_removes_it_from_the_picker(editor_client, db):
    async with db() as session:
        await record_round_label_phrase(session, "typo", "Offical", "官方")
        await session.commit()
        pid = (await session.execute(select(RoundLabelPhrase))).scalar_one().id

    resp = editor_client.post(f"/round-phrases/{pid}/forget")
    assert resp.status_code == 204

    r = editor_client.get("/concerts/new")
    assert "Offical" not in r.text

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_forgetting_a_phrase_leaves_the_rounds_that_used_it(editor_client, db):
    """A phrase is a suggestion, not a foreign key: create a concert whose
    round carries the full trilingual triple (which records the phrase as a
    side effect, same as the wiring tests below), forget that phrase, then
    assert the Round row -- not just the phrase table -- still has all three
    label values untouched."""
    create_resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "phforget",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert create_resp.status_code in (200, 303)

    async with editor_client.db() as session:
        round_id = (await session.execute(select(Round))).scalar_one().id
        pid = (await session.execute(select(RoundLabelPhrase))).scalar_one().id

    resp = editor_client.post(f"/round-phrases/{pid}/forget")
    assert resp.status_code == 204

    async with editor_client.db() as session:
        # The phrase suggestion is gone...
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []
        # ...but the round that used it is untouched.
        round_ = await session.get(Round, round_id)
        assert (round_.label, round_.label_en, round_.label_zh) == (
            "1次先行抽選", "1st-round lottery", "第一轮先行",
        )


async def test_forgetting_an_unknown_phrase_is_404(editor_client):
    assert editor_client.post("/round-phrases/9999/forget").status_code == 404


async def test_forget_requires_an_editor(client):
    """Signed out is a redirect, not a 403 -- see the auth invariant."""
    resp = client.post("/round-phrases/1/forget")
    assert resp.status_code in (200, 303, 204)


# ── Wiring: every save path records a phrase as a side effect ────────────


async def test_saving_a_concert_records_its_round_phrases(editor_client, db):
    resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph1",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with editor_client.db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert (row.label, row.label_en, row.label_zh) == (
            "1次先行抽選", "1st-round lottery", "第一轮先行",
        )


async def test_a_round_saved_in_japanese_only_records_nothing(editor_client, db):
    """The common case today. It must not litter the picker with unusable
    partial suggestions."""
    resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph2",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": [""], "round_label_zh": [""],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with editor_client.db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_editing_an_existing_round_records_its_phrase(editor_client):
    """The edit route's UPDATE branch (apply_round_fields, for a round id
    the submit already carries) is a separate call site from create's
    build_round -- covered here since it is the one most likely to be
    missed."""
    create_resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph3",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": [""], "round_label_zh": [""],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert create_resp.status_code in (200, 303)

    async with editor_client.db() as session:
        round_id = (await session.execute(select(Round))).scalar_one().id
        day_id = (await session.execute(select(ConcertDay))).scalar_one().id
        # Nothing recorded yet -- the create above only filled the Japanese
        # label, matching the common-case test above.
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []

    edit_resp = editor_client.post("/concerts/ph3/edit", data={
        "title": "T", "event_id": "ph3",
        "day_id": [str(day_id)],
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_id": [str(round_id)],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert edit_resp.status_code in (200, 303)

    async with editor_client.db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert (row.label, row.label_en, row.label_zh) == (
            "1次先行抽選", "1st-round lottery", "第一轮先行",
        )
        # The update branch must not have deleted and recreated the round.
        assert (await session.execute(select(Round))).scalar_one().id == round_id


async def test_adding_a_round_via_edit_records_its_phrase(editor_client):
    """The edit route's INSERT branch (build_round, for a round with no id
    in the submit -- a newly added row on an existing concert) is the fourth
    call site."""
    create_resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph4",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
    })
    assert create_resp.status_code in (200, 303)

    async with editor_client.db() as session:
        day_id = (await session.execute(select(ConcertDay))).scalar_one().id

    edit_resp = editor_client.post("/concerts/ph4/edit", data={
        "title": "T", "event_id": "ph4",
        "day_id": [str(day_id)],
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_id": [""],
        "round_label": ["2次先行抽選"], "round_label_en": ["2nd-round lottery"],
        "round_label_zh": ["第二轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert edit_resp.status_code in (200, 303)

    async with editor_client.db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert (row.label, row.label_en, row.label_zh) == (
            "2次先行抽選", "2nd-round lottery", "第二轮先行",
        )


async def test_import_commit_records_its_round_phrase(editor_client):
    """The third save path: the URL-import commit route builds rounds the
    same way and must run the same recording."""
    resp = editor_client.post("/concerts/import/commit", data={
        "title": "Imported Show",
        "day_label": ["Day 1"],
        "day_starts_at": ["2026-08-01T18:00"],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
    })
    assert resp.status_code in (200, 303)

    async with editor_client.db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert (row.label, row.label_en, row.label_zh) == (
            "1次先行抽選", "1st-round lottery", "第一轮先行",
        )


# ── The picker dialog ────────────────────────────────────────────────────


async def test_the_phrase_picker_lists_remembered_triples(editor_client, db):
    async with db() as session:
        await record_round_label_phrase(session, "1次先行抽選", "1st-round lottery", "第一轮先行")
        await session.commit()

    r = editor_client.get("/concerts/new")
    assert r.status_code == 200

    start = r.text.index('id="round-phrase-picker"')
    dialog = r.text[start : r.text.index("</dialog>", start)]

    # All three languages visible before you pick.
    assert "1次先行抽選" in dialog
    assert "1st-round lottery" in dialog
    assert "第一轮先行" in dialog
    # Invariant 7: no inline on* handler may carry user-controlled text.
    assert not re.search(r"\son(?!input)\w+\s*=", dialog), dialog
    # Searchable in any language via the shared filterChips helper.
    assert 'data-name="1次先行抽選 1st-round lottery 第一轮先行"' in dialog


async def test_the_picker_can_show_a_no_match_state(editor_client, db):
    """data-phrase-empty (the zero-ever-saved state) is computed server-side
    from round_phrases and carries no data-name, so the shared filterChips
    helper (base.html) never touches it -- a search matching nothing left
    the list silently blank. The fix is a second, JS-driven paragraph the
    dialog's own script toggles after every filterChips call. This suite
    has no JS runtime, so it pins the markup and wiring statically: the
    hidden-by-default paragraph exists, and the script both reacts to the
    search box's input event and clears the state on close."""
    async with db() as session:
        await record_round_label_phrase(session, "1次先行抽選", "1st-round lottery", "第一轮先行")
        await session.commit()

    r = editor_client.get("/concerts/new")
    start = r.text.index('id="round-phrase-picker"')
    dialog = r.text[start : r.text.index("</dialog>", start)]
    script = r.text[r.text.index("</dialog>", start) : r.text.index("</script>", start)]

    # Distinct from data-phrase-empty -- reusing that paragraph for a
    # no-match search would tell someone with saved labels that none exist.
    assert "data-phrase-empty" in dialog
    assert re.search(r"data-phrase-no-match\s+hidden", dialog), dialog

    assert 'querySelector("[data-phrase-no-match]")' in script
    assert re.search(r'searchInput\.addEventListener\("input",\s*updateNoMatchState\)', script)
    # Reopening after a search must not carry a stale no-match state, same
    # as the existing close handler already does for the search box itself.
    assert "updateNoMatchState()" in script


async def test_every_round_row_can_open_the_phrase_picker(editor_client):
    r = editor_client.get("/concerts/new")
    tpl_start = r.text.index('id="round-row-template"')
    tpl = r.text[tpl_start : r.text.index("</template>", tpl_start)]
    assert "data-open-phrases" in tpl, "a JS-added round row must offer the picker too"


async def test_the_edit_page_offers_the_picker_on_saved_and_blank_rows(editor_client):
    """The opener has to reach BOTH round-row shapes on this page -- the
    pre-filled loop row and the blank <template> -- or it silently goes
    missing on whichever half the editor happens to use."""
    resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph5",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行抽選"], "round_label_en": [""], "round_label_zh": [""],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    r = editor_client.get("/concerts/ph5/edit")
    assert r.status_code == 200
    assert 'id="round-phrase-picker"' in r.text

    # Slice the page so each shape is pinned independently. A bare count()
    # over the whole page is satisfied by the dialog's OWN script, which
    # also contains the literal "data-open-phrases" string (it is the
    # delegated click-listener's selector) -- that let one opener plus the
    # script pass a ">= 2" threshold while the other shape had none.
    # (base.html's header carries its own <form>/</form> for the language
    # toggle, ahead of the concert form -- anchor on id="new-concert" rather
    # than the first "</form>" in the page, or the slice ends before the
    # round row is even reached.)
    form_start = r.text.index('id="new-concert"')
    form_end = r.text.index("</form>", form_start)
    form = r.text[form_start:form_end]
    assert "data-open-phrases" in form, "the pre-filled saved round row needs it"

    tpl_start = r.text.index('id="round-row-template"')
    tpl = r.text[tpl_start : r.text.index("</template>", tpl_start)]
    assert "data-open-phrases" in tpl, "the blank <template> row needs it"


async def test_the_import_preview_offers_the_picker_on_every_round_row(
    editor_client, monkeypatch
):
    """The third editor surface. Its parsed rows are where a remembered
    translation is most valuable -- the scrape only ever fills Japanese."""
    html = (FIXTURES / "ramen_graduation_concert.html").read_text(encoding="utf-8")

    async def fake_fetch(url: str) -> str:
        return html

    monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)

    r = editor_client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    assert 'id="round-phrase-picker"' in r.text

    # Same slicing as the edit-page test above -- a bare count() is
    # satisfied by the dialog's own script even if the parsed row's opener
    # is missing entirely.
    form_start = r.text.index('id="new-concert"')
    form_end = r.text.index("</form>", form_start)
    form = r.text[form_start:form_end]
    assert "data-open-phrases" in form, "the parsed round row needs it"

    tpl_start = r.text.index('id="round-row-template"')
    tpl = r.text[tpl_start : r.text.index("</template>", tpl_start)]
    assert "data-open-phrases" in tpl, "the blank <template> row needs it"


async def test_the_phrase_dialog_sits_outside_the_form_on_every_page(
    editor_client, monkeypatch
):
    """The dialog is all buttons today, but the venue dialog next door proves
    how quickly one grows inputs -- anything inside the concert <form> rides
    along on save. Currently correct by placement on all three pages, but
    untested, so a future include moved above </form> would fail silently.

    base.html's header carries its own <form>/</form> for the language
    toggle, ahead of the concert form on every page, so the first "</form>"
    in the page is always that one -- checking against it would pass
    trivially no matter where the dialog landed. Anchor on the CONCERT
    form's own close instead, found from its id="new-concert" open tag."""

    def assert_dialog_after_concert_form(page: str):
        form_start = page.index('id="new-concert"')
        form_end = page.index("</form>", form_start)
        assert page.index('id="round-phrase-picker"') > form_end

    assert_dialog_after_concert_form(editor_client.get("/concerts/new").text)

    create_resp = editor_client.post("/concerts", data={
        "title": "T", "event_id": "ph6",
        "day_label": ["Day 1"], "day_label_en": [""], "day_label_zh": [""],
        "day_starts_at": ["2026-09-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
    })
    assert create_resp.status_code in (200, 303)
    assert_dialog_after_concert_form(editor_client.get("/concerts/ph6/edit").text)

    html2 = (FIXTURES / "ramen_graduation_concert.html").read_text(encoding="utf-8")

    async def fake_fetch2(url: str) -> str:
        return html2

    monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch2)
    assert_dialog_after_concert_form(
        editor_client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text
    )
