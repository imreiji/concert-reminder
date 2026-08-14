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
conversion so this side of the diff has SOMETHING to compare against
`HeldRound.opens_at_utc`. A value that is absent, blank or does not parse in
that exact shape is treated as "no open time known" rather than raised --
the same warns-and-skips habit every parser in this package follows, and
the only sane response to text a model wrote freehand.

THE TWO SIDES ARE NOT COMPARABLE ON PRECISION WITHOUT HELP. Parsing
"%Y-%m-%d %H:%M" always yields ":00" seconds/microseconds, but
`HeldRound.opens_at_utc` comes off a bare `Round.opens_at_utc` column with no
precision constraint -- `domain/yaml_import.py`'s `_dt` accepts a YAML
timestamp WITH seconds verbatim, so a hand- or AI-authored draft can seed a
live round with one. `dedupe_key` truncates both sides to minute precision
for exactly this reason; read its docstring before touching either side of
this comparison.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.round_evidence import ProposedRound
from app.domain.timezones import jst_to_utc

# The first two of `TIMESTAMP_FIELDS` (round_evidence.py): the round's OWN
# opening and closing times, not the concert's. Public because the POLL
# (`app/round_poll.py`) stores both on a `RoundProposal` and must read them
# through `proposed_stamp_utc` below rather than parsing that text a second
# time -- see its docstring.
OPENS_AT_FIELD = "apply_opens_jst"
CLOSES_AT_FIELD = "apply_closes_jst"
# The other two of `TIMESTAMP_FIELDS`, added 2026-08-14. The prompt has asked
# for all four since it was written and `verify_rounds` grounds all four
# against the page, but the poll stored only the first two -- so a results
# announcement and a payment deadline, two of the three anchors this app exists
# to remind people about, were parsed, checked and then thrown away. Read
# through `proposed_stamp_utc` like the other two; a second parse here would be
# free to disagree with the one the dedupe key is built from.
RESULTS_AT_FIELD = "results_jst"
PAYMENT_AT_FIELD = "payment_deadline_jst"
# NOT a timestamp: a list of the leg LABELS the model named, verbatim. It is a
# field name rather than a literal at the poll's call site for the same reason
# as the four above -- the prompt, `verify_rounds` and the poll must all spell
# it the same way, and none of them raises when they don't.
APPLIES_TO_FIELD = "applies_to"
_STAMP_TEXT_FORMAT = "%Y-%m-%d %H:%M"


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

    Truncated to MINUTE precision on whatever it is given. The two sides of
    the diff are not guaranteed to agree below that: `_proposed_opens_at_utc`
    always yields ":00" seconds/microseconds because it parses
    "%Y-%m-%d %H:%M" text, but `HeldRound.opens_at_utc` comes off a bare
    `Round.opens_at_utc` column with no such constraint --
    `domain/yaml_import.py`'s `_dt` accepts a YAML timestamp WITH seconds
    verbatim, so a hand- or AI-authored draft can put one on a live round.
    Without this truncation the same real-world round produces two different
    keys depending on which side of the diff it's read from, and is
    re-proposed every poll, forever -- the exact failure this key exists to
    prevent. The truncation lives HERE, not at call sites, so `dedupe_key` is
    safe to call directly and the fix cannot be forgotten at a second site.
    """
    if opens_at_utc is not None:
        opens_at_utc = opens_at_utc.replace(second=0, microsecond=0)
    stamp = opens_at_utc.isoformat() if opens_at_utc is not None else ""
    return f"{_normalize_label(label)}|{stamp}"


def proposed_stamp_utc(proposed: ProposedRound, field: str) -> datetime | None:
    """One of a proposed round's JST-text timestamps as aware UTC, or None.

    See the module docstring for why this exists: `ProposedRound` has no
    datetime attributes at all, only the draft vocabulary's
    `"%Y-%m-%d %H:%M"` JST TEXT buried in `data`.

    ONE conversion, two readers, and that is the point. `new_proposals` below
    reads `OPENS_AT_FIELD` because the dedupe key is built from it; the poll
    (`app/round_poll.py`) reads BOTH fields, because a `RoundProposal` row
    stores the closing time as well as the opening one. A second parse written
    at that call site would be free to disagree with this one about what counts
    as a readable stamp -- and the half that fed `dedupe_key` would be the half
    nobody noticed had drifted, because its symptom is a proposal quietly
    re-proposed forever rather than an error.

    Absent, blank or not in that exact shape is "no time known" rather than a
    raise: the same warns-and-skips habit every parser in this package follows,
    and the only sane response to text a model wrote freehand.
    """
    text = str(proposed.data.get(field) or "").strip()
    if not text:
        return None
    try:
        naive_jst = datetime.strptime(text, _STAMP_TEXT_FORMAT)
    except ValueError:
        return None
    return jst_to_utc(naive_jst)


def _proposed_opens_at_utc(proposed: ProposedRound) -> datetime | None:
    """The round's opening time -- the half of `proposed_stamp_utc` the dedupe
    key is built from."""
    return proposed_stamp_utc(proposed, OPENS_AT_FIELD)


def new_proposals(
    existing: Sequence[HeldRound], proposed: Sequence[ProposedRound]
) -> list[ProposedRound]:
    """`proposed` minus anything the concert already holds, order preserved."""
    held = {dedupe_key(r.label, r.opens_at_utc) for r in existing}
    return [
        p for p in proposed if dedupe_key(p.label, _proposed_opens_at_utc(p)) not in held
    ]
