"""XSS regression tests for editor-authored strings rendered into HTML.

Tag names are editor-authored free text and are rendered in a hostile
position: inside a <script> block (the shared tag picker's JS constants),
where plain HTML-escaping is NOT sufficient, so the picker gets dedicated
tests here rather than relying on the feature tests that only cover the
happy path.

The second half of the file is a repo-wide SWEEP for invariant 7's third
rule -- never interpolate user-controlled text into an inline `on*` handler.
That rule was enforced per page by whoever remembered; the sweep makes it a
property of the template directory.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app
from app.web.routes import imports as import_routes

EDITOR_ID = 42
FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"

# Breaks out of a <script> block: the HTML tokenizer ends the script at the
# literal "</" regardless of JS string quoting, so json.dumps alone is not enough.
SCRIPT_PAYLOAD = "</script><img src=x onerror=alert(1)>"


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


# ── Helpers ──────────────────────────────────────────────────────────────

_CONST_RE = re.compile(r"const (NC_GROUPS|TAG_NAMES|INITIAL_SELECTED) = (.*);")


def js_constants(html: str) -> dict:
    """Parse the picker's three JS constants back out of the rendered page."""
    return {m.group(1): json.loads(m.group(2)) for m in _CONST_RE.finditer(html)}


def assert_script_block_intact(html: str) -> None:
    assert SCRIPT_PAYLOAD not in html, "payload rendered literally: script block broken out of"
    assert html.count("<script") == html.count("</script"), "unbalanced script tags"


def seed_payload_group(client) -> None:
    """A GROUP tag lands in both NC_GROUPS and TAG_NAMES, covering both blobs."""
    r = client.post("/tags", data={
        "name_en": SCRIPT_PAYLOAD, "name_zh": SCRIPT_PAYLOAD, "name": SCRIPT_PAYLOAD,
        "kind": "group",
    })
    assert r.status_code == 303


def picker_pages(client) -> dict[str, str]:
    """Every page that includes _tag_picker_script.html, rendered."""
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    new = client.get("/concerts/new")
    edit = client.get("/concerts/c/edit")

    async def fake_fetch(url: str) -> str:
        return (FIXTURES / "ramen_graduation_concert.html").read_text(encoding="utf-8")

    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)
    preview = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    for r in (new, edit, preview):
        assert r.status_code == 200
    return {"/concerts/new": new.text, "/concerts/c/edit": edit.text, "preview": preview.text}


# ── The tag picker's JS constants ────────────────────────────────────────


def test_tag_picker_constants_escape_script_terminator(client):
    login_as(client, EDITOR_ID, "reiji")
    seed_payload_group(client)
    for page, html in picker_pages(client).items():
        assert_script_block_intact(html)
        # The name must still be THERE, just inert -- an escaping fix that
        # silently dropped the tag would otherwise pass the check above.
        assert "alert(1)" in html, f"{page}: payload vanished entirely"
        assert "\\u003c/script\\u003e" in html, f"{page}: '<' not escaped"


def test_tag_picker_constants_are_objects_not_strings(client):
    """Regression guard for the double-encoding trap: `| tojson` serializes the
    Python object, so the producers must pass raw dicts. Pre-serializing with
    json.dumps first yields a JS *string* -- still perfectly escaped, so a
    pure escaping test cannot catch it, but the picker is silently dead."""
    login_as(client, EDITOR_ID, "reiji")
    seed_payload_group(client)
    for page, html in picker_pages(client).items():
        consts = js_constants(html)
        assert set(consts) == {"NC_GROUPS", "TAG_NAMES", "INITIAL_SELECTED"}, page
        for name, value in consts.items():
            assert isinstance(value, dict), f"{page}: {name} deserialized to {type(value)}"


def test_tag_picker_group_members_still_populate(client):
    """The picker's whole job: NC_GROUPS maps group id -> members."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "group",
    })
    client.post("/tags", data={
        "name_en": "Kozue Otomune", "name_zh": "Kozue Otomune", "name": "Kozue Otomune",
        "kind": "artist",
    })
    client.post("/tags/1/members", data={"member_tag_id": 2})
    consts = js_constants(client.get("/concerts/new").text)
    assert consts["NC_GROUPS"]["1"]["members"] == [{"id": 2, "name": "Kozue Otomune"}]
    assert consts["TAG_NAMES"]["1"] == "Hasunosora"


# ── Invariant 7, third rule: what may reach an inline on* handler ────────
#
# The browser HTML-decodes an attribute BEFORE parsing it as JS, so Jinja's
# `&#39;` escaping does not protect an `onclick="f('{{ x }}')"`. A tag name
# containing an apostrophe closes the JS string literal; one containing
# `')` closes the call. Autoescaping is doing its job and the page is still
# executing attacker text.
#
# A blanket "no {{ }} inside on*" ban would be useless -- the handlers that
# exist are legitimate. The rule that separates them is SHAPE: everything
# interpolated into an on* attribute must be a BARE dotted path whose last
# segment is in INTEGER_ID_SEGMENTS -- an explicit ALLOWLIST of segments known
# to be integer columns, today just `id`, and see the note beside it for why a
# `*_id` SUFFIX rule is not good enough -- or be explicitly allowlisted in
# ON_HANDLER_ALLOWLIST below. Names, titles, labels and preset names --
# anything an editor or a user typed -- must reach the DOM through a `data-`
# attribute and be read back via `dataset`, which is what `data-tag-name` /
# `data-preset-name` and the `data-confirm` handlers already do.

TEMPLATES = Path(__file__).parent.parent / "src" / "app" / "web" / "templates"

# `\b` before `on` is load-bearing, but NOT for the reason you would guess:
# `action=` is safe from `on[a-z]+=` either way, because `[a-z]+` demands at
# least one letter between `on` and `=` and `actiON=` has none. (`on[a-z]*=`
# with a STAR does match it -- that near-miss is what produced an early
# estimate of 58 handlers in this app when the real count is 5.)
#
# What the boundary actually excludes is the `on` buried mid-word in front of
# a real `=` and a real quote. Measured against this repo, three shapes:
#   .textContent = ""                 -> `ontent = ""`
#   .dataset.clearConfirmed = "1"     -> `onfirmed = "1"`
#   data-confirm="{{ _('Delete?') }}" -> `onfirm="{{ _('Delete?') }}"`
# Only the third carries an interpolation, and it is the `data-confirm` idiom
# invariant 7 PRESCRIBES -- so dropping the `\b` does not merely add noise, it
# reports the recommended remedy as the violation. That is why the sample in
# test_the_handler_sweep_sees_handlers_and_tells_names_from_ids carries a
# `data-confirm` line.
_ON_ATTR = re.compile(r"""\bon[a-z]+\s*=\s*"([^"]*)\"""", re.S)
_INTERPOLATION = re.compile(r"\{\{(.*?)\}\}", re.S)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
# A bare dotted path and nothing else. A filter, a call, a literal or an
# inline expression is not id-shaped BY CONSTRUCTION and has to be argued
# for in the allowlist rather than slipping through on its last word.
_DOTTED_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")

ON_HANDLER_ALLOWLIST = {
    # _tag_picker_script.html: `{% for kindname in ["franchise", "group",
    # "character", "artist"] %}`, a literal list three lines above its uses.
    # Template-controlled, never a DB value, so it cannot carry a quote or a
    # paren. If that loop ever iterates something fetched, this entry is wrong.
    "kindname",
}

# The final path segment an id-shaped expression may carry: an explicit SET,
# deliberately NOT an `endswith("_id")` suffix rule.
#
# `Concert.event_id` is the standing counterexample and it is already in this
# schema: an editor-TYPED STRING that sails straight through a suffix test.
# It is safe in an href today only because EVENT_ID_RE (routes/concerts.py)
# happens to forbid quotes and parens -- safe by accident, which is precisely
# what a repo-wide sweep exists to stop the codebase relying on. A suffix rule
# would also wave through the next string-valued `*_id` column with no warning
# at all, leaving the one known gap for the next per-page reviewer to remember,
# which is the state this sweep replaced.
#
# `id` is the only segment any handler in the app uses (tags.html's two dialog
# closers). A new one is a one-line addition here, and having to write it down
# is the moment to check the column really is an integer.
INTEGER_ID_SEGMENTS = {"id"}


def _id_shaped(expr: str) -> bool:
    if not _DOTTED_PATH.match(expr):
        return False
    return expr.rsplit(".", 1)[-1] in INTEGER_ID_SEGMENTS


def _blank_comments(src: str) -> str:
    """Drop Jinja comments, keeping line numbers (they render nothing)."""
    return _JINJA_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), src)


def handler_interpolations(src: str) -> list[tuple[int, str, str]]:
    """(line, whole attribute, expression) for every {{ }} inside an on*."""
    src = _blank_comments(src)
    found = []
    for attr in _ON_ATTR.finditer(src):
        line = src[: attr.start()].count("\n") + 1
        for raw in _INTERPOLATION.findall(attr.group(1)):
            found.append((line, attr.group(0), raw.strip()))
    return found


def offending_interpolations(src: str) -> list[tuple[int, str, str]]:
    return [
        hit for hit in handler_interpolations(src)
        if not _id_shaped(hit[2]) and hit[2] not in ON_HANDLER_ALLOWLIST
    ]


def test_no_free_text_is_interpolated_into_an_inline_handler():
    """The sweep. Read the block above before adding to the allowlist -- and
    do NOT rewrite a handler just to make this green without saying so: a
    handler that trips this is either a real finding or an allowlist entry
    with a reason next to it."""
    offenders = [
        f"{path.name}:{line}  {attr[:100]}  ->  {{{{ {expr} }}}}"
        for path in sorted(TEMPLATES.glob("*.html"))
        for line, attr, expr in offending_interpolations(
            path.read_text(encoding="utf-8")
        )
    ]
    assert not offenders, (
        "free text interpolated into an inline on* handler (invariant 7): the "
        "browser HTML-decodes the attribute before parsing it as JS, so Jinja's "
        "escaping does not protect it. Put the value in a data- attribute and "
        "read it via dataset. If it genuinely cannot contain a quote or a paren: "
        "an integer column goes in INTEGER_ID_SEGMENTS, anything else in "
        "ON_HANDLER_ALLOWLIST, both with a comment saying why.\n  "
        + "\n  ".join(offenders)
    )


SCANNER_SAMPLE = (
    '<form action="/tags/{{ t.id }}/edit">\n'
    '  <button onclick="closeDialog(\'{{ t.id }}\')">x</button>\n'
    '  <button onclick="alert(\'{{ t.name }}\')">bad</button>\n'
    '  <button onclick="pick(\'{{ t.name | e }}\')">also bad</button>\n'
    '  <input oninput="filterChips(this, \'#picker-{{ kindname }}\')">\n'
    "  {# <button onclick=\"alert('{{ t.name }}')\">commented</button> #}\n"
    '  <form data-confirm="{{ _(\'Delete this?\') }}">go</form>\n'
)


def test_the_handler_sweep_sees_handlers_and_tells_names_from_ids():
    """The sweep above passes on an EMPTY scan, so a matcher that silently
    stopped matching would look exactly like a clean repo. This pins the
    scanner itself against a sample carrying one of each case.

    BOTH assertions are exact equality against a NON-EMPTY literal, never a
    subset or an `in` check: a scanner that found nothing at all must fail
    here, and a subset assertion would pass on the empty list.

    Mutations this catches: dropping `\b` from _ON_ATTR (`data-cONfirm=` on
    the last line starts matching, and the sweep begins reporting the very
    idiom invariant 7 prescribes); scanning `action=` as a handler; loosening
    _DOTTED_PATH so a filtered expression passes on its last word (`t.name | e`
    is still free text); letting a bare `.name` through; and stripping Jinja
    comments in a way that shifts line numbers.
    """
    seen = handler_interpolations(SCANNER_SAMPLE)
    # The form action and the data-confirm are NOT handlers; the commented-out
    # handler renders nothing.
    assert [expr for _l, _a, expr in seen] == [
        "t.id", "t.name", "t.name | e", "kindname",
    ]
    # ...and the allowlisted one still reports as line 5, so the comment above
    # it was blanked rather than deleted.
    assert seen[-1][0] == 5
    assert [(line, expr) for line, _a, expr in offending_interpolations(SCANNER_SAMPLE)] == [
        (3, "t.name"), (4, "t.name | e"),
    ]


def test_a_string_event_id_is_refused_by_the_id_rule():
    """`Concert.event_id` is editor-typed text, not an integer, so the suffix
    reading of "id-shaped" was wrong about the one counterexample already in
    the schema. It is refused by NAME now, not by luck.

    Mutation this catches -- and the only one that matters here: restoring
    `last == "id" or last.endswith("_id")` in _id_shaped. The sweep itself
    cannot catch that, because no template puts an event_id in a handler
    today; without this test the narrowing is unpinned and reverts silently.
    """
    handler = '<button onclick="go(\'{{ concert.event_id }}\')">x</button>'
    assert [expr for _l, _a, expr in offending_interpolations(handler)] == [
        "concert.event_id"
    ], "a STRING event_id must not pass as id-shaped"
    # The narrowing is a real narrowing, not a blanket ban: the segment the
    # app actually uses still passes, so the sweep stays green on tags.html.
    assert not offending_interpolations(
        '<button onclick="go({{ t.id }})">x</button>'
    )
