"""PURE. Which proposed rounds are NEW, and the key that makes a dismissal stick.

No session, no network, no key -- so the rule that decides whether the owner
is shown a proposal at all is testable without any of them, the same
separation `round_evidence.py` already makes for whether a round may exist.

DISCREPANCY WITH THE PLAN THAT WROTE THIS FILE'S FIRST DRAFT: the plan assumed
`round_evidence.ProposedRound` carried a `label` AND an `opens_at_utc` field
directly. The real dataclass (`round_evidence.py:352`) is
`ProposedRound(data: dict, evidence: dict[str, str], label: str)` -- there is
no `opens_at_utc` attribute. A round's opening time, when the model proposed
one, lives as JST TEXT under `data["apply_opens_jst"]` (one of
`TIMESTAMP_FIELDS`), written by `round_completion._as_stamp_text` in the
draft vocabulary's `"%Y-%m-%d %H:%M"` shape -- never a `datetime`, so PyYAML
resolving a mapping value and the draft parser reading it back stay in
agreement. `_proposed_opens_at_utc` below does the JST-text -> aware-UTC
conversion `HeldRound.opens_at_utc` already carries (it comes straight off a
`ConcertDay`/`RoundRule` column, via `db.quiet_ladders.QuietRound`), so the two
sides of the diff are comparable at all. A value that is absent, blank or
does not parse in that exact shape is treated as "no open time known" rather
than raised -- the same warns-and-skips habit every parser in this package
follows, and the only sane response to text a model wrote freehand.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.round_evidence import ProposedRound
from app.domain.timezones import jst_to_utc

# The field `_proposed_opens_at_utc` reads: the first of `TIMESTAMP_FIELDS`
# (round_evidence.py), i.e. the round's OWN opening time, not the concert's.
_OPENS_AT_FIELD = "apply_opens_jst"
_OPENS_AT_TEXT_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class HeldRound:
    """A round the concert ALREADY carries. Not `db.quiet_ladders.QuietRound`:
    `domain/` may not import `db/`, so the caller adapts."""

    label: str
    opens_at_utc: datetime | None


def _normalize_label(label: str) -> str:
    # NFKC folds full-width digits and letters onto their ASCII forms, so
    # '１次先行' and '1次先行' are one round rather than two rows a day.
    return unicodedata.normalize("NFKC", label).strip().casefold()


def dedupe_key(label: str, opens_at_utc: datetime | None) -> str:
    """Stable identity for one proposed round.

    Derived and readable rather than an opaque hash: a key you can read in the
    table is a key you can debug.
    """
    stamp = opens_at_utc.isoformat() if opens_at_utc is not None else ""
    return f"{_normalize_label(label)}|{stamp}"


def _proposed_opens_at_utc(proposed: ProposedRound) -> datetime | None:
    """The round's opening time as aware UTC, or None if absent/unparseable.

    See the module docstring for why this exists: `ProposedRound` has no
    `opens_at_utc` attribute, only JST text buried in `data`.
    """
    text = str(proposed.data.get(_OPENS_AT_FIELD) or "").strip()
    if not text:
        return None
    try:
        naive_jst = datetime.strptime(text, _OPENS_AT_TEXT_FORMAT)
    except ValueError:
        return None
    return jst_to_utc(naive_jst)


def new_proposals(
    existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]
) -> list[ProposedRound]:
    """`proposed` minus anything the concert already holds, order preserved."""
    held = {dedupe_key(r.label, r.opens_at_utc) for r in existing}
    return [
        p for p in proposed if dedupe_key(p.label, _proposed_opens_at_utc(p)) not in held
    ]
