"""The create-boundary backstop: a half-translated field is a 422.

The browser-side check normally catches this and paints an inline error
without submitting, so these routes are the backstop -- and for a caller
with JS disabled the 422 detail string is the ENTIRE error UX, which is
why every test here asserts on the message and not just the status code.

The deliberate asymmetry -- the EDIT routes stay open, so a legacy
half-translated record can still be saved -- is pinned at the bottom.
"""

import ast
import re
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Round, Tag
from app.db.session import get_session
from app.domain import translations as app_translations
from app.web import auth
from app.web.app import create_app
from app.web.routes import concerts as concert_routes
from app.web.routes import imports as import_routes

EDITOR_ID = 42


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
def client(db, monkeypatch):
    """Signed-in-editor TestClient -- same fixture shape as
    tests/test_round_phrases.py's `editor_client`."""
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
    # The import-preview markup tests below need to stub the ramen.events
    # fetch, the same way tests/test_imports.py does.
    c.monkeypatch = monkeypatch
    return c


def _minimal_concert(event_id: str, *, full_title: bool = False) -> dict:
    """The smallest payload `POST /concerts` accepts -- no legs, no rounds."""
    data: dict = {"event_id": event_id, "title": "ラブライブ"}
    if full_title:
        data |= {"title_en": "Love Live", "title_zh": "LoveLive"}
    return data


def _leg(label: str, label_en: str, label_zh: str) -> dict:
    """One fully-specified leg row (every array create_concert zips strictly)."""
    return {
        "day_label": [label], "day_label_en": [label_en], "day_label_zh": [label_zh],
        "day_starts_at": ["2026-09-01T18:00"], "day_doors_at": [""],
    }


def _round(label: str, label_en: str, label_zh: str) -> dict:
    """One fully-specified round row."""
    return {
        "round_label": [label], "round_label_en": [label_en], "round_label_zh": [label_zh],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""],
    }


# --- concert create -------------------------------------------------------

async def test_creating_a_concert_with_a_half_translated_title_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-title"),
        "title_en": "Love Live", "title_zh": "",
    })
    assert r.status_code == 422
    assert "中文" in r.json()["detail"], r.json()
    assert "title" in r.json()["detail"].lower(), r.json()


async def test_creating_a_concert_with_no_title_translations_is_422(client):
    """title is mandatory: blank in all three is not the escape hatch it is
    for an optional field."""
    r = client.post("/concerts", data={
        **_minimal_concert("no-title-tr"), "title_en": "", "title_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "English" in detail and "中文" in detail, detail


async def test_creating_a_concert_with_an_untouched_optional_field_is_fine(client):
    """notes left blank in all three is not an error."""
    r = client.post("/concerts", data={
        **_minimal_concert("blank-notes", full_title=True),
        "notes": "", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code in (200, 303), r.text


async def test_a_half_translated_notes_field_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-notes", full_title=True),
        "notes": "備考", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "notes" in detail.lower() and "English" in detail and "中文" in detail, detail


async def test_a_half_translated_leg_label_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-leg", full_title=True),
        **_leg("1日目", "Day 1", ""),
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "中文" in detail, detail
    assert "1" in detail, f"the message must name the row: {detail}"


async def test_a_half_translated_round_label_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-round", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        **_round("1次先行抽選", "", "第一轮先行"),
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "English" in detail, detail
    assert "round" in detail.lower(), detail


async def test_a_fully_translated_concert_with_legs_and_rounds_saves(client):
    r = client.post("/concerts", data={
        **_minimal_concert("all-good", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        **_round("1次先行抽選", "1st-round lottery", "第一轮先行"),
    })
    assert r.status_code in (200, 303), r.text


async def test_blank_labels_everywhere_are_still_fine(client):
    """Labels are optional -- a leg with no label at all still saves."""
    r = client.post("/concerts", data={
        **_minimal_concert("blank-labels", full_title=True),
        **_leg("", "", ""),
        **_round("", "", ""),
    })
    assert r.status_code in (200, 303), r.text


# --- tag create -----------------------------------------------------------

async def test_creating_a_tag_requires_all_three_names(client):
    r = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "", "kind": "group",
    })
    assert r.status_code == 422
    assert "中文" in r.json()["detail"], r.json()


async def test_creating_a_fully_translated_tag_is_fine(client):
    r = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "莲之空", "kind": "group",
    })
    assert r.status_code in (200, 303), r.text


async def test_a_quick_venue_with_a_half_translated_city_is_422(client):
    r = client.post("/tags/venue/quick", data={
        "name": "Zepp羽田", "name_en": "Zepp Haneda", "name_zh": "Zepp羽田",
        "city": "東京", "city_en": "Tokyo", "city_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "city" in detail.lower() and "中文" in detail, detail


async def test_a_quick_venue_with_no_city_at_all_is_fine(client):
    """city is all-or-nothing, not mandatory."""
    r = client.post("/tags/venue/quick", data={
        "name": "Zepp羽田", "name_en": "Zepp Haneda", "name_zh": "Zepp羽田",
        "city": "", "city_en": "", "city_zh": "",
    })
    assert r.status_code == 200, r.text


# --- import commit --------------------------------------------------------
#
# The import route is a create boundary like POST /concerts, and enforces the
# same rule -- but through its OWN require_variants calls, so nothing above
# covers it. Note it end-pads the label-variant arrays (its minimal-client
# contract), which is exactly why a half-translated row has to be caught by
# the check and not by the strict zip.


def _import_payload(title: str, **over) -> dict:
    """The established import-commit shape (see tests/test_imports.py), fully
    translated, so a test can knock out one variant and know that is the only
    reason it fails."""
    data: dict = {
        "title": title, "title_en": title, "title_zh": "中文标题",
        "day_label": ["Day 1"], "day_label_en": ["Day 1"], "day_label_zh": ["第一天"],
        "day_starts_at": ["2027-01-23T17:00"],
        "round_label": ["1次先行抽選"], "round_label_en": ["1st-round lottery"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2026-11-08T23:59"],
        "round_results_at": [""], "round_payment_at": [""], "round_url": [""],
    }
    return data | over


async def test_import_commit_with_a_fully_translated_draft_saves(client):
    """The control: without it, a payload that 422'd for an unrelated reason
    would make all three tests below pass for nothing."""
    r = client.post("/concerts/import/commit", data=_import_payload("Good Import"))
    assert r.status_code in (200, 303), r.text


async def test_import_commit_with_a_half_translated_title_is_422(client):
    r = client.post("/concerts/import/commit", data=_import_payload(
        "Half Title", title_zh="",
    ))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "title" in detail.lower() and "中文" in detail, detail


async def test_import_commit_with_a_half_translated_leg_label_is_422(client):
    r = client.post("/concerts/import/commit", data=_import_payload(
        "Half Leg", day_label_zh=[""],
    ))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "中文" in detail, detail
    assert "leg" in detail.lower() and "1" in detail, detail


async def test_import_commit_with_a_half_translated_round_label_is_422(client):
    r = client.post("/concerts/import/commit", data=_import_payload(
        "Half Round", round_label_en=[""],
    ))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "English" in detail, detail
    assert "round" in detail.lower() and "1" in detail, detail


async def test_import_commit_with_half_translated_notes_is_422(client):
    """Notes was the last gap: the route collected only `notes`, so an import
    could write a Japanese-only note no other create path allows -- and the
    edit page's variant-gaps notice would then nag forever about a field the
    import form never offered in three languages."""
    r = client.post("/concerts/import/commit", data=_import_payload(
        "Half Notes", notes="日本語のみ", notes_en="", notes_zh="",
    ))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "notes" in detail.lower(), detail
    assert "English" in detail and "中文" in detail, detail


async def test_import_commit_with_notes_blank_in_all_three_saves(client):
    """Notes is all-or-nothing, NOT mandatory -- an import with no notes at
    all is the normal case and must stay a 303."""
    r = client.post("/concerts/import/commit", data=_import_payload("No Notes"))
    assert r.status_code in (200, 303), r.text


def test_the_import_preview_offers_all_three_notes_boxes(client):
    """The server-side rule above is only reachable if the form can satisfy
    it: a fold with `notes` alone would 422 the moment anything was typed."""
    body = _preview(client)
    assert sorted(set(_slots(body, "notes"))) == ["en", "ja", "zh"]
    for field in ("notes", "notes_en", "notes_zh"):
        assert f'name="{field}"' in body, field


# --- the deliberate asymmetry --------------------------------------------

async def test_editing_a_legacy_half_translated_record_is_still_allowed(client):
    """The EDIT routes deliberately do NOT enforce this.

    The phase ships without a backfill, so most existing rows are
    half-translated or untranslated. Enforcing on edit would wall the owner
    out of their own data -- a later task surfaces the gap on the page
    instead. If this test ever fails because someone "helpfully" added
    require_variants to edit_concert, that is the regression, not this test.
    """
    created = client.post("/concerts", data=_minimal_concert("legacy", full_title=True))
    assert created.status_code in (200, 303), created.text

    r = client.post("/concerts/legacy/edit", data={
        "event_id": "legacy",
        "title": "ラブライブ", "title_en": "Love Live", "title_zh": "",
        "notes": "備考", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code in (200, 303), r.text


async def test_editing_a_legacy_half_translated_tag_is_still_allowed(client):
    """The same asymmetry, on the OTHER edit route.

    edit_tag is the second half of the canary: create_tag enforces the rule,
    edit_tag deliberately does not, and without this test require_variants
    could be added there and nothing would notice.
    """
    created = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "莲之空", "kind": "group",
    })
    assert created.status_code in (200, 303), created.text

    async with client.db() as s:
        tag_id = (await s.execute(select(Tag.id))).scalars().one()

    r = client.post(f"/tags/{tag_id}/edit", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "",
    })
    assert r.status_code in (200, 303), r.text


# --- the browser-side block ----------------------------------------------
#
# Everything above is the backstop. These pin the markup and wiring the
# browser guard needs, because the guard itself is JS and pytest cannot run
# it: what CAN be asserted is that every trio is enumerable, that exactly the
# create forms opt in, and -- the one that has bitten -- that the venue
# dialog's trios, which live OUTSIDE the concert <form> on purpose, are still
# in the guard's reach.

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"


def _slots(body: str, group: str) -> list[str]:
    """The slot attributes of one variant group, in document order."""
    return re.findall(
        r'data-variant-group="' + group + r'"[^>]*data-variant-slot="(\w+)"', body
    ) + re.findall(
        r'data-variant-slot="(\w+)"[^>]*data-variant-group="' + group + r'"', body
    )


def _editor_form_tag(body: str) -> str | None:
    """The opening <form> tag of the editor form on an editor page.

    `data-variant-guard` also appears in the guard script's own source, so
    "is this form guarded" has to be asked of the tag, not of the page.
    """
    for tag in re.findall(r"<form[^>]*>", body):
        if 'id="new-concert"' in tag:
            return tag
    return None


def _new_tag_form_tag(body: str) -> str | None:
    """The opening <form> tag of the create-tag dialog on /tags.

    /tags also renders one edit <form id="tag-edit-{id}"> per tag, on the
    same page -- the census test below must not conflate the two, so this
    slices to the create dialog first (data-name-scoped by
    `id="new-tag-dialog"`, the same trick `_preview` uses for the import
    dialog boundary) and only then looks for its <form>.
    """
    dialog = body.split('id="new-tag-dialog"')
    if len(dialog) < 2:
        return None
    section = dialog[1].split("</dialog>")[0]
    m = re.search(r"<form[^>]*>", section)
    return m.group(0) if m else None


def _preview(client) -> str:
    html = (FIXTURES_DIR / "ramen_graduation_concert.html").read_text(encoding="utf-8")

    async def fake_fetch(url: str) -> str:
        return html

    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200, r.text
    return r.text


def test_the_create_form_marks_every_variant_trio(client):
    """Each trio is findable by marker, not by guessing at input names."""
    body = client.get("/concerts/new").text
    for group in ("title", "notes", "day_label", "round_label"):
        got = sorted(set(_slots(body, group)))
        assert got == ["en", "ja", "zh"], f"{group}: {got}"


def test_the_create_form_opts_into_the_guard(client):
    body = client.get("/concerts/new").text
    assert _editor_form_tag(body) is not None
    assert "data-variant-guard" in _editor_form_tag(body)
    assert "checkVariantGroups" in body, "the guard script must be on the page"


def test_only_the_title_trio_is_mandatory_on_the_concert_form(client):
    """Mirrors the server: title is mandatory, notes/labels are all-or-nothing.

    Mark notes mandatory and every concert without notes becomes unsavable.
    """
    # The concert form only -- the venue dialog on the same page has its own
    # mandatory group (v_name), which is checked separately below. (Slicing
    # from the form tag, not from the top of the page: base.html's language
    # switcher is a <form> too, and closes long before this one opens.)
    page = client.get("/concerts/new").text
    body = page.split(_editor_form_tag(page))[1].split("</form>")[0]
    mandatory = re.findall(r'data-variant-group="(\w+)"[^>]*data-variant-mandatory', body)
    assert set(mandatory) == {"title"}, mandatory


def test_cloned_row_templates_carry_the_markers(client):
    """Legs and rounds are added by cloning a <template>. A cloned row that
    carried no markers would be silently exempt, which is precisely the
    row an editor is most likely to leave half-translated."""
    body = client.get("/concerts/new").text
    tpl = body.split('<template id="round-row-template">')[1]
    assert sorted(set(_slots(tpl, "round_label"))) == ["en", "ja", "zh"]
    assert "data-variant-scope" in tpl.split("</template>")[0]

    day_tpl = body.split('<template id="day-row-template">')[1].split("</template>")[0]
    assert sorted(set(_slots(day_tpl, "day_label"))) == ["en", "ja", "zh"]
    assert "data-variant-scope" in day_tpl


def test_every_row_is_its_own_variant_scope(client):
    """Two legs both hold a `day_label` group; without a per-row scope the
    guard would merge them and read one row's English as another's."""
    created = client.post("/concerts", data={
        **_minimal_concert("two-legs", full_title=True),
        "day_label": ["1日目", "2日目"],
        "day_label_en": ["Day 1", "Day 2"],
        "day_label_zh": ["第一天", "第二天"],
        "day_starts_at": ["2026-09-01T18:00", "2026-09-02T18:00"],
        "day_doors_at": ["", ""],
    })
    assert created.status_code in (200, 303), created.text

    body = client.get("/concerts/two-legs/edit").text
    # Two rendered legs + the clone template = three scoped `.eleg` blocks.
    assert body.count('class="eleg" data-variant-scope') >= 3, body.count("eleg")


# The trap: these inputs are NOT inside the concert <form>.


def test_the_venue_dialog_trios_are_marked(client):
    """v_name / v_city live outside the concert <form> by design, so a check
    scoped to form descendants would skip them entirely."""
    body = client.get("/concerts/new").text
    dialog = body.split('id="venue-create"')[1].split("</dialog>")[0]
    assert sorted(set(_slots(dialog, "v_name"))) == ["en", "ja", "zh"]
    assert sorted(set(_slots(dialog, "v_city"))) == ["en", "ja", "zh"]
    assert "data-variant-mandatory" in dialog, "a venue must have a name"


def test_the_venue_dialog_checks_variants_before_its_fetch(client):
    """The dialog POSTs by fetch, not by form submit, so the submit hook
    never sees it -- it has to call the guard itself, ahead of the fetch."""
    body = client.get("/concerts/new").text
    script = body.split('id="venue-create"')[1]
    guard_at = script.find("checkVariantGroups")
    fetch_at = script.find('fetch("/tags/venue/quick"')
    assert guard_at != -1, "the dialog never calls the guard"
    assert fetch_at != -1
    assert guard_at < fetch_at, "the guard must run before the request goes out"


def test_the_phrase_dialog_dispatches_input_after_filling_a_variant(client):
    """The phrase dialog fills round_label/_en/_zh by direct .value
    assignment, which fires no "input" event on its own -- so the variant
    guard's delegated listener (bound on document, see _variant_guard.html)
    would never see the fill and a warning painted before the dialog opened
    would survive under three now-full boxes. The fix dispatches a bubbling
    "input" event right after each assignment."""
    body = client.get("/concerts/new").text
    script = body.split('id="round-phrase-picker"')[1]
    assign_at = script.find("input.value = value")
    dispatch_at = script.find('new Event("input", { bubbles: true })')
    assert assign_at != -1, "the dialog never fills the input"
    assert dispatch_at != -1, "the dialog never dispatches an input event"
    assert assign_at < dispatch_at, "the event must fire after the value is set"


def test_the_venue_dialog_is_reachable_from_every_page_that_includes_it(client):
    """It is included by all three editor pages; the guard has to be too."""
    created = client.post("/concerts", data=_minimal_concert("dlg", full_title=True))
    assert created.status_code in (200, 303), created.text
    for body in (
        client.get("/concerts/new").text,
        client.get("/concerts/dlg/edit").text,
        _preview(client),
    ):
        assert 'id="venue-create"' in body
        assert "checkVariantGroups" in body


def test_the_import_preview_marks_and_guards_its_trios(client):
    body = _preview(client)
    for group in ("title", "day_label", "round_label"):
        assert sorted(set(_slots(body, group))) == ["en", "ja", "zh"], group
    assert "data-variant-guard" in _editor_form_tag(body)


def test_the_new_tag_dialog_marks_and_guards_its_trio(client):
    """create_tag calls require_variants("Tag name", ..., mandatory=True),
    so the browser-side dialog has to offer name_en/name_zh AND block on a
    gap -- without both, every real submission would 422 on the missing
    languages with no way for the editor to know why."""
    body = client.get("/tags").text
    form_tag = _new_tag_form_tag(body)
    assert form_tag is not None, "no create-tag <form> found"
    assert sorted(set(_slots(body, "tag_name"))) == ["en", "ja", "zh"]
    assert "data-variant-mandatory" in body.split('id="new-tag-dialog"')[1].split("</dialog>")[0]
    assert "data-variant-guard" in form_tag, form_tag


# The other half of the asymmetry pinned above: the EDIT pages are marked
# (a later task reads the markers to say what is missing) but never blocked,
# because every pre-i18n row in the database is half-translated and blocking
# would wall the owner out of their own data.


def test_the_concert_edit_form_is_marked_but_never_blocked(client):
    created = client.post("/concerts", data=_minimal_concert("legacy-edit", full_title=True))
    assert created.status_code in (200, 303), created.text
    body = client.get("/concerts/legacy-edit/edit").text
    assert sorted(set(_slots(body, "title"))) == ["en", "ja", "zh"]
    form_tag = _editor_form_tag(body)
    assert form_tag is not None
    assert "data-variant-scope" in form_tag, form_tag
    assert "data-variant-guard" not in form_tag, form_tag


def test_the_tag_edit_dialog_is_marked_but_never_blocked(client):
    created = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "莲之空", "kind": "group",
    })
    assert created.status_code in (200, 303), created.text
    body = client.get("/tags").text
    assert sorted(set(_slots(body, "tag_name"))) == ["en", "ja", "zh"]
    # The literal also appears in the guard script's own source, AND in the
    # (correctly guarded) create-tag dialog's <form> on the same page -- so
    # look at the EDIT form specifically (id="tag-edit-{id}"), not every
    # <form> tag on the page indiscriminately.
    edit_forms = re.findall(r'<form id="tag-edit-\d+"[^>]*>', body)
    assert edit_forms, "no tag-edit form found"
    guarded = [f for f in edit_forms if "data-variant-guard" in f]
    assert not guarded, f"editing a legacy tag must stay possible: {guarded}"


def test_the_guard_marks_its_duplication_of_the_domain_rule(client):
    """The JS re-implements domain/translations.py:missing_variants. That is
    accepted, but a future change to one has to be visibly a change to both,
    so each side names the other."""
    body = client.get("/concerts/new").text
    assert "missing_variants" in body
    src = Path(app_translations.__file__).read_text(encoding="utf-8")
    assert "_variant_guard.html" in src


# --- census: every require_variants caller has a guarded create surface ---
#
# data-variant-guard is opt-in per <form>, and nothing enforces the pairing
# between "this route 422s on a gap" and "this route's template blocks the
# submit before that". A route added to CREATE_BOUNDARIES below without a
# matching guarded surface gets exactly that silent fallback: markers or no
# markers, the editor sees a raw JSON 422 the first time they leave a
# language blank.
#
# This is a MECHANICAL route -> template -> create-surface census, not a
# page-wide substring search: _variant_guard.html's own script source
# contains the literal string "data-variant-guard" (see its docstring and
# its submit-hook selector), so "does data-variant-guard appear anywhere on
# this page" would pass vacuously for every page that merely includes the
# guard script -- which is every editor page. And /tags renders the create
# dialog's guarded <form> alongside one unguarded edit <form> PER TAG on the
# very same page, so a check has to isolate the create surface specifically
# or it either false-fails on the (correctly unguarded) edit forms or passes
# by accident depending on which form the regex happens to find first. Each
# locator below pulls the ONE element for its route: the create <form> tag
# itself for the three form-submit routes, or (quick_create_venue's shape,
# since it POSTs by fetch and a <form> submit event never fires for it) the
# ordering of its explicit checkVariantGroups() call ahead of the fetch.
CREATE_BOUNDARIES = ("create_concert", "import_commit", "create_tag", "quick_create_venue")

# Globbed, not a hand-listed tuple: naming the three modules we happen to
# know about today would reopen the exact hole this census closes one level
# up -- a require_variants call in a FOURTH route module would go
# undetected, and that route's form would silently fall back to the raw 422.
# Only the web layer can legitimately call it (it is an HTTP boundary living
# in web/forms.py), so this glob is the whole legitimate surface.
_ROUTE_DIR = Path(concert_routes.__file__).parent
_ROUTE_SOURCES = sorted(p for p in _ROUTE_DIR.glob("*.py") if p.name != "__init__.py")


def _require_variants_callers() -> set[str]:
    """Every function in the route modules that calls require_variants.

    DERIVED from source rather than listed by hand, because the census below
    can only be honest if "what the server enforces" comes from the server.
    The hand-written CREATE_BOUNDARIES tuple compared against itself checks
    nothing: a fifth route added later with a require_variants call and no
    guarded template gets exactly the silent raw-422 fallback this whole
    section exists to prevent, and no test would have moved.

    ast, not a regex over the text: a call is attributed to the INNERMOST
    enclosing def, so a helper closure would be reported under its own name
    instead of silently crediting the route around it.
    """
    callers: set[str] = set()
    for source in _ROUTE_SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        funcs = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if not (isinstance(call.func, ast.Name) and call.func.id == "require_variants"):
                continue
            enclosing = [
                f for f in funcs
                if f.lineno <= call.lineno and call.lineno <= (f.end_lineno or f.lineno)
            ]
            assert enclosing, f"require_variants call outside any def at line {call.lineno}"
            # Innermost wins: the narrowest span containing the call.
            callers.add(min(enclosing, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name)
    return callers


def test_the_create_boundary_list_matches_the_routes_that_actually_enforce():
    """CREATE_BOUNDARIES is the census's contract, so it has to be checked
    against the source, not against itself. Add a require_variants call in a
    new function and this fails until that route is censused below."""
    assert _require_variants_callers() == set(CREATE_BOUNDARIES)


def test_every_require_variants_caller_has_a_guarded_create_surface(client):
    covered = []

    # create_concert -> POST /concerts -> concert_new.html, form#new-concert.
    # This page also carries the venue dialog (used below for
    # quick_create_venue), which is NOT rendered by tags.html -- fetched once
    # here and reused, rather than re-fetched per route, so a stray reuse of
    # this variable for a different route cannot silently paper over a
    # missing dialog on some OTHER page.
    concert_new_body = client.get("/concerts/new").text
    form_tag = _editor_form_tag(concert_new_body)
    assert form_tag is not None, "create_concert: no editor <form> found"
    assert "data-variant-guard" in form_tag, f"create_concert: {form_tag}"
    covered.append("create_concert")

    # import_commit -> POST /concerts/import/commit -> import_preview.html,
    # form#new-concert
    preview_body = _preview(client)
    form_tag = _editor_form_tag(preview_body)
    assert form_tag is not None, "import_commit: no editor <form> found"
    assert "data-variant-guard" in form_tag, f"import_commit: {form_tag}"
    covered.append("import_commit")

    # create_tag -> POST /tags -> tags.html, the #new-tag-dialog <form>
    # specifically (NOT the per-tag edit dialogs sharing the same page)
    tags_body = client.get("/tags").text
    form_tag = _new_tag_form_tag(tags_body)
    assert form_tag is not None, "create_tag: no create-tag <form> found"
    assert "data-variant-guard" in form_tag, f"create_tag: {form_tag}"
    covered.append("create_tag")

    # quick_create_venue -> POST /tags/venue/quick -> _venue_create_dialog.html,
    # #venue-create -- included by concert_new.html/concert_edit.html/
    # import_preview.html, but NOT by tags.html, so this reads
    # concert_new_body, not tags_body. Never a <form> submit -- it POSTs by
    # fetch, so its guard is an explicit checkVariantGroups() call BEFORE
    # that fetch fires (mirrors
    # test_the_venue_dialog_checks_variants_before_its_fetch's ordering
    # check; duplicated here so this census is self-contained per route
    # rather than trusting an unrelated test to keep covering it).
    assert 'id="venue-create"' in concert_new_body, (
        "quick_create_venue: no #venue-create dialog found"
    )
    script = concert_new_body.split('id="venue-create"')[1]
    guard_at = script.find("checkVariantGroups")
    fetch_at = script.find('fetch("/tags/venue/quick"')
    assert guard_at != -1, "quick_create_venue: the dialog never calls the guard"
    assert fetch_at != -1, "quick_create_venue: the dialog never posts"
    assert guard_at < fetch_at, "quick_create_venue: the guard must run before the request"
    covered.append("quick_create_venue")

    # The census itself must stay honest: every route this file exercises
    # above has to be one of the ones actually enforced server-side, and
    # every server-side caller has to appear above -- either drift silently
    # narrows the coverage this test claims to have.
    assert sorted(covered) == sorted(CREATE_BOUNDARIES)


# --- the edit page names what is missing ---------------------------------
#
# The other side of the asymmetry. The edit routes never block, so the gap
# has to be visible somewhere -- and it is computed from CURRENT DB STATE and
# rendered on the page, not carried as a flash message (this codebase has no
# flash mechanism, and state-derived is better anyway: you see the gap while
# looking at the record, before deciding what to change, rather than after
# you save).
#
# Every setup below goes THROUGH the edit route to create the half-translated
# state, which is itself the proof that the save is still unblocked: if
# require_variants were ever added to edit_concert/edit_tag, these tests
# would fail at their setup line, not at their assertion.

GAPS = "variant-gaps"  # the notice's marker class


async def test_the_concert_edit_page_names_a_missing_language(client):
    """A concert missing only title_zh: the notice names 中文 and the title."""
    created = client.post("/concerts", data=_minimal_concert("gap-title", full_title=True))
    assert created.status_code in (200, 303), created.text

    saved = client.post("/concerts/gap-title/edit", data={
        "event_id": "gap-title",
        "title": "ラブライブ", "title_en": "Love Live", "title_zh": "",
    })
    assert saved.status_code in (200, 303), saved.text

    body = client.get("/concerts/gap-title/edit").text
    assert GAPS in body, "the edit page says nothing about the gap"
    notice = body.split(GAPS)[1][:600]
    assert "中文" in notice, notice
    assert "Title" in notice, notice
    # Only the one language is missing -- naming English too would be a lie.
    assert "English" not in notice, notice


async def test_a_fully_translated_concert_shows_no_notice(client):
    """A notice that is always there is noise nobody reads."""
    created = client.post("/concerts", data={
        **_minimal_concert("no-gaps", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        **_round("1次先行抽選", "1st-round lottery", "第一轮先行"),
    })
    assert created.status_code in (200, 303), created.text
    assert GAPS not in client.get("/concerts/no-gaps/edit").text


async def test_the_notice_names_the_row_a_per_row_gap_is_on(client):
    """"Round 2 label", not "a round label" -- and numbered the way the
    create boundary's 422 numbers it, so the two agree."""
    created = client.post("/concerts", data={
        **_minimal_concert("row-gap", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        "round_label": ["1次先行", "2次先行"],
        "round_label_en": ["1st-round lottery", "2nd-round lottery"],
        "round_label_zh": ["第一轮先行", "第二轮先行"],
        "round_kind": ["lottery_round", "lottery_round"],
        "round_closes_at": ["2026-08-01T18:00", "2026-09-01T18:00"],
        "round_opens_at": ["", ""], "round_results_at": ["", ""],
        "round_payment_at": ["", ""], "round_url": ["", ""], "round_notes": ["", ""],
    })
    assert created.status_code in (200, 303), created.text

    # Blank the SECOND round's English straight in the DB: this is exactly
    # the shape of a legacy row, and it keeps the test off the edit form's
    # dozen-parallel-array contract.
    async with client.db() as s:
        rounds = list((await s.execute(
            select(Round).order_by(Round.id)
        )).scalars())
        assert len(rounds) == 2, rounds
        rounds[1].label_en = None
        await s.commit()

    body = client.get("/concerts/row-gap/edit").text
    assert GAPS in body
    assert "Round 2 label" in body, "the notice must name which round"
    assert "Round 1 label" not in body, "round 1 is fully translated"


async def test_the_tag_edit_dialog_names_its_own_gap(client):
    created = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "莲之空", "kind": "group",
    })
    assert created.status_code in (200, 303), created.text
    assert GAPS not in client.get("/tags").text, "a full tag needs no notice"

    async with client.db() as s:
        tag_id = (await s.execute(select(Tag.id))).scalars().one()
    saved = client.post(f"/tags/{tag_id}/edit", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "",
    })
    assert saved.status_code in (200, 303), saved.text

    body = client.get("/tags").text
    assert GAPS in body, "the tag dialog says nothing about the gap"
    notice = body.split(GAPS)[1][:400]
    assert "中文" in notice, notice
    assert "Name" in notice, notice


# --- the untranslated backlog is countable -------------------------------
#
# The per-tag notice above tells you about the tag you happen to have open.
# The Tags page needs the other reading: how much is left. The count is
# computed in db/service.py's tag_directory_context alongside the other
# summary counts (not in the template, not in the route) and rendered only
# when it is non-zero -- a permanent "0 tags" is noise, and its ABSENCE is
# the signal that the backlog is clear.
#
# It counts the NAME trio only. A VENUE's city trio is deliberately excluded:
# city is all-or-nothing and optional, so a venue with no city at all is
# legitimately complete, and folding it in would make one number mean two
# different things.

BACKLOG = "untranslated-count"  # the count's marker class


def _full_tag(client, name: str, kind: str = "group") -> None:
    created = client.post("/tags", data={
        "name": name, "name_en": f"{name}-en", "name_zh": f"{name}-zh", "kind": kind,
    })
    assert created.status_code in (200, 303), created.text


async def _break_zh(client, name: str) -> None:
    """Blank one variant THROUGH the edit route -- which is also the proof
    that the edit path is still unblocked."""
    async with client.db() as s:
        tag_id = (await s.execute(select(Tag.id).where(Tag.name == name))).scalar_one()
    saved = client.post(f"/tags/{tag_id}/edit", data={
        "name": name, "name_en": f"{name}-en", "name_zh": "",
    })
    assert saved.status_code in (200, 303), saved.text


async def test_a_fully_translated_catalogue_shows_no_count(client):
    _full_tag(client, "alpha")
    _full_tag(client, "beta")
    assert BACKLOG not in client.get("/tags").text, "zero is not worth showing"


async def test_the_tags_page_counts_untranslated_tags(client):
    _full_tag(client, "alpha")
    _full_tag(client, "beta")
    await _break_zh(client, "alpha")

    body = client.get("/tags").text
    assert BACKLOG in body, "the Tags page never mentions the backlog"
    note = body.split(BACKLOG)[1][:300]
    assert "1 tag" in note, note
    assert "2 tag" not in note, note


async def test_the_count_is_the_number_of_tags_with_a_gap(client):
    _full_tag(client, "alpha")
    _full_tag(client, "beta")
    _full_tag(client, "gamma")
    await _break_zh(client, "alpha")
    await _break_zh(client, "beta")

    note = client.get("/tags").text.split(BACKLOG)[1][:300]
    assert "2 tags" in note, note


async def test_a_venue_without_a_city_is_not_counted_as_untranslated(client):
    """City is optional and all-or-nothing; a venue with none is complete."""
    _full_tag(client, "Zepp", kind="venue")
    assert BACKLOG not in client.get("/tags").text, "a blank city is not a gap"


async def test_the_backlog_count_lives_in_the_service_summary(client):
    """Computed alongside the other summary counts, not in the template."""
    from app.db import service

    _full_tag(client, "alpha")
    await _break_zh(client, "alpha")
    async with client.db() as s:
        ctx = await service.tag_directory_context(s)
    assert ctx["summary"]["untranslated"] == 1
