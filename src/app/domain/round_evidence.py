"""Is this proposed round grounded in the page it claims to come from?

THIS IS PHASE 2's `strip_rounds`. Phase 1 could promise honesty cheaply: it
emitted no rounds at all, and stripped any the model invented anyway. Phase 2
emits rounds, so the promise has to be earned per round, in code, on the same
principle -- the prompt asks, the code decides.

A round survives only if the model showed WHERE it read each timestamp and this
module can find that text on the page. Five ways to fail, each of them a way a
fabricated deadline could otherwise reach a real user as a real reminder:

  1. a timestamp with no quote at all;
  2. a quote that is not on the page (the plain hallucination);
  3. a quote that IS on the page but does not carry this timestamp -- the
     nastiest case, because the naive "did the quote match?" check passes;
  4. anchors out of order (results before the deadline they announce);
  5. an implausible date, or an `applies_to` naming a leg the draft lacks.

NOTHING IS DROPPED SILENTLY. Every rejection carries a human-readable reason
that reaches the preview, because a real deadline quietly discarded is exactly
as harmful as a fake one quietly kept -- the operator has no way to know to look
in either case.

The comparison is deliberately about WORDS, not layout: both sides go through
`page_text.collapse`, and digits are compared as NUMBERS after normalizing
full-width forms and 年月日時分. A model reproducing 2026年1月10日 as 2026年1月10日
with a different space, or a page writing 23:59 as ２３：５９, must not be a
rejection -- those are formatting, and rejecting on formatting would train the
operator to ignore rejections.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.domain.page_text import collapse

# The four anchors a round can carry, in the order they must occur in time.
# Same order as the ladder itself; `_check_order` reads it as an ordering.
TIMESTAMP_FIELDS: tuple[str, ...] = (
    "apply_opens_jst",
    "apply_closes_jst",
    "results_jst",
    "payment_deadline_jst",
)

# How far from today a proposed date may sit. A ticket page can legitimately
# carry a deadline that has already passed (an old lead being drafted late), so
# the past window is generous; the future window is what catches a fat-fingered
# or hallucinated century.
_PAST_YEARS = 2
_FUTURE_YEARS = 3

_FULLWIDTH = str.maketrans("０１２３４５６７８９：", "0123456789:")
_NUMBER = re.compile(r"\d+")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})")


@dataclass(frozen=True)
class ProposedRound:
    """One round as the model proposed it, with its evidence held apart.

    `data` is the round mapping destined for the draft's YAML and NEVER
    contains `evidence` -- that is proofreading scaffolding and must not ride
    into a document that gets committed into `concerts`.
    """

    data: dict
    evidence: dict[str, str]
    label: str


@dataclass(frozen=True)
class Verdict:
    accepted: tuple[ProposedRound, ...] = ()
    rejected: tuple[str, ...] = ()


def normalize_numbers(text: str) -> list[int]:
    """Every number in `text`, after folding the Japanese ways of writing one.

    ２０２６年１月１０日(土)２３：５９ and 2026-01-10 23:59 must yield the same
    list, or the check would reject a page for its typography.
    """
    folded = text.translate(_FULLWIDTH)
    folded = re.sub(r"[年月時]", " ", folded)
    folded = re.sub(r"[日分秒]", " ", folded)
    return [int(n) for n in _NUMBER.findall(folded)]


def _stamp_parts(stamp: str) -> tuple[int, int, int, int, int] | None:
    """(year, month, day, hour, minute) from a 'YYYY-MM-DD HH:MM' string."""
    match = _STAMP.search(stamp.strip())
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    return (year, month, day, hour, minute)


def _quote_carries_stamp(quote: str, page_numbers: set[int], parts) -> bool:
    """Does this quote actually say this timestamp?

    Month, day and hour must be IN THE QUOTE. The YEAR may instead come from
    anywhere on the page: Japanese ticket pages routinely put it in a heading
    and omit it from the deadline line itself, and demanding it in the quote
    would reject the common case. The MINUTE is waived when it is 0, because
    '20時' is how a page writes 20:00 and carries no zero to find.
    """
    year, month, day, hour, minute = parts
    numbers = set(normalize_numbers(quote))
    if not {month, day, hour} <= numbers:
        return False
    if minute and minute not in numbers:
        return False
    return year in numbers or year in page_numbers


def _check_order(data: dict) -> str | None:
    """The anchors present must not go backwards in time."""
    seen: list[tuple[str, str]] = []
    for field_name in TIMESTAMP_FIELDS:
        value = str(data.get(field_name) or "").strip()
        if value:
            seen.append((field_name, value))
    for (a_name, a), (b_name, b) in zip(seen, seen[1:], strict=False):
        # ISO-ish strings sort chronologically as text, which is the whole
        # reason this app writes them this way -- no parsing needed here.
        if b < a:
            return f"{a_name} ({a}) and {b_name} ({b}) are out of order"
    return None


def verify_rounds(
    rounds: Sequence[ProposedRound],
    page_text: str,
    leg_labels: Sequence[str],
    today: date,
) -> Verdict:
    """Split proposed rounds into the grounded and the rejected-with-a-reason.

    One bad round never costs a good one -- the same skip-and-count philosophy
    every parser in this package follows -- because the alternative is a page
    with one sloppy line handing back nothing at all.
    """
    page = collapse(page_text)
    page_numbers = set(normalize_numbers(page))
    known_legs = {label.strip() for label in leg_labels}
    accepted: list[ProposedRound] = []
    rejected: list[str] = []

    for proposed in rounds:
        label = proposed.label or "(unlabelled round)"
        reason = _reject_reason(proposed, page, page_numbers, known_legs, today)
        if reason is None:
            accepted.append(proposed)
        else:
            rejected.append(f"round {label!r}: {reason}")

    return Verdict(accepted=tuple(accepted), rejected=tuple(rejected))


def _reject_reason(
    proposed: ProposedRound,
    page: str,
    page_numbers: set[int],
    known_legs: set[str],
    today: date,
) -> str | None:
    """The first reason this round cannot be trusted, or None."""
    stamps = {
        name: str(proposed.data.get(name) or "").strip()
        for name in TIMESTAMP_FIELDS
        if str(proposed.data.get(name) or "").strip()
    }
    if not stamps:
        return "no timestamps at all -- a round with no deadline is a label, not a rung"

    for name, stamp in stamps.items():
        quote = collapse(str(proposed.evidence.get(name) or ""))
        if not quote:
            return f"no evidence for {name} ({stamp})"
        if quote not in page:
            return f"the quote for {name} is not on the page: {quote!r}"
        parts = _stamp_parts(stamp)
        if parts is None:
            return f"{name} ({stamp}) is not a 'YYYY-MM-DD HH:MM' timestamp"
        year = parts[0]
        if not (today.year - _PAST_YEARS <= year <= today.year + _FUTURE_YEARS):
            return f"{name} ({stamp}) has an implausible year"
        if not _quote_carries_stamp(quote, page_numbers, parts):
            return f"the quote for {name} does not carry {stamp}: {quote!r}"

    order_problem = _check_order(proposed.data)
    if order_problem is not None:
        return order_problem

    applies_to = proposed.data.get("applies_to") or []
    if isinstance(applies_to, list):
        for leg in applies_to:
            if str(leg).strip() not in known_legs:
                return f"applies_to names {str(leg).strip()!r}, which is not a leg of this draft"

    return None
