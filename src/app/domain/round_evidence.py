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
     does not. That rule reads the JAPANESE shape written in full; the
     abbreviated Japanese shape and the English one each get their own,
     equally strict reader -- see `_carryover_stamp_in` and
     `_english_stamp_in`;
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

TWO LANGUAGES, TWO MATCHERS -- NEVER ONE LOOSE ONE. Rule 4 above encodes the
Japanese order (`2026年8月5日（水）19:00`: date first, month as a NUMBER, time
immediately after). English states the same fact in an order that rule cannot
see -- the time FIRST, the month as a WORD, the year AFTER the day -- and the
overseas-package section of an international page is written in it. A live run
over the real catalogue (2026-08-10) accepted 39 rounds with no invented
timestamps and false-rejected exactly one: the LoveLive! Series 15th
Anniversary page's

    "From 19:00 on Wednesday, August 5, 2026 JST to 23:59 on Monday,
     August 17, 2026 JST"

quoted verbatim, both anchors correct, and discarded. So `_english_stamp_in`
is a SECOND matcher tried after the Japanese one fails, never a loosening of
it: the Japanese path is byte-for-byte what it was, and each matcher stays the
strictest reading of the one shape it knows. Widening one rule to cover both
orders is how a rule that catches "a real date and someone else's real time"
stops catching it -- note that the quote above states TWO deadlines nine
characters apart, so it is itself the splice a loose reader would fall for.

THREE SHAPES, THREE MATCHERS -- same principle, one more time. The same live
run rejected

    抽選応募期間：2026年7月14日（火）21:00 ～ 28日（火）23:59

for the OPPOSITE half of the same line it had just accepted: in a Japanese
range the closing date drops 月 (and 年) because they repeat the opening's, so
the closing day has no month beside it to be adjacent to. That abbreviation is
the ordinary way a Japanese ticket page states a window -- more common than the
English shape above -- and `_carryover_stamp_in` reads it as a THIRD matcher,
tried after the first fails. It lets a bare day inherit a month from an EARLIER
date IN THE SAME QUOTE, and every extra thing it demands (the day written as
`日`, no `月` re-anchoring in between, the day climbing rather than rolling
over) exists because the abbreviation removes evidence the full shape supplied
-- a matcher that reads less of the page must not therefore ask less of it.

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

# -- The abbreviated Japanese shape -----------------------------------------
#
# 「2026年7月14日（火）21:00 ～ 28日（火）23:59」. In a range, the closing date
# routinely drops 月 (and 年) because they repeat the opening's -- see
# `_carryover_stamp_in`. These read the ERA MARKERS, which is exactly what the
# main loop cannot do: `_blank_era_words` erases 年月日 before it counts number
# tokens, so to the main loop a day and a minute look alike. The whole safety
# of the carryover rests on telling them apart, so it works on the marked text.
# Each leading `(?<!\d)` stops a longer number being read as a short one from
# its tail ("2026月" would otherwise offer month 26).
_JP_FULL_DATE = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*年\s*)?(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"
)
_JP_MONTH_MARK = re.compile(r"(?<!\d)\d{1,2}\s*月")
# The day must be WRITTEN as a day. The ':' and '.' in the leading fence are
# the same fence `_EN_DAY` carries and for the same reason -- a number that is
# the tail of something else is not a day. The TRAILING fence is what keeps 日
# meaning "the Nth" rather than "days": 「28日間」 is a span of 28 days, 「28日
# 目」 the 28th day OF something, 「28日分」 28 days' worth, and a page saying
# 「先着28日間、23:59 締切」 states no closing date at all -- but the counter
# sits exactly where a carried-over day would, with a real time after it.
_JP_BARE_DAY = re.compile(r"(?<![\d:.])(?P<day>\d{1,2})\s*日(?![間目分後前以程])")

# -- The English shape ------------------------------------------------------
#
# Month names TITLE-CASED and ALL-CAPS only, never lowercase, and the reason is
# "May": it is the one month that is also an ordinary English word, and a page
# reading "winners may 5 days later, from 23:59" would otherwise offer a date.
# Requiring a capital costs an all-lowercase page its round (a visible false
# reject, one line typed by hand) and buys the modal verb out of the grammar
# entirely -- the trade this module makes everywhere.
_EN_MONTHS: dict[str, int] = {
    "January": 1, "Jan": 1,
    "February": 2, "Feb": 2,
    "March": 3, "Mar": 3,
    "April": 4, "Apr": 4,
    "May": 5,
    "June": 6, "Jun": 6,
    "July": 7, "Jul": 7,
    "August": 8, "Aug": 8,
    "September": 9, "Sept": 9, "Sep": 9,
    "October": 10, "Oct": 10,
    "November": 11, "Nov": 11,
    "December": 12, "Dec": 12,
}
# Longest first, so "September" is tried before "Sept" before "Sep" -- with the
# short form first, `Sep` would match and the rest of the word would be left to
# the trailing `(?![A-Za-z])` to reject, losing a perfectly good date.
_MONTH_ALT = "|".join(
    form
    for name in sorted(_EN_MONTHS, key=len, reverse=True)
    for form in (name, name.upper())
)
_WEEKDAY_ALT = "|".join(
    sorted(
        (
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
            "Mon", "Tue", "Tues", "Wed", "Weds", "Thu", "Thur", "Thurs", "Fri", "Sat", "Sun",
        ),
        key=len,
        reverse=True,
    )
)

# The day is fenced on BOTH sides so it can never be part of a longer number
# that means something else -- each half of this fence was a false accept
# before it was a lookaround:
#   trailing: without it "5 August 2026" ALSO reads month-first as "August 20"
#     (the "20" of 2026), a date the page states nowhere;
#   leading: a bare `(?<!\d)` still lets the day-first form take the MINUTE of
#     a clock time as its day, so "Doors 18:05 August, 2026 at 19:00" offered
#     "05 August, 2026" with a real connective and a real time after it, and
#     proved 2026-08-05 19:00. A ':' or '.' in front means this number is the
#     tail of something, not a day.
_EN_DAY = r"(?<![\d:.])(?P<day>\d{1,2})(?!\d)(?i:st|nd|rd|th)?"
# Ordinal suffixes are accepted and NOT checked for agreement -- "5th" and the
# typo "5st" are the same evidence about the same day, and a grammar check here
# would only ever turn a real page into a rejection.
_EN_YEAR = r"(?:,?\s*(?P<year>\d{4})(?!\d))?"
_EN_MONTH = rf"(?P<month>{_MONTH_ALT})\.?(?![A-Za-z])"
_EN_DATES = (
    re.compile(rf"{_EN_MONTH}\s+{_EN_DAY}{_EN_YEAR}"),  # August 5, 2026
    re.compile(rf"{_EN_DAY}\s+{_EN_MONTH}{_EN_YEAR}"),  # 5 August 2026
)

# A 12-hour time is refused outright rather than converted: "7:00 PM" is 19:00,
# a model claiming 07:00 from it is twelve hours wrong, and matching the digits
# would call that proved. Teaching this module to convert means owning every
# way the conversion can be got backwards on evidence it is supposed to be
# checking, so am/pm is simply not evidence here -- a visible false reject.
_EN_TIME = re.compile(r"(?<![\d:])(?P<hour>\d{1,2}):(?P<minute>\d{2})(?![\d:])(?!\s*[APap]\.?[Mm])")

# What may sit between a date and its time, and NOTHING else. This is the
# English locality guarantee: not a character budget (a budget is what lets
# "August 5, 2026 JST to 23:59" read as one statement -- nine characters, two
# different deadlines) but an exhaustive list of the connectives that actually
# bind a time to a date. "to", "until", "and", or any other word that starts a
# new clause is absent by construction, so a splice fails on the words between
# its halves rather than on how far apart they are.
_EN_GAP_TIME_BEFORE = re.compile(  # "19:00 [JST] on [Wednesday,] August 5"
    rf"\s*(?:\(?JST\)?)?\s*(?:on\s+)?(?:(?:{_WEEKDAY_ALT})\.?,?\s*)?", re.IGNORECASE
)
_EN_GAP_TIME_AFTER = re.compile(  # "August 5, 2026 [JST][,] [at|from] 19:00"
    r"\s*(?:\(?JST\)?)?\s*,?\s*(?:(?:at|from)\s+)?", re.IGNORECASE
)


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

    In JAPANESE, below; in ENGLISH, in `_english_stamp_in`, which this falls
    through to and which is a separate matcher for a separate grammar -- an
    international page writes its overseas section in English, and neither
    reader is loosened to cover the other's shape.

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

    # Nothing in the Japanese shape as written in full. Try the two other
    # matchers -- each a separate reader of a separate shape, tried after this
    # loop, never a relaxation of it: the loop above is unchanged.
    if _carryover_stamp_in(quote, parts, minute_waived=minute_waived):
        return True
    return _english_stamp_in(quote, parts)


def _carryover_stamp_in(
    quote: str, parts: tuple[int, int, int, int, int], *, minute_waived: bool
) -> bool:
    """Does this quote say this timestamp with the month left to carry over?

    A Japanese range writes its closing date without 月 (and without 年),
    because they repeat the opening's:

        抽選応募期間：2026年7月14日（火）21:00 ～ 28日（火）23:59

    The loop above wants the month as the number token immediately before the
    day, so it proves the OPENING anchor of that line and rejects the CLOSING
    one -- the abbreviation is not a corner case but the ordinary way a
    Japanese ticket page states a window, and the 2026-08-10 live press
    false-rejected a correct round on exactly this line. So: a THIRD matcher,
    for the abbreviated shape only, holding every guarantee the full shape has
    plus the ones the abbreviation itself needs.

    A bare day is a date only when ALL of this holds, and each clause is a way
    a fabricated deadline would otherwise get through:

      * it is WRITTEN as a day -- `\\d+日`, not merely a number, and not 日 as
        a COUNTER (「28日間」, 「28日目」, 「28日分」). Without the marker the
        opening time of the line above donates a day (21), an hour (00) and a
        minute (28) that are adjacent in exactly the order this rule wants,
        and 2026-07-21 00:28 -- a stamp the page states nowhere -- reads as
        local, contiguous evidence; without the counter fence 「先着28日間、
        23:59 締切」 proves a closing date that line never states;
      * an ANCHOR date precedes it IN THIS QUOTE: a full 「M月D日」 whose month
        is the claimed one. Never a later date (Japanese states the month
        before the day it governs, so a date after one says nothing about it),
        and never one from elsewhere on the page -- a quote that needs the rest
        of the page to be read is not self-sufficient evidence;
      * NOTHING RE-ANCHORS THE MONTH IN BETWEEN. The anchor must be the last
        `\\d+月` before the bare day, so a heading like 【8月】 -- which names a
        month without naming a day, and would slip past a rule that only
        looked for whole dates -- breaks the carry instead of being stepped
        over. This is what makes "the LAST date before it" safe to rely on;
      * the anchor's day is STRICTLY EARLIER than the bare one. 「7月28日 ～
        3日」 means August 3, but that is an inference about rollover, not a
        reading, and this module never infers: requiring the day to climb
        leaves the same month as the only consistent reading of what is
        actually written. Both answers are refused there -- the mechanical
        July 3 because it is wrong, the intended August 3 because the quote
        does not say it -- and a human types that round in.

    The YEAR carries on the same terms as the month, which means it is
    re-anchored on the same terms too: an intervening 年 breaks the carry just
    as an intervening 月 does. Where the anchor states one it gets the
    treatment English already gets from a date that states its year -- an
    anchor saying 2026 refuses a claim of 2027 outright -- and where it states
    none, the caller's broad "in this quote or anywhere on the page" rule
    stands, which is the Japanese-page-with-the-year-in-a-heading case.

    Everything downstream of the day is the main loop's rule verbatim: the
    hour is the VERY NEXT number token after the day, the minute immediately
    after it (unless waived), and the span from the day to the end of the time
    is capped at `_MAX_STAMP_SPAN_CHARS`. Measured from the BARE DAY, not from
    the anchor: the anchor is what makes the day a date, but the deadline
    itself is stated in the abbreviated half, and that half is as short as any
    other real deadline line.
    """
    year, month, day, hour, minute = parts
    text = _fold_digits(quote)
    tokens = _number_tokens(quote)  # positions align with `text`; folding is 1:1
    dates = {m.start("month"): m for m in _JP_FULL_DATE.finditer(text)}
    marks = list(_JP_MONTH_MARK.finditer(text))

    for bare in _JP_BARE_DAY.finditer(text):
        start = bare.start("day")
        if int(bare.group("day")) != day:
            continue

        preceding = [m for m in marks if m.start() < start]
        if not preceding:
            continue  # a bare day with no month before it in this quote
        anchor = dates.get(preceding[-1].start())
        if anchor is None:
            continue  # the nearest month names no day: 【8月】 anchors nothing
        if anchor.end() > start:
            continue  # this "bare" day is the anchor's own -- the main loop's job
        if int(anchor.group("month")) != month or int(anchor.group("day")) >= day:
            continue
        if anchor.group("year") is not None and int(anchor.group("year")) != year:
            continue
        if "年" in text[anchor.end() : start]:
            continue  # something restated the year in between; it re-anchors too

        time_index = next((i for i, (_, s, _) in enumerate(tokens) if s == start), None)
        if time_index is None:
            continue
        time_index += 1
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

        if time_end - start <= _MAX_STAMP_SPAN_CHARS:
            return True

    return False


def _english_stamp_in(quote: str, parts: tuple[int, int, int, int, int]) -> bool:
    """Does this quote say this timestamp in ENGLISH, IN ONE PLACE?

    Same guarantee as the Japanese loop, expressed in the grammar English
    actually uses. A date is a month WORD and a day adjacent to each other in
    either order, with only whitespace between them ("August 5", "5 August").
    A time is `HH:MM`. The two are one statement only when the text BETWEEN
    them is a connective that binds them -- `_EN_GAP_TIME_BEFORE` when the time
    leads ("19:00 on Wednesday, August 5, 2026"), `_EN_GAP_TIME_AFTER` when it
    follows ("August 5, 2026 at 19:00") -- matched in FULL, so anything else
    there is a refusal rather than a tolerated gap.

    That whitelist, not the character span, is what makes the motivating quote
    safe. "From 19:00 on Wednesday, August 5, 2026 JST to 23:59 on Monday,
    August 17, 2026 JST" states two deadlines whose halves interleave: the
    second time sits NINE characters after the first date, nearer to it than to
    its own. Any distance rule pairs them and proves a deadline the page never
    states; the words between them ("JST to ") do not bind, so this one
    doesn't. `_MAX_STAMP_SPAN_CHARS` is applied on top -- the same number the
    Japanese path uses, unreachable while the whitelist stays this short, and
    there precisely so that adding a connective to it later cannot quietly buy
    an unbounded one.

    The YEAR is the one place this is STRICTER than the Japanese path, because
    English gives it something to be strict with: the year is written beside
    the day, so when the date states one it must be the claimed one. Where the
    date states none, the broad "in this quote or anywhere on the page" rule
    the caller already applied stands, exactly as it does for a Japanese page
    that puts the year in a heading.
    """
    year, month, day, hour, minute = parts
    text = _fold_digits(quote)

    dates = [
        match
        for pattern in _EN_DATES
        for match in pattern.finditer(text)
        if _EN_MONTHS[match.group("month").title()] == month
        and int(match.group("day")) == day
        and (match.group("year") is None or int(match.group("year")) == year)
    ]
    if not dates:
        return False
    times = [
        match
        for match in _EN_TIME.finditer(text)
        if int(match.group("hour")) == hour and int(match.group("minute")) == minute
    ]

    for stated_date in dates:
        for stated_time in times:
            if stated_time.end() <= stated_date.start():
                gap = text[stated_time.end() : stated_date.start()]
                bound, span = _EN_GAP_TIME_BEFORE, stated_date.end() - stated_time.start()
            elif stated_time.start() >= stated_date.end():
                gap = text[stated_date.end() : stated_time.start()]
                bound, span = _EN_GAP_TIME_AFTER, stated_time.end() - stated_date.start()
            else:
                continue  # overlapping: the "time" is part of the date's digits
            if bound.fullmatch(gap) and span <= _MAX_STAMP_SPAN_CHARS:
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
    # Built by walking TIMESTAMP_FIELDS itself, not `present` directly: that
    # set is for MEMBERSHIP only (Python randomizes string-hash order per
    # process, so iterating a set is non-deterministic run to run), and this
    # dict's OWN iteration order is what decides which reason the loop below
    # returns first when more than one timestamp is bad -- this module's
    # whole contract is that the operator sees the FIRST reason, in ladder
    # order, not whichever one a hash happened to yield first.
    present = _present_timestamp_fields(proposed.data)
    stamps = {
        name: str(proposed.data[name]).strip()
        for name in TIMESTAMP_FIELDS
        if name in present
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
