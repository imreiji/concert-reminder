"""Core vocabulary of the domain.

These enums are imported by BOTH the pure domain logic and the ORM models,
so the database and the business rules can never drift apart on what a
"round kind" or an "anchor" means.

Stored as strings in SQLite (readable in any DB browser, stable across
enum reordering).
"""

import enum


class RoundKind(enum.StrEnum):
    """What a deadline round represents in the Japanese concert lifecycle.

    A round can carry up to 4 timestamps (opens/closes/results/payment,
    see Round in db/models.py) -- these members classify what KIND of
    round it is, independent of how many of those 4 fields are filled in.
    RESULT_ANNOUNCEMENT/PAYMENT_DEADLINE stay valid standalone kinds for
    editors who don't want to bundle everything into one lottery round.
    """

    LOTTERY_ROUND = "lottery_round"                  # 先行抽選 round (最速/1次/2次...)
    ELIGIBILITY_ITEM_SALE = "eligibility_item_sale"  # serial-code item on sale (CD/BD)
    STREAM_TICKET_SALE = "stream_ticket_sale"        # 配信チケット, often per concert day
    # 一般発売: a free-to-enter lottery round requiring no serial code --
    # NOT first-come-first-served (see FCFS_SALE for that).
    GENERAL_SALE = "general_sale"
    RESULT_ANNOUNCEMENT = "result_announcement"      # 当落発表 (usually a single moment)
    PAYMENT_DEADLINE = "payment_deadline"            # 入金期限 after winning
    # True first-come-first-served: buy outright the instant it opens, no
    # application/lottery step. Per the guide, always the last round for a
    # concert and not guaranteed to happen (only if lottery rounds leave
    # tickets unsold).
    FCFS_SALE = "fcfs_sale"
    # The overseas tour package ("gaijin pack") lottery track: a hotel +
    # ticket bundle sold via its own lottery, structurally separate from
    # the eplus serial-code system. Not guaranteed to exist per concert.
    TOUR_PACKAGE = "tour_package"
    OTHER = "other"                                  # future franchise inventions


class ConcertKind(enum.StrEnum):
    """What TYPE of event this is, independent of its lottery/sale rounds.
    Optional, purely organizational -- existing concerts have no kind set
    until an editor backfills one."""

    CONCERT = "concert"
    TOUR = "tour"
    FESTIVAL = "festival"
    RELEASE = "release"
    MEET_GREET = "meet_greet"
    FAN_MEETING = "fan_meeting"
    TALK = "talk"
    STAGE = "stage"
    SCREENING = "screening"
    GOODS = "goods"
    STREAM = "stream"
    OTHER = "other"


class TagKind(enum.StrEnum):
    """What a tag names. GROUP tags contain member (usually ARTIST) tags."""

    FRANCHISE = "franchise"   # Hasunosora, Gakumas, Ikizuraibu...
    ARTIST = "artist"         # individual performers
    VENUE = "venue"           # Yokohama Arena, K-Arena...
    GROUP = "group"           # unit/group containing artist tags


class Anchor(enum.StrEnum):
    """Which moment a reminder offset is measured from."""

    OPENS = "opens"              # round.opens_at_utc
    CLOSES = "closes"            # round.closes_at_utc
    RESULTS = "results"          # round.results_at_utc
    PAYMENT = "payment"          # round.payment_deadline_at_utc
    EVENT_START = "event_start"  # concert_day.starts_at_utc


class Channel(enum.StrEnum):
    """Where a reminder is delivered."""

    DM = "dm"
    CHANNEL = "channel"


class LotteryOutcome(enum.StrEnum):
    """A user's recorded progress through one round's lottery, tracked per
    (user, round) in RoundOutcome. Strict sequence enforced in
    record_round_outcome, not at the DB layer:
    APPLIED -> (WON | LOST) -> PAID (PAID only reachable from WON)."""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    WON = "won"
    LOST = "lost"
    PAID = "paid"
