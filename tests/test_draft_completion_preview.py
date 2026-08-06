"""Evidence renders where the proofreading happens, and the paste fallback
works when everything else declined.

Same fixture shape as tests/test_draft_completion_web.py -- this suite has no
shared conftest fixture for an HTTP client, so this file builds its own
db/client/session/admin_client set from the same TestClient + OAuth-stub
pattern, plus `admin_user_id` so a seeded PendingDraft's `created_by` can
match whichever discord id `admin_client` actually signed in as.
"""

import re

import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, PendingDraft
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
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
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
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


@pytest.fixture()
def admin_client(client):
    login_as(client, ADMIN_ID, "admin")
    return client


@pytest.fixture()
def editor_client(client):
    login_as(client, EDITOR_ID, "editor")
    return client


@pytest.fixture()
def admin_user_id():
    return ADMIN_ID


@pytest_asyncio.fixture()
async def session(db):
    async with db() as s:
        yield s


COMPLETED = """\
title: 例）ライブ
performances:
- label: Day 1
  venue: Zepp Haneda
rounds:
- label: 1次先行抽選
  kind: lottery
  apply_closes_jst: 2026-01-10 23:59
"""

RECORD = yaml.safe_dump(
    {
        "source_url": "https://eplus.jp/x",
        "evidence": [{"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}],
        "rejected": ["round '2次先行': the quote for apply_closes_jst is not on the page"],
    },
    allow_unicode=True,
)


async def _seed(session, user_id, **over):
    draft_text = over.pop("draft_text", COMPLETED)
    row = PendingDraft(draft_text=draft_text, title="t", created_by=user_id, **over)
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_evidence_renders_beside_the_round_it_grounds(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id, completion_yaml=RECORD)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    assert "申込締切 2026年1月10日(土)23:59" in body


@pytest.mark.asyncio
async def test_a_rejected_round_is_reported_on_the_preview(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id, completion_yaml=RECORD)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    assert "is not on the page" in body


@pytest.mark.asyncio
async def test_a_real_source_url_renders_as_a_clickable_link(
    admin_client, session, admin_user_id
):
    # RECORD's source_url is a genuine https:// URL -- it must reach an
    # href, not just appear as text.
    row = await _seed(session, admin_user_id, completion_yaml=RECORD)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    assert 'href="https://eplus.jp/x"' in body


@pytest.mark.asyncio
async def test_a_non_url_source_is_shown_as_text_never_a_broken_link(
    admin_client, session, admin_user_id
):
    """The paste route stores the literal "(pasted by hand)" as source_url --
    not a URL at all. It must still say where the rounds came from (invariant
    7 is about the LINK, not the sentence), but never as an href: a relative
    "(pasted by hand)" link would resolve under /concerts/import/pending/ and
    404 if anyone clicked it."""
    record = yaml.safe_dump(
        {
            "source_url": "(pasted by hand)",
            "evidence": [{"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}],
            "rejected": ["round '2次先行': the quote for apply_closes_jst is not on the page"],
        },
        allow_unicode=True,
    )
    row = await _seed(session, admin_user_id, completion_yaml=record)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    assert "(pasted by hand)" in body
    assert 'href="(pasted by hand)"' not in body


@pytest.mark.asyncio
async def test_a_draft_with_no_completion_record_renders_no_evidence_or_rejection_ui(
    admin_client, session, admin_user_id
):
    """Renamed from a name that promised more than the assertion checked
    ("...renders exactly as before" with no comparison to any prior render).
    Pins what actually matters: neither the per-round evidence block nor the
    rejected-rounds banner appears when completion_yaml is empty, and the
    page renders normally rather than erroring."""
    row = await _seed(session, admin_user_id)
    r = admin_client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    assert "evidence-quote" not in r.text
    # The rejected-banner's own copy, from import_preview.html -- absent
    # confirms the banner itself didn't render, not just that it was empty.
    assert "The AI proposed these" not in r.text


@pytest.mark.asyncio
async def test_a_round_added_after_completion_gets_no_evidence_and_no_crash(
    admin_client, session, admin_user_id
):
    """`evidence` is positional against `parsed.rounds`; an operator can add a
    round to the draft text AFTER a completion pass ran (draft_text is
    re-parsed fresh on every GET), which leaves round 1 with no matching
    evidence entry at all. Must render 200, not index past the list."""
    draft_with_extra_round = COMPLETED + (
        "- label: 2次先行抽選\n  kind: lottery\n  apply_closes_jst: 2026-01-20 23:59\n"
    )
    row = await _seed(
        session, admin_user_id, draft_text=draft_with_extra_round, completion_yaml=RECORD
    )
    r = admin_client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    # The grounded round still shows its quote...
    assert "申込締切 2026年1月10日(土)23:59" in r.text
    # ...and the round nobody completed shows none, not a stale/misaligned one.
    assert r.text.count("evidence-quote") == 1


@pytest.mark.asyncio
async def test_a_round_removed_after_completion_does_not_crash(
    admin_client, session, admin_user_id
):
    """The reverse of the above: the draft now has FEWER rounds than the
    evidence list has entries (an operator deleted the round). The extra
    evidence entries simply go unused rather than raising or misattaching."""
    no_rounds = (
        "title: 例）ライブ\nperformances:\n- label: Day 1\n  venue: Zepp Haneda\nrounds: []\n"
    )
    row = await _seed(session, admin_user_id, draft_text=no_rounds, completion_yaml=RECORD)
    r = admin_client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    assert "evidence-quote" not in r.text


@pytest.mark.asyncio
async def test_a_completion_record_with_the_wrong_shape_does_not_crash(
    admin_client, session, admin_user_id
):
    """`completion_yaml` parses as valid YAML but not as the shape
    `complete_one` writes -- e.g. `evidence` came back a string instead of a
    list. This can only happen to a hand-corrupted row (only `complete_one`
    ever writes this column), but the render must still degrade to "nothing
    to show" rather than 500, exactly as an unparseable document does."""
    malformed = yaml.safe_dump({"source_url": "https://eplus.jp/x", "evidence": "oops"})
    row = await _seed(session, admin_user_id, completion_yaml=malformed)
    r = admin_client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    assert "evidence-quote" not in r.text


@pytest.mark.asyncio
async def test_an_evidence_list_with_a_non_dict_element_does_not_crash(
    admin_client, session, admin_user_id
):
    """The outer-list check alone isn't enough: `evidence: ["oops"]` IS a
    list, so it passes that check, but round_card calls `.items()` on each
    element -- a bare string has no `.items()`. The bad entry must read as
    "no evidence for this round" rather than 500 the whole page, and it must
    not shift a LATER, well-formed entry's position (positional alignment)."""
    malformed = yaml.safe_dump(
        {
            "source_url": "https://eplus.jp/x",
            "evidence": ["oops", {"apply_closes_jst": "申込締切 2026年1月10日(土)23:59"}],
        },
        allow_unicode=True,
    )
    draft_with_two_rounds = COMPLETED + (
        "- label: 2次先行抽選\n  kind: lottery\n  apply_closes_jst: 2026-01-20 23:59\n"
    )
    row = await _seed(
        session, admin_user_id, draft_text=draft_with_two_rounds, completion_yaml=malformed
    )
    r = admin_client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    # Round 0's bad entry shows nothing; round 1's good entry, at its
    # correct (unshifted) index, still shows its quote.
    assert r.text.count("evidence-quote") == 1
    assert "申込締切 2026年1月10日(土)23:59" in r.text


@pytest.mark.asyncio
async def test_the_concert_editor_surfaces_render_no_evidence_block(admin_client):
    # The round card is shared with concert_new/concert_edit. Neither passes
    # evidence, and neither may grow a block because this feature exists.
    body = admin_client.get("/concerts/new").text
    assert "evidence-quote" not in body


@pytest.mark.asyncio
async def test_the_paste_fold_wraps_its_content_in_fold_body(
    admin_client, session, admin_user_id, monkeypatch
):
    """Every other details.fold in this template wraps its content in a
    .fold-body div, which is what supplies the padding (the bare details.fold
    has a border and none). Without it the paragraph/textarea/button sit
    flush against the border."""
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    row = await _seed(session, admin_user_id)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    m = re.search(
        r'<details class="fold" data-fold="paste-page">.*?</details>', body, re.DOTALL
    )
    assert m, "no paste-page fold on the page"
    fold_html = m.group(0)
    assert 'class="fold-body"' in fold_html
    # And the form/textarea/button must be INSIDE that div, not just present
    # somewhere in the fold.
    assert re.search(
        r'class="fold-body">\s*<form.*?</form>\s*</div>', fold_html, re.DOTALL
    ), fold_html


@pytest.mark.asyncio
async def test_pasting_a_page_completes_the_draft(
    admin_client, session, admin_user_id, monkeypatch
):
    from app.llm import LlmReply

    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def fake_chat(system, user, **kw):
        assert "申込締切" in user  # the pasted page reached the prompt
        return LlmReply(
            text=(
                "rounds:\n  - label: 1次先行抽選\n    kind: lottery\n"
                "    apply_closes_jst: 2026-01-10 23:59\n"
                "    evidence:\n"
                '      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"\n'
            ),
            tokens_in=10,
            tokens_out=5,
        )

    monkeypatch.setattr("app.draft_completion.llm.chat", fake_chat)
    row = await _seed(session, admin_user_id)
    row.draft_text = "title: x\nperformances: []\nrounds: []\n"
    await session.commit()

    # Captured BEFORE expire_all(): that call expires every attribute of
    # every object in this session, PRIMARY KEY INCLUDED on this aiosqlite
    # stack (CLAUDE.md's own documented gotcha, from the triage runner's
    # post-rollback bookkeeping) -- row.id read AFTER expiry would trigger a
    # lazy load outside the greenlet SQLAlchemy's async path requires, which
    # raises MissingGreenlet rather than a value.
    row_id = row.id
    r = admin_client.post(
        f"/concerts/import/pending/{row_id}/complete",
        data={"page_text": "1次先行抽選 申込締切 2026年1月10日(土)23:59"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    refreshed = await session.get(PendingDraft, row_id)
    assert yaml.safe_load(refreshed.draft_text)["rounds"][0]["label"] == "1次先行抽選"


@pytest.mark.asyncio
async def test_an_oversized_paste_is_refused_before_any_call(
    admin_client, session, admin_user_id, monkeypatch
):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def explode(*a, **kw):
        raise AssertionError("an oversized paste must not reach the model")

    monkeypatch.setattr("app.draft_completion.llm.chat", explode)
    row = await _seed(session, admin_user_id)
    r = admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "x" * 150_001},
        follow_redirects=False,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_an_empty_paste_is_refused(admin_client, session, admin_user_id, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    row = await _seed(session, admin_user_id)
    r = admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "   "},  # whitespace-only, same as nothing pasted
        follow_redirects=False,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_pressing_it_with_the_flag_off_404s(
    admin_client, session, admin_user_id, monkeypatch
):
    monkeypatch.setattr("app.config.settings.triage_enabled", False)
    row = await _seed(session, admin_user_id)
    r = admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "some page text"},
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_plain_editor_pressing_it_gets_403(editor_client, session, monkeypatch):
    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    row = await _seed(session, EDITOR_ID)
    r = editor_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "some page text"},
        follow_redirects=False,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_draft_that_is_not_yours_404s(admin_client, session, monkeypatch):
    from app.db.service import ensure_user

    monkeypatch.setattr("app.config.settings.triage_enabled", True)
    # Seeded under a DIFFERENT discord id than admin_client signed in as --
    # created_by is an FK to users.discord_id, so that user must exist first
    # (a real one would, from having logged in at least once themselves).
    await ensure_user(session, EDITOR_ID, "someone-else")
    row = await _seed(session, EDITOR_ID)
    r = admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "some page text"},
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_an_llm_error_from_the_provider_is_a_502_not_a_traceback(
    admin_client, session, admin_user_id, monkeypatch
):
    from app.llm import LlmError

    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def fails(*a, **kw):
        raise LlmError("the provider returned a non-200 response")

    monkeypatch.setattr("app.draft_completion.llm.chat", fails)
    row = await _seed(session, admin_user_id)
    r = admin_client.post(
        f"/concerts/import/pending/{row.id}/complete",
        data={"page_text": "some page text"},
        follow_redirects=False,
    )
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_an_unusable_model_reply_redirects_and_is_not_rebilled(
    admin_client, session, admin_user_id, monkeypatch
):
    """CompletionResponseError: the reply as a WHOLE isn't a YAML mapping.
    complete_one already wrote and flushed completion_yaml recording this
    before re-raising -- the route must commit that (a redirect, not a 500)
    or the SAME unusable reply gets billed again on the very next press."""
    from app.db.service import completion_candidates
    from app.llm import LlmReply

    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def bad_reply(system, user, **kw):
        # Parses as YAML, but as a scalar string, not a mapping --
        # parse_completion_response's "expected a YAML mapping" branch.
        return LlmReply(text="just a plain string, not a mapping", tokens_in=5, tokens_out=2)

    monkeypatch.setattr("app.draft_completion.llm.chat", bad_reply)
    row = await _seed(session, admin_user_id)
    row.draft_text = "title: x\nperformances: []\nrounds: []\n"
    await session.commit()
    row_id = row.id

    r = admin_client.post(
        f"/concerts/import/pending/{row_id}/complete",
        data={"page_text": "some ticket page text"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    session.expire_all()
    refreshed = await session.get(PendingDraft, row_id)
    assert refreshed.completion_yaml != ""
    assert "could not be used" in refreshed.completion_yaml

    # The mark survived the commit, so a second press has nothing left to
    # retry: this row must not come back as a candidate.
    candidates = await completion_candidates(session, admin_user_id)
    assert row_id not in [r.id for r in candidates]


@pytest.mark.asyncio
async def test_a_corrupted_stored_draft_redirects_and_is_not_rebilled(
    admin_client, session, admin_user_id, monkeypatch
):
    """DraftMergeError: the STORED draft (not the reply) can't be read back
    as a mapping -- a hand-corrupted row. Same contract as the reply-side
    failure above: complete_one already flushed the rejection, so the route
    must commit rather than let it roll back and re-bill next press."""
    from app.db.service import completion_candidates
    from app.llm import LlmReply

    monkeypatch.setattr("app.config.settings.triage_enabled", True)

    async def valid_reply(system, user, **kw):
        return LlmReply(
            text="rounds:\n  - label: 1次先行抽選\n    kind: lottery\n"
            "    apply_closes_jst: 2026-01-10 23:59\n",
            tokens_in=5,
            tokens_out=2,
        )

    monkeypatch.setattr("app.draft_completion.llm.chat", valid_reply)
    row = await _seed(session, admin_user_id)
    # Not a YAML mapping at all -- merge_rounds refuses to touch it.
    row.draft_text = "just a plain string, not a mapping"
    await session.commit()
    row_id = row.id

    r = admin_client.post(
        f"/concerts/import/pending/{row_id}/complete",
        data={"page_text": "some ticket page text"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    session.expire_all()
    refreshed = await session.get(PendingDraft, row_id)
    assert refreshed.completion_yaml != ""
    assert "could not be used" in refreshed.completion_yaml

    candidates = await completion_candidates(session, admin_user_id)
    assert row_id not in [r.id for r in candidates]
