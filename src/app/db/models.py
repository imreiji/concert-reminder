"""Database schema (SQLAlchemy 2.0 typed style).

Design recap (see README):
  concerts ─┬─ concert_days      "Day 1", 昼公演/夜公演 — actual show datetimes
            └─ windows           generic typed deadline windows (lotteries, sales...)
  users ──── reminder_rules      "remind me N days before/after X"
  reminder_queue                 materialized outbox the scheduler drains

Timezone rule enforced HERE, at the boundary: the UTCDateTime type refuses
naive datetimes on write and always returns aware-UTC on read. SQLite itself
has no timezone concept, so we make the ORM the gatekeeper.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    TypeDecorator,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.types import Anchor, Channel, WindowKind


class UTCDateTime(TypeDecorator):
    """Store aware-UTC datetimes in SQLite; reject naive ones loudly."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected — store timezone-aware UTC only")
        return value.astimezone(UTC).replace(tzinfo=None)  # normalized, stored naive

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(100))
    avatar_hash: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Moncton")
    # tz_auto: timezone is browser-detected until the user overrides it manually
    tz_auto: Mapped[bool] = mapped_column(default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    rules: Mapped[list["ReminderRule"]] = relationship(back_populates="user")


class WebSession(Base):
    """Server-side login sessions: revocable, expiring, replay-proof.

    The cookie carries an opaque random token; only its SHA-256 lands here,
    so a leaked database cannot be turned back into usable cookies.
    Logout revokes the row -> a replayed cookie is dead server-side.
    """

    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Concert(Base):
    __tablename__ = "concerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    franchise: Mapped[str | None] = mapped_column(String(100))  # "Hasunosora", "Gakumas"...
    venue: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.discord_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    creator: Mapped["User"] = relationship()  # also fixes ORM insert ordering (FK alone doesn't)
    days: Mapped[list["ConcertDay"]] = relationship(
        back_populates="concert", cascade="all, delete-orphan", order_by="ConcertDay.starts_at_utc"
    )
    windows: Mapped[list["Window"]] = relationship(
        back_populates="concert", cascade="all, delete-orphan"
    )


class ConcertDay(Base):
    __tablename__ = "concert_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(100))  # "Day 1", "Day 2 夜公演"
    starts_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)

    concert: Mapped[Concert] = relationship(back_populates="days")


class Window(Base):
    __tablename__ = "windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id", ondelete="CASCADE"))
    kind: Mapped[WindowKind] = mapped_column(
        Enum(WindowKind, values_callable=lambda e: [m.value for m in e])
    )
    label: Mapped[str] = mapped_column(String(200))  # "最速先行 Round 1", "Day 2 配信"
    opens_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closes_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    applies_to: Mapped[list | None] = mapped_column(JSON)  # optional concert_day ids
    url: Mapped[str | None] = mapped_column(String(500))

    concert: Mapped[Concert] = relationship(back_populates="windows")


class ReminderRule(Base):
    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    # Exactly one of these is set: concert-wide rule, or one-window rule.
    concert_id: Mapped[int | None] = mapped_column(ForeignKey("concerts.id", ondelete="CASCADE"))
    window_id: Mapped[int | None] = mapped_column(ForeignKey("windows.id", ondelete="CASCADE"))

    anchor: Mapped[Anchor] = mapped_column(
        Enum(Anchor, values_callable=lambda e: [m.value for m in e])
    )
    offset_days: Mapped[int] = mapped_column(default=-1)  # negative = before
    offset_hours: Mapped[int] = mapped_column(default=0)
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, values_callable=lambda e: [m.value for m in e]), default=Channel.DM
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger)  # when channel=CHANNEL
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    user: Mapped[User] = relationship(back_populates="rules")


class ReminderQueue(Base):
    """Materialized outbox. The scheduler's entire job is draining this table.

    Idempotency: the functional unique index below treats NULL window/day as 0,
    so re-planning after an edit UPSERTS instead of duplicating. (Plain SQLite
    UNIQUE constraints consider NULLs distinct — the coalesce() sidesteps that.)
    """

    __tablename__ = "reminder_queue"
    __table_args__ = (
        Index(
            "uq_reminder_queue_dedupe",
            "rule_id",
            text("coalesce(window_id, 0)"),
            text("coalesce(day_id, 0)"),
            "anchor",
            unique=True,
        ),
        Index("ix_reminder_queue_due", "sent_at_utc", "fire_at_utc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("reminder_rules.id", ondelete="CASCADE"))
    window_id: Mapped[int | None] = mapped_column(ForeignKey("windows.id", ondelete="CASCADE"))
    day_id: Mapped[int | None] = mapped_column(ForeignKey("concert_days.id", ondelete="CASCADE"))
    anchor: Mapped[Anchor] = mapped_column(
        Enum(Anchor, values_callable=lambda e: [m.value for m in e])
    )
    fire_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    sent_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
