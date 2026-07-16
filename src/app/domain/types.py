"""Core vocabulary of the domain.

These enums are imported by BOTH the pure domain logic and the ORM models,
so the database and the business rules can never drift apart on what a
"window kind" or an "anchor" means.

Stored as strings in SQLite (readable in any DB browser, stable across
enum reordering).
"""

import enum


class WindowKind(enum.StrEnum):
    """What a deadline window represents in the Japanese concert lifecycle."""

    LOTTERY_ROUND = "lottery_round"                  # 先行抽選 round (最速/1次/2次...)
    ELIGIBILITY_ITEM_SALE = "eligibility_item_sale"  # serial-code item on sale (CD/BD)
    STREAM_TICKET_SALE = "stream_ticket_sale"        # 配信チケット, often per concert day
    GENERAL_SALE = "general_sale"                    # 一般発売, first-come-first-served
    RESULT_ANNOUNCEMENT = "result_announcement"      # 当落発表 (usually a single moment)
    PAYMENT_DEADLINE = "payment_deadline"            # 入金期限 after winning
    OTHER = "other"                                  # future franchise inventions


class TagKind(enum.StrEnum):
    """What a tag names. GROUP tags contain member (usually ARTIST) tags."""

    FRANCHISE = "franchise"   # Hasunosora, Gakumas, Ikizuraibu...
    ARTIST = "artist"         # individual performers
    VENUE = "venue"           # Yokohama Arena, K-Arena...
    GROUP = "group"           # unit/group containing artist tags


class Anchor(enum.StrEnum):
    """Which moment a reminder offset is measured from."""

    OPENS = "opens"              # window.opens_at_utc
    CLOSES = "closes"            # window.closes_at_utc
    EVENT_START = "event_start"  # concert_day.starts_at_utc


class Channel(enum.StrEnum):
    """Where a reminder is delivered."""

    DM = "dm"
    CHANNEL = "channel"
