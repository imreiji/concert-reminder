"""Locale plumbing: gettext catalogues + the active-locale ContextVar.

Top-level (not domain/ -- it does file I/O at startup; not web/ -- the bot
imports it too). `en` is the source language and maps to NullTranslations,
so the default locale is the identity function: English output is
byte-identical to the pre-i18n app, which is what keeps the existing
exact-substring render tests green.

Catalogues are .po files compiled to .mo IN MEMORY at first use -- no .mo
on disk, no compile step in the deploy ritual.
"""

from contextvars import ContextVar
from io import BytesIO
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import NullTranslations, Translations

SUPPORTED = ("en", "zh", "ja")

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"
_active_locale: ContextVar[str] = ContextVar("active_locale", default="en")
_null = NullTranslations()
_catalog_cache: dict[str, NullTranslations] = {}


def get_locale() -> str:
    return _active_locale.get()


def set_locale(locale: str) -> None:
    """Set the active locale; anything unsupported falls back to en."""
    _active_locale.set(locale if locale in SUPPORTED else "en")


def _translations_from_po_text(po_text: str, locale: str) -> Translations:
    catalog = read_po(BytesIO(po_text.encode("utf-8")), locale=locale)
    buf = BytesIO()
    write_mo(buf, catalog)
    buf.seek(0)
    return Translations(fp=buf)


def _load(locale: str) -> NullTranslations:
    po_path = _TRANSLATIONS_DIR / locale / "LC_MESSAGES" / "messages.po"
    if not po_path.exists():
        return _null
    return _translations_from_po_text(po_path.read_text(encoding="utf-8"), locale)


def _catalog() -> NullTranslations:
    locale = _active_locale.get()
    if locale == "en":
        return _null
    if locale not in _catalog_cache:
        _catalog_cache[locale] = _load(locale)
    return _catalog_cache[locale]


def reset_catalog_cache() -> None:
    """Test hook: force catalogues to reload from disk."""
    _catalog_cache.clear()


def gettext(message: str) -> str:
    return _catalog().gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _catalog().ngettext(singular, plural, n)


def N_(message: str) -> str:
    """No-op extraction marker for module-level dicts (translated at lookup)."""
    return message


_ = gettext


def negotiate(accept_language: str) -> str:
    """First supported language in an Accept-Language header, else en.

    Deliberately simple: entries arrive in the browser's preference order
    in practice, so q-values are not re-sorted. Any zh-* variant maps to zh.
    """
    for part in accept_language.split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        primary = code.split("-")[0]
        if primary in SUPPORTED:
            return primary
    return "en"
