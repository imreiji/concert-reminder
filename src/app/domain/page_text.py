"""HTML to the ONE text an AI completion pass reads.

Pure, like every other module in `domain/`: a string in, a string out, no
httpx call of its own -- the same split `ingest.py` and `eventernote.py` make
against their fetchers.

WHY ONE FUNCTION. The completion pass asks a model for rounds and then checks
that the quote it gave for each timestamp actually occurs on the page. That
check is only worth anything if the text the model read and the text the
verifier searches are byte-identical, so both go through here. Two
normalizations -- one for the prompt, one for the check -- would let a quote
be "not found" because of a whitespace rule the model never saw, or, worse,
let an invented quote match text the model was never given.

The whitespace rule is deliberately aggressive: every run of whitespace,
INCLUDING U+3000 (the ideographic space, which this app's Japanese pages are
full of and which `str.split()` already treats as whitespace), collapses to one
ASCII space. Models reproduce spacing loosely when they quote, so an exact
substring test against loosely-spaced source text fails constantly; collapsing
both sides is what makes the test about the WORDS rather than the layout.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

# How much of a page reaches the model. Extracted text is far denser than the
# HTML it came from -- 60k characters of Japanese is an enormous page -- and the
# same cap covers a pasted page, so the fetched and pasted paths cannot behave
# differently.
PAGE_TEXT_CAP = 60_000

_WHITESPACE = re.compile(r"\s+")


def collapse(text: str) -> str:
    """Every run of whitespace to a single space, ends trimmed."""
    return _WHITESPACE.sub(" ", text).strip()


def html_to_text(html: str) -> str:
    """Readable text from a fetched page: no script, no style, blocks separated.

    The separator is not cosmetic. `get_text()` with no separator fuses
    adjacent cells -- `<td>23:59</td><td>受付終了</td>` becomes `23:59受付終了`
    -- and a model quoting either half would then be quoting something that,
    as a string, is not on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_page_text(soup.get_text(separator=" "))


def normalize_page_text(text: str) -> str:
    """Collapse, then cap. The entry point for text that is already text
    (a pasted page), and the tail of `html_to_text`."""
    # A cut can land right after a space collapse() already reduced to one --
    # truncation is the only thing that can put whitespace back at the end,
    # so strip it again or this stops being idempotent at the cap boundary.
    return collapse(text)[:PAGE_TEXT_CAP].rstrip()
