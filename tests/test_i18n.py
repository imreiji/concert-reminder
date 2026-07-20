"""app.i18n: catalogue loading, ContextVar locale, fallbacks."""

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import i18n
from app.db.models import Base, User


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def test_supported_set():
    assert i18n.SUPPORTED == ("en", "zh", "ja")


def test_default_locale_is_en():
    assert i18n.get_locale() == "en"


def test_set_locale_rejects_unknown_to_en():
    i18n.set_locale("fr")
    assert i18n.get_locale() == "en"
    i18n.set_locale("ja")
    assert i18n.get_locale() == "ja"
    i18n.set_locale("en")


def test_gettext_identity_for_en():
    i18n.set_locale("en")
    assert i18n.gettext("Up next") == "Up next"


def test_gettext_missing_msgid_falls_back_to_english():
    # Catalogues are empty at this point in the branch; any msgid falls through.
    i18n.set_locale("ja")
    try:
        assert i18n.gettext("No such string anywhere") == "No such string anywhere"
    finally:
        i18n.set_locale("en")


def test_ngettext_english_plurals():
    i18n.set_locale("en")
    assert i18n.ngettext("{n} day", "{n} days", 1) == "{n} day"
    assert i18n.ngettext("{n} day", "{n} days", 3) == "{n} days"


def test_n_marker_is_identity():
    assert i18n.N_("payment due") == "payment due"


def test_negotiate_accept_language():
    assert i18n.negotiate("ja,en-US;q=0.9") == "ja"
    assert i18n.negotiate("zh-TW,zh;q=0.9") == "zh"
    assert i18n.negotiate("zh-CN") == "zh"
    assert i18n.negotiate("en-GB,en;q=0.8") == "en"
    assert i18n.negotiate("fr-FR,de;q=0.5") == "en"
    assert i18n.negotiate("") == "en"


def test_loaded_translation_is_used():
    """A real (temp) catalogue entry translates; proves the po->mo->Translations path."""
    import textwrap

    from babel.messages.pofile import read_po  # noqa: F401  (import sanity)

    po = textwrap.dedent('''
        msgid ""
        msgstr ""
        "Content-Type: text/plain; charset=utf-8\\n"
        "Plural-Forms: nplurals=1; plural=0;\\n"

        msgid "Up next"
        msgstr "次はこれ"
    ''')
    i18n._catalog_cache["ja"] = i18n._translations_from_po_text(po, "ja")
    i18n.set_locale("ja")
    try:
        assert i18n.gettext("Up next") == "次はこれ"
    finally:
        i18n.set_locale("en")
        i18n.reset_catalog_cache()


@pytest.mark.asyncio
async def test_user_language_defaults_to_en(session):
    user = User(discord_id=1, username="alice")
    session.add(user)
    await session.commit()
    got = await session.get(User, 1)
    assert got.language == "en"
