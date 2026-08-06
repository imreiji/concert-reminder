"""Evidence renders where the proofreading happens, and the paste fallback
works when everything else declined.

Same fixture shape as tests/test_draft_completion_web.py -- this suite has no
shared conftest fixture for an HTTP client, so this file builds its own
db/client/session/admin_client set from the same TestClient + OAuth-stub
pattern, plus `admin_user_id` so a seeded PendingDraft's `created_by` can
match whichever discord id `admin_client` actually signed in as.
"""

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
async def test_a_draft_with_no_completion_record_renders_exactly_as_before(
    admin_client, session, admin_user_id
):
    row = await _seed(session, admin_user_id)
    body = admin_client.get(f"/concerts/import/pending/{row.id}").text
    assert body.count("evidence-quote") == 0


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
async def test_the_concert_editor_surfaces_render_no_evidence_block(admin_client):
    # The round card is shared with concert_new/concert_edit. Neither passes
    # evidence, and neither may grow a block because this feature exists.
    body = admin_client.get("/concerts/new").text
    assert "evidence-quote" not in body


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
