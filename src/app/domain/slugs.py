"""URL/handle slug primitives. Pure: no I/O, no ORM.

Its own module because two unrelated consumers need it -- concert `event_id`
generation (via `yaml_export.slugify`) and tag handles -- and neither is a
natural home for the other's fallback rule.
"""

import re


def slug_core(text: str) -> str:
    """'Hasunosora 5th Live!' -> 'hasunosora-5th-live'. NO fallback.

    Returns "" when nothing survives, which is the whole point: everything
    outside [a-z0-9] is stripped, so a Japanese-only string empties out and the
    CALLER decides what that means. `slugify` answers "concert"; a tag answers
    "{kind}-{id}". A shared fallback could not serve both, and worse, "concert"
    is indistinguishable from a real title of that name.
    """
    lowered = text.strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def tag_slug_base(name: str, name_en: str | None) -> str:
    """A tag handle's base, before de-duplication: the English name if it yields
    anything, else the canonical name, else "".

    `name_en` first because the trilingual rule makes it mandatory at every tag
    create boundary, so it is reliably present on new rows; `name` covers the
    older rows that predate that rule. "" means the caller must fall back to
    {kind}-{id}, which needs a flushed row and so cannot happen here.
    """
    return slug_core(name_en or "") or slug_core(name)
