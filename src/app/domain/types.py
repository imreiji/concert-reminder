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
    # A second, nested campaign that only holders of a qualifying round's
    # ticket may enter (e.g. an upgrade lottery for premium seats open only
    # to people who already secured a ticket in an earlier round).
    UPGRADE = "upgrade"
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
    """What a tag names. GROUP tags contain member tags -- ARTIST tags,
    CHARACTER tags, or a mix."""

    FRANCHISE = "franchise"   # Hasunosora, Gakumas, Ikizuraibu...
    ARTIST = "artist"         # individual performers
    VENUE = "venue"           # Yokohama Arena, K-Arena...
    GROUP = "group"           # unit/group containing artist tags
    CHARACTER = "character"   # 如月千早 -- voiced by an ARTIST, see Tag.voiced_by_tag_id


# Which kinds a tag of each kind may sit UNDER. Both shapes mean the same thing
# -- "the broader thing I belong to" -- and a subunit belongs to its group the
# way a group belongs to its franchise.
#
# ONE table, here in the pure vocabulary, because there are two write paths into
# `Tag.parent_id` -- POST /tags and the catalogue importer -- and they disagreed
# once already: the importer kept a franchise-only rule after the editor widened,
# so a file could not express a subunit at all. A kind absent from this mapping
# takes NO parent.
ALLOWED_PARENT_KINDS: dict[TagKind, tuple[TagKind, ...]] = {
    TagKind.GROUP: (TagKind.FRANCHISE, TagKind.GROUP),
    TagKind.CHARACTER: (TagKind.FRANCHISE,),
}


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


class LegResult(enum.StrEnum):
    """Per-day resolution of a multi-leg round's lottery. Deliberately NOT
    LotteryOutcome: a day only ever resolves won-or-lost; applied and paid
    stay round-level concepts (see the 2026-07-26 per-leg-outcomes spec)."""

    WON = "won"
    LOST = "lost"


class SubscriptionState(enum.StrEnum):
    """A user's explicit per-concert override on the tag-derived default,
    stored per (user, concert) in ConcertSubscription. The absence of a row
    means "follow the tag default" (the pre-branch behaviour); a row is an
    explicit opt-in or opt-out:

    - SUBSCRIBED: track this concert even if no followed tag matches.
    - OPTED_OUT: prune this concert even though a followed tag matches.

    (A per-leg opt-out is the same idea one level down, but needs no state
    column -- LegOptOut's presence alone means opted out.)"""

    SUBSCRIBED = "subscribed"
    OPTED_OUT = "opted_out"


class DeliveryOutcome(enum.StrEnum):
    """A DM send's result. Distinct from "should this row be marked sent"
    (SUCCESS and FORBIDDEN both do; TRANSIENT_FAILURE doesn't) and from
    "should the per-user dm_blocked_since flag change" (SUCCESS clears it,
    FORBIDDEN sets it, TRANSIENT_FAILURE touches neither).

    Lives here rather than in scheduler/loop.py, where it was defined
    originally, because db/models.py types a column on it and db/ must never
    import from scheduler/.
    """

    SUCCESS = "success"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"


class DeliverySource(enum.StrEnum):
    """Which outbox a delivery came from. Both are logged: the likeliest way
    this app messages the wrong people is handle_newly_tagged fanning a
    new_event NOTIFICATION across a tag's followers, not a reminder."""

    REMINDER = "reminder"
    NOTIFICATION = "notification"


class BroadcastMode(enum.StrEnum):
    """How an admin broadcast chose its recipients. All three are RESOLVED
    sets -- a known list of user ids at send time. Derived modes (everyone
    tracking a concert, followers of a tag) were considered and rejected: the
    set can change between the preview an admin approved and the send that
    executes, so the count they confirmed would be a lie."""

    BATCH = "batch"        # the recipients of one delivery_log batch
    ALL = "all"            # every user
    EXPLICIT = "explicit"  # a hand-entered list of discord ids
