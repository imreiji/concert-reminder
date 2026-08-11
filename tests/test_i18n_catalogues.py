"""Catalogue completeness: every extracted msgid is translated in ja and zh.

This is the CI guard against orphaned entries. It extracts msgids in-process
the same way `pybabel extract` does (same method_map/keywords as babel.cfg),
then asserts each one is present AND non-empty in both shipped catalogues.
Plural entries carry a tuple msgstr under nplurals=1 -- every member must be
non-empty.

It also guards placeholder INTEGRITY: a translation must carry exactly the
placeholders its msgid does. This matters doubly for the sentence-slot
patterns (welcome/preferences reminder builders), where a dropped {direction}
or {hours} would silently render a select-less form whose POST is missing a
field -- split_slots only raises on UNKNOWN slots, so this test is the guard
for DROPPED ones.
"""

import re
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
        # A fuzzy entry is dropped at compile (i18n.py compiles with
        # write_mo(use_fuzzy=False)), so the page renders English. Treat it as
        # untranslated so `pybabel update` auto-marking a changed msgid fuzzy
        # fails the guard instead of silently shipping English.
        if m.fuzzy:
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


# An `id="..."` counts as a placeholder too, and for the same reason: a few
# msgids carry markup the PAGE addresses by id (concert_detail.html's clear
# dialog wraps the round label in `<b id="clearNameLeg">` and fills it with
# `getElementById(...).textContent = label`). A translator reflowing that
# sentence -- CJK word order moves the label -- can drop the wrapper without
# dropping any word, and nothing else notices: the id test renders English
# only, and the capture-phase submit guard has already called preventDefault()
# by the time `getElementById` returns null, so the button is silently dead for
# that language alone.
#
# It is a guard rather than a hoist because the label is a grammatical
# constituent inside the sentence in all three languages (a genitive head in
# ja, the object of 在 in zh), so lifting it out of the trans block would mean
# rewriting the copy into a label line plus a sentence with no antecedent --
# and would fix only these two strings, while the guard covers the next one too.
_PLACEHOLDER = re.compile(
    r"%\([a-zA-Z_][a-zA-Z0-9_]*\)[sd]|\{[a-zA-Z_][a-zA-Z0-9_.:]*\}|id=\"[^\"]*\""
)


def _assert_placeholders_intact(locale: str) -> None:
    po = ROOT / "src" / "app" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    catalog = read_po(BytesIO(po.read_bytes()), locale=locale)
    bad: list[str] = []
    for m in catalog:
        if not m.id or not m.string:
            continue
        ids = m.id if isinstance(m.id, tuple) else (m.id,)
        strs = m.string if isinstance(m.string, tuple) else (m.string,)
        # Plural pairs collapse to one CJK form: it must carry the UNION of
        # the two English forms' placeholders (in practice both forms use the
        # same set).
        want: set[str] = set()
        for i in ids:
            want |= set(_PLACEHOLDER.findall(i))
        for s in strs:
            if s and set(_PLACEHOLDER.findall(s)) != want:
                bad.append(f"{ids[0][:60]!r} -> {s[:60]!r}")
    assert not bad, f"placeholder mismatch in {locale} ({len(bad)}): {bad[:10]}"


def test_ja_placeholders_intact():
    _assert_placeholders_intact("ja")


def test_zh_placeholders_intact():
    _assert_placeholders_intact("zh")


def test_the_placeholder_pattern_counts_html_ids_and_still_counts_the_rest():
    """Asserted on the PATTERN itself, because the two catalogue tests above
    read a variable and cannot see a fault in its definition: with the
    `id="..."` branch missing they both keep passing, and the only symptom is a
    dead button in one language.

    Mutation this catches: dropping the `id=\"[^\"]*\"` alternative (the ids go
    uncounted), and equally dropping either of the two it was added beside --
    the branch is an ADDITION to the brace/percent forms, not a replacement."""
    found = set(_PLACEHOLDER.findall(
        'You recorded a ticket for <b id="clearNameLeg">this round</b>, '
        "{n} of %(count)s."
    ))
    assert found == {'id="clearNameLeg"', "{n}", "%(count)s"}
