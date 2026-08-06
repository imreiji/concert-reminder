"""Is this proposed round grounded in the page it claims to come from?

THIS IS PHASE 2's `strip_rounds`. Phase 1 could promise honesty cheaply: it
emitted no rounds at all, and stripped any the model invented anyway. Phase 2
emits rounds, so the promise has to be earned per round, in code, on the same
principle -- the prompt asks, the code decides.

A round survives only if the model showed WHERE it read each timestamp and this
module can find that text on the page, IN ONE PLACE. A false ACCEPT is the
failure that matters here -- a fabricated deadline reaching a real user as a
real reminder -- and a false REJECT costs a human one round typed in by hand,
so every rule below is deliberately the stricter reading. Ways to fail, each
of them a way a fabricated deadline could otherwise reach a real user:

  1. a timestamp with no quote at all;
  2. a quote longer than 200 collapsed characters -- quoting half the page is
     not evidence, whatever it contains;
  3. a quote that is not on the page (the plain hallucination);
  4. a quote that IS on the page but does not carry this timestamp IN ONE
     PLACE -- the nastiest case, and the one a flat "are these digits present
     somewhere" test cannot catch: a quote can carry a real date and someone
     else's real time, or splice two real lines into a stamp neither of them
     states, and every one of its digits will still be "in there somewhere".
     The fix is locality, not presence: month must be immediately followed by
     day as the next number token, and hour must be the VERY NEXT number
     token after that date (immediately followed by minute, unless waived) --
     not merely present later in the quote. The date-to-time span is
     additionally capped at 60 characters, so a real deadline line still
     passes while a quote padding a lot of irrelevant text between the two
     does not;
  5. an implausible year, or a month/day that isn't a real calendar date;
  6. anchors out of order (results before the deadline they announce),
     compared as PARSED (year, month, day, hour, minute) tuples -- never as
     raw text, since this app's own two accepted separators (' ' and 'T')
     don't sort against each other the way their times do, and a stray prefix
     on an otherwise-fine stamp outranks every digit in a lexicographic
     compare;
  7. an `applies_to` naming a leg the draft lacks, OR not shaped like a list
     of leg names at all -- an unrecognized shape gets no free pass, it gets
     a reason.

NOTHING IS DROPPED SILENTLY. Every rejection carries a human-readable reason
that reaches the preview, because a real deadline quietly discarded is exactly
as harmful as a fake one quietly kept -- the operator has no way to know to look
in either case.

The comparison is deliberately about WORDS, not layout: the page text is run
through `page_text.normalize_page_text` (collapse, then the same 60k cap the
model's own prompt is built under -- so a quote can never verify against text
the model was never shown), and every quote through `page_text.collapse`.
Digits are compared as NUMBERS after normalizing full-width forms and
年月日時分秒, and that same folding is applied to BOTH sides of the "is this
quote on the page at all" substring test -- a page (or a model's
transcription of it) writing 23:59 as ２３：５９ must not read as absent
evidence, or the very tolerance this module claims to have would be false in
one direction.

An accepted round's `data` never carries an `evidence` key -- that field is
proofreading scaffolding, stripped here as one of two layers (the draft
parser downstream strips it again) because a key that must never reach a
document committed into `concerts` should not depend on exactly one layer
remembering to remove it.

AN ACCEPTED ROUND'S `evidence` IS TRIMMED TO WHAT WAS ACTUALLY VERIFIED.
Everything above checks the keys in TIMESTAMP_FIELDS a round itself carries --
nothing else. A model's evidence mapping can carry MORE keys than that (a
field the round doesn't have, or one it invented outright), and those extra
entries are never compared against the page at all. The preview renders
`evidence` under a heading that says "Read from the ticket page:", so an
unverified key surviving there would be exactly the fabricated-but-trusted
quote this whole module exists to catch -- `verify_rounds` drops every
evidence key outside the verified set before an accepted round's `evidence`
ever leaves this module.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from app.domain.page_text import collapse, normalize_page_text

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

# A real deadline line is short. A quote past this is grounds for rejection on
# its own -- see module docstring point 2.
_MAX_QUOTE_CHARS = 200

# The character span from the start of a matched date to the end of its
# matched time, in the (folded, position-preserving) quote text -- see
# module docstring point 4.
_MAX_STAMP_SPAN_CHARS = 60

# Any of these anywhere in a quote means its time is written explicitly
# enough that a zero minute is a real zero, not an omission -- so the
# zero-minute waiver in `_quote_carries_stamp` does not apply.
_TIME_SEPARATORS = (":", "：", "分")

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


def _fold_digits(text: str) -> str:
    """Full-width digits and colon to half-width, and nothing else.

    Applied to BOTH sides of the on-page substring test, so a page (or a
    model's transcription of it) writing its digits zenkaku is not read as
    absent evidence -- see module docstring.
    """
    return text.translate(_FULLWIDTH)


def _blank_era_words(text: str) -> str:
    """Blank out 年月日時分秒 so only the surrounding numbers remain.

    Each character is replaced ONE FOR ONE with a single space, so this never
    shifts any later character's position -- `_number_tokens` relies on that
    to report offsets that still index into the caller's original string.
    """
    text = re.sub(r"[年月時]", " ", text)
    return re.sub(r"[日分秒]", " ", text)


def _number_tokens(text: str) -> list[tuple[int, int, int]]:
    """(value, start, end) for every number in `text`, position-aligned to
    `text` itself. Folding is 1:1 in length (`_fold_digits` is a character
    translation, `_blank_era_words` replaces one character with one), so a
    token's offsets never shift relative to the caller's input.
    """
    folded = _blank_era_words(_fold_digits(text))
    return [(int(m.group()), m.start(), m.end()) for m in _NUMBER.finditer(folded)]


def normalize_numbers(text: str) -> list[int]:
    """Every number in `text`, after folding the Japanese ways of writing one.

    ２０２６年１月１０日(土)２３：５９ and 2026-01-10 23:59 must yield the same
    list, or the check would reject a page for its typography.
    """
    return [value for value, _, _ in _number_tokens(text)]


def _stamp_parts(stamp: str) -> tuple[int, int, int, int, int] | None:
    """(year, month, day, hour, minute) from a 'YYYY-MM-DD HH:MM' string."""
    match = _STAMP.search(stamp.strip())
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    return (year, month, day, hour, minute)


def _quote_carries_stamp(
    quote: str, page_numbers: set[int], parts: tuple[int, int, int, int, int]
) -> bool:
    """Does this quote actually say this timestamp, IN ONE PLACE?

    Locality, not just presence: month must be immediately followed by day as
    the next number token, and hour must be the VERY NEXT number token after
    that date (immediately followed by minute, unless waived) -- not merely
    present somewhere later in the quote. A flat "are these digits all in
    here somewhere" test accepts a quote naming one date and someone else's
    time, or a quote spanning two lines whose digits happen to recombine into
    the claimed stamp; requiring the time to be the date's own immediate
    successor closes both. The span from the date's first token to the
    time's last is additionally capped at `_MAX_STAMP_SPAN_CHARS`, so a real
    deadline line still passes while a quote padding a lot of irrelevant text
    between the two does not.

    The YEAR is checked once, broadly -- anywhere in this quote's own numbers
    or anywhere on the page -- not required adjacent to the date: Japanese
    ticket pages routinely put it in a heading and omit it from the deadline
    line, and this half of the old, unlocalized rule stays unchanged.

    The MINUTE is waived when it is 0 AND the quote carries no time separator
    at all (no ':', '：', '分') -- '20時' is how a page writes 20:00 and has
    no zero to find. '12:00' gets no such waiver and needs none:
    `normalize_numbers("12:00")` already yields `[12, 0]`, so a quote whose
    real minute is not 0 (e.g. "12:30") is compared against 0 for real, and
    correctly fails to match rather than being waved through.
    """
    year, month, day, hour, minute = parts
    tokens = _number_tokens(quote)
    quote_numbers = {value for value, _, _ in tokens}
    if year not in quote_numbers and year not in page_numbers:
        return False

    minute_waived = minute == 0 and not any(sep in quote for sep in _TIME_SEPARATORS)

    for i in range(len(tokens) - 1):
        month_value, month_start, _ = tokens[i]
        day_value, _, _ = tokens[i + 1]
        if month_value != month or day_value != day:
            continue

        time_index = i + 2
        if time_index >= len(tokens):
            continue
        hour_value, _, hour_end = tokens[time_index]
        if hour_value != hour:
            continue

        if minute_waived:
            time_end = hour_end
        else:
            minute_index = time_index + 1
            if minute_index >= len(tokens):
                continue
            minute_value, _, minute_end = tokens[minute_index]
            if minute_value != minute:
                continue
            time_end = minute_end

        start = month_start
        if i > 0 and tokens[i - 1][0] == year:
            start = tokens[i - 1][1]
        if time_end - start <= _MAX_STAMP_SPAN_CHARS:
            return True

    return False


def _present_timestamp_fields(data: dict) -> set[str]:
    """Which of TIMESTAMP_FIELDS this round's `data` actually carries
    (non-blank) -- the same membership test `_reject_reason` uses to build its
    own `stamps` dict, pulled out so `verify_rounds` can reuse it to trim an
    accepted round's `evidence` down to exactly the keys that were checked.

    This is what stands between "the model quoted a real page line for
    apply_closes_jst" and "the model's ENTIRE evidence mapping, unchecked,
    rides onto the preview under a heading that says it came from the page" --
    `_reject_reason` only ever inspects the keys in TIMESTAMP_FIELDS the round
    itself carries, so any other key in `evidence` (a field the round doesn't
    have, or one the model invented) has never been compared against the page
    at all.
    """
    return {name for name in TIMESTAMP_FIELDS if str(data.get(name) or "").strip()}


def _check_order(stamps: dict[str, str]) -> str | None:
    """The anchors present must not go backwards in time -- compared as
    PARSED (year, month, day, hour, minute) tuples, never as raw text.

    `_STAMP` accepts both a ' ' and a 'T' separator, and `.search()` tolerates
    a leading prefix on the stamp string -- so a raw string compare is not
    actually a time compare: ' ' sorts below 'T' regardless of the times
    involved (a stamp using 'T' can outrank a genuinely later one using ' '),
    and a stray prefix like "受付 " outranks every digit that follows it (a
    stamp that is actually earlier can still sort as "greater"). Parsing
    removes the ambiguity outright. Only the anchors actually present are
    compared -- `seen` already excludes empty fields, so a round carrying,
    say, only `results_jst` and `payment_deadline_jst` is checked as that
    pair, never against an anchor it doesn't have.
    """
    seen = [(name, _stamp_parts(stamps[name])) for name in TIMESTAMP_FIELDS if name in stamps]
    for (a_name, a_parts), (b_name, b_parts) in zip(seen, seen[1:], strict=False):
        if b_parts < a_parts:
            return f"{a_name} ({stamps[a_name]}) and {b_name} ({stamps[b_name]}) are out of order"
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

    `page_text` goes through `normalize_page_text`, not bare `collapse`: it
    applies the SAME 60k cap the model's own prompt is built under, so a quote
    can never verify against text the model was never shown. It is idempotent,
    so this costs nothing when the caller already normalized.
    """
    page = normalize_page_text(page_text)
    folded_page = _fold_digits(page)
    page_numbers = set(normalize_numbers(page))
    known_legs = {label.strip() for label in leg_labels}
    accepted: list[ProposedRound] = []
    rejected: list[str] = []

    for proposed in rounds:
        label = proposed.label or "(unlabelled round)"
        reason = _reject_reason(proposed, folded_page, page_numbers, known_legs, today)
        if reason is None:
            # `evidence` is proofreading scaffolding and must never ride into
            # the document that gets committed -- stripped here as one of two
            # layers (the draft parser strips it again downstream).
            cleaned_data = {k: v for k, v in proposed.data.items() if k != "evidence"}
            # The evidence that DOES survive onto the preview must be trimmed
            # to the keys `_reject_reason` actually verified against the page
            # -- the TIMESTAMP_FIELDS this round carries. `_reject_reason`
            # only ever checks those; any OTHER key the model wrote into
            # `evidence` (a field this round doesn't have, one it invented) is
            # unchecked free text, and rendering it under a "Read from the
            # ticket page:" heading would be exactly the misdirection this
            # whole module exists to prevent, whatever the checked fields say.
            verified_keys = _present_timestamp_fields(proposed.data)
            cleaned_evidence = {k: v for k, v in proposed.evidence.items() if k in verified_keys}
            accepted.append(replace(proposed, data=cleaned_data, evidence=cleaned_evidence))
        else:
            rejected.append(f"round {label!r}: {reason}")

    return Verdict(accepted=tuple(accepted), rejected=tuple(rejected))


def _reject_reason(
    proposed: ProposedRound,
    folded_page: str,
    page_numbers: set[int],
    known_legs: set[str],
    today: date,
) -> str | None:
    """The first reason this round cannot be trusted, or None."""
    stamps = {
        name: str(proposed.data[name]).strip() for name in _present_timestamp_fields(proposed.data)
    }
    if not stamps:
        return "no timestamps at all -- a round with no deadline is a label, not a rung"

    for name, stamp in stamps.items():
        quote = collapse(str(proposed.evidence.get(name) or ""))
        if not quote:
            return f"no evidence for {name} ({stamp})"
        if len(quote) > _MAX_QUOTE_CHARS:
            return (
                f"the quote for {name} is {len(quote)} characters -- quoting "
                f"that much of the page is not evidence: {quote[:60]!r}..."
            )
        if _fold_digits(quote) not in folded_page:
            return f"the quote for {name} is not on the page: {quote!r}"
        parts = _stamp_parts(stamp)
        if parts is None:
            return f"{name} ({stamp}) is not a 'YYYY-MM-DD HH:MM' timestamp"
        year, month, day, _hour, _minute = parts
        if not (today.year - _PAST_YEARS <= year <= today.year + _FUTURE_YEARS):
            return f"{name} ({stamp}) has an implausible year"
        try:
            date(year, month, day)
        except ValueError:
            return f"{name} ({stamp}) is not a real calendar date"
        if not _quote_carries_stamp(quote, page_numbers, parts):
            return f"the quote for {name} does not carry {stamp}: {quote!r}"

    order_problem = _check_order(stamps)
    if order_problem is not None:
        return order_problem

    # `applies_to` omitted, None, empty or an empty string all mean "every
    # leg" -- the same as `Round.applies_to` elsewhere in this app. Anything
    # else that isn't a list gets no free pass: this module's contract is
    # that nothing is dropped silently, and a bare scalar ("applies_to: Day
    # 9", an entirely ordinary way for a model to answer wrong) is exactly
    # the unrecognized shape that contract exists to catch, not skip.
    applies_to = proposed.data.get("applies_to")
    if applies_to:
        if not isinstance(applies_to, list):
            return (
                f"applies_to is not a list of leg labels "
                f"({type(applies_to).__name__}: {applies_to!r})"
            )
        for leg in applies_to:
            leg_name = str(leg).strip()
            if leg_name not in known_legs:
                return f"applies_to names {leg_name!r}, which is not a leg of this draft"

    return None
