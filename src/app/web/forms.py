"""HTTP-boundary wrappers around domain validators.

domain/ may not import fastapi, so the translation from a domain exception
to a status code lives here -- the same shape resolve_tags uses for a bad
tag id. Its own module rather than routes/concerts.py because routes/tags.py
and routes/imports.py need it too, and neither should have to import a
~920-line feature-route module for a six-line helper.
"""

from fastapi import HTTPException

from app.domain.translations import missing_variants
from app.domain.urls import UnsafeURLError, clean_url


def form_url(raw: str | None) -> str | None:
    """clean_url, with a rejection surfaced as 422 instead of a 500."""
    try:
        return clean_url(raw)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Language names are deliberately NOT _()-wrapped: this project never
# translates them (see the UI conventions in CLAUDE.md) -- someone reading an
# error about a missing 中文 value has to recognise the language before they
# can read anything else.
_SLOT_LABEL = {"ja": "日本語", "en": "English", "zh": "中文"}


def require_variants(
    field_label: str, base: str, en: str, zh: str, *, mandatory: bool = False
) -> None:
    """422 unless a field is filled in all three languages or none.

    missing_variants (domain/) is the rule; this is only its HTTP boundary,
    the same shape form_url gives clean_url above.

    The detail names the field AND the missing languages, because by the time
    this fires there is no other error UI to lean on. On a normal submit the
    browser-side check (web/templates/_variant_guard.html) has already caught
    the gap, painted an inline message beside the field and not submitted at
    all; reaching here means JS is off, or the request never came from the
    form -- and then this string is the entire error UX. 422 rather than 409:
    a gap is a structural violation, not a uniqueness conflict.
    """
    gaps = missing_variants(base, en, zh, mandatory=mandatory)
    if not gaps:
        return
    raise HTTPException(
        status_code=422,
        detail=f"{field_label} needs {', '.join(_SLOT_LABEL[g] for g in gaps)}",
    )
