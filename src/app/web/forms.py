"""HTTP-boundary form parsing shared across web routes.

Deliberately tiny and dependency-light: routes/tags.py and routes/imports.py
both need form_url, and importing it from routes/concerts.py dragged a whole
feature-route module (and a tags -> concerts edge) in for six lines.
"""

from fastapi import HTTPException

from app.domain.urls import UnsafeUrlError, clean_url


def form_url(value: str | None) -> str | None:
    """domain.urls.clean_url at the HTTP boundary -- the one place a bad
    scheme becomes a 422. Every editor-facing URL field must go through it;
    an editor who pastes a junk URL is told so rather than having it
    silently dropped."""
    try:
        return clean_url(value)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
