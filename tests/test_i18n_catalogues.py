"""Catalogue completeness: every extracted msgid is translated in ja and zh.

This is the CI guard against orphaned entries. It extracts msgids in-process
the same way `pybabel extract` does (same method_map/keywords as babel.cfg),
then asserts each one is present AND non-empty in both shipped catalogues.
Plural entries carry a tuple msgstr under nplurals=1 -- every member must be
non-empty.
"""

from io import BytesIO
from pathlib import Path

from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po

ROOT = Path(__file__).parent.parent
METHOD_MAP = [
    ("src/app/**.py", "python"),
    ("src/app/web/templates/**.html", "jinja2"),
]
KEYWORDS = {"_": None, "gettext": None, "N_": None, "ngettext": (1, 2)}

# msgids deliberately left untranslated (should stay empty; every addition
# needs a comment saying why). Intentionally empty -- everything is translated.
WHITELIST: set[str] = set()


def _extracted_msgids() -> set[str]:
    found: set[str] = set()
    for _fname, _lineno, message, _comments, _ctx in extract_from_dir(
        str(ROOT), method_map=METHOD_MAP, keywords=KEYWORDS
    ):
        found.add(message if isinstance(message, str) else message[0])
    return found


def _translated_msgids(locale: str) -> set[str]:
    """The set of msgids that are fully translated in `locale`.

    A plain entry counts when its msgstr is non-empty; a plural entry counts
    only when every plural member is non-empty.
    """
    po = ROOT / "src" / "app" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    catalog = read_po(BytesIO(po.read_bytes()), locale=locale)
    done: set[str] = set()
    for m in catalog:
        if not m.id:
            continue
        msgid = m.id if isinstance(m.id, str) else m.id[0]
        string = m.string
        if isinstance(string, (list, tuple)):
            translated = bool(string) and all(bool(s) for s in string)
        else:
            translated = bool(string)
        if translated:
            done.add(msgid)
    return done


def _assert_complete(locale: str) -> None:
    extracted = _extracted_msgids() - WHITELIST
    translated = _translated_msgids(locale)
    missing = extracted - translated
    assert not missing, (
        f"untranslated in {locale} ({len(missing)}): {sorted(missing)[:20]}"
    )


def test_ja_catalogue_complete():
    _assert_complete("ja")


def test_zh_catalogue_complete():
    _assert_complete("zh")
