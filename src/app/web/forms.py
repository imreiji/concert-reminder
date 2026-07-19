"""HTTP-boundary wrappers around domain validators.

domain/ may not import fastapi, so the translation from a domain exception
to a status code lives here -- the same shape resolve_tags uses for a bad
tag id. Its own module rather than routes/concerts.py because routes/tags.py
and routes/imports.py need it too, and neither should have to import a
~920-line feature-route module for a six-line helper.
"""

from fastapi import HTTPException

from app.domain.urls import UnsafeURLError, clean_url


def form_url(raw: str | None) -> str | None:
    """clean_url, with a rejection surfaced as 422 instead of a 500."""
    try:
        return clean_url(raw)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
