"""Offset paging for the agent API.

Its own module beside `web/forms.py`, which is the same kind of thing: a small
HTTP-boundary helper several routers import.

THE RULE THAT MAKES THIS CORRECT LIVES IN THE QUERIES, NOT HERE. Offset paging
over a non-unique sort key is broken even when nothing is being inserted --
SQLite may order ties differently between the two queries, so a row repeats on
page 2 while another vanishes. Every paged query must sort on a TOTALLY ORDERED
key, with a unique column (normally `id`) as the final tiebreaker. This module
cannot enforce that; `tests/test_api_reads.py` asserts it per endpoint.

HOW it asserts it depends on where the sort happens, and the obvious test is
the wrong one for a Python-side sort. Against a SQL `ORDER BY ... LIMIT ...
OFFSET`, checking that the union of the pages equals the whole set is exactly
right. Against rows sorted in Python it proves nothing: `list.sort` is stable
and deterministic, so slices of one list are disjoint whatever the key is.
There the assertion that bites is on the ORDER
(`test_the_list_sort_is_totally_ordered`), seeded so the query's own row order
disagrees with the tiebreaker. Pick the one that matches the endpoint.
"""

from dataclasses import dataclass

from fastapi import HTTPException

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


async def page_params(limit: int = DEFAULT_LIMIT, offset: int = 0) -> PageParams:
    """422 rather than a silent clamp.

    Clamping is the friendlier-looking choice and the wrong one: an agent that
    asked for 5000, received 500 and was told nothing would conclude it had
    read the whole set and stop paging.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(
            status_code=422, detail=f"limit must be between 1 and {MAX_LIMIT}"
        )
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be 0 or greater")
    return PageParams(limit=limit, offset=offset)


def page_envelope(items: list, total: int, params: PageParams) -> dict:
    """`total` is the count BEFORE limit/offset, which is what lets a caller
    know when to stop instead of paging until it gets a short page."""
    return {
        "items": items,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }
