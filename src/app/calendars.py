"""Calendar-feed discovery: WHICH feeds, how they are fetched, what counts as a lead.

Sits ABOVE db/ like `app/discovery.py` and `app/ops.py`: it imports `domain/`
and `db.service`, and nothing in `db/` imports it. The parser is pure and lives
in `domain/ics_read.py`; the host-pinned fetch is the SHARED one in
`app/fetching.py` -- ONE copy of that guard, so a weakness found in it cannot be
fixed in one caller and missed in the other.

The feed table is CODE-LEVEL CONFIG, not a table or an env var (design doc §1):
the set changes rarely, changing it is an edit+deploy exactly like the admin
whitelist, and each entry carries typed fields no env CSV expresses well. The
whole point of `include_prefixes` and `dates_are` living here as DATA is that
the next person tunes the roster without touching a line of logic.

`app.discovery` must stay importable from here -- it is not imported here on
purpose, since Task 5 adds the calendar pass INTO the sweep and the reverse
import would close a cycle. That is why USER_AGENT is spelled out below rather
than imported from `app.discovery`; a UA string is not a security control, and
duplicating it costs less than the cycle.

================================ THE LIVE PROBE ================================
Every candidate was fetched once with curl on 2026-08-03 (today, JST) and read
with `domain/ics_read.parse_ics`. Verdicts, and WHY, so a later reader can tell
a deliberate omission from an oversight:

  imas-tix  https://calendar.google.com/calendar/ical/maruamyu.net_4mns...
            X-WR-CALNAME "アイマス関連イベント チケット申込期限".
            1044 VEVENTs, 0 unreadable, dates 2017-11-01..2026-08-02.
            FUTURE (>= 2026-08-03): ZERO -- and it is KEPT anyway. The feed is
            demonstrably alive: DTSTAMP was today, the newest LAST-MODIFIED was
            2026-08-02T13:57Z, and July 2026 alone carries seven entries. A
            DEADLINE calendar empties its own forward window by construction --
            every entry is a date that passes -- so "no future rows" on one
            morning is a lull, not rot. The rule "a feed with no future VEVENTs
            is dropped" is aimed at DEAD feeds; applying it literally here would
            delete the im@s half of the feature (design doc §Why) on the
            strength of a one-day sample. Re-check if it is still empty in a
            month.
            Summaries are bare event names ("デレラガールズ 15th ... 一般抽選")
            with no prefix vocabulary at all -- single-purpose, so
            include_prefixes stays empty, as the design says.

  ll-fans   https://ll-fans.jp/articles/calendar listed EIGHT calendars, not the
            four the plan knew about. All eight are public, all eight fetched
            200, all eight parsed with ZERO unreadable rows, and all eight carry
            future entries -- so NOTHING was dropped for staleness. In
            particular the MAIN feed is not stale: 1764 VEVENTs running to
            2027-03-20, 49 of them future. (The design doc's "the main feed
            looked stale" reads as a partial sample; corrected here.)

            The site's own description explains the division of labour, and the
            data agrees with it exactly:
              メイン   = "シリーズの展開に関わる大きな出来事" -- ライブ/イベント
                         plus 誕生日・CD/BD・TV・生放送 noise.
              サブ     = "各グループで展開されるイベントの申込期限、ラジオ出演など"
                         -- ticket rounds plus radio.
            So the main feed is this roster's EVENT source and the group subs
            are its DEADLINE sources. `dates_are` is per FEED, so that split is
            what keeps every stored date honest; mixing the two inside one feed
            would file 申込締切 rows as performance dates, which is the precise
            mistake `date_is_deadline` was added to prevent (design doc §3).

            Observed SUMMARY vocabulary, 12 months to 2026-08-03, by feed
            (counts are entries, not leads):
              ll-main       イベント 85, CD 51, ライブ 42, 生放送 41, ラジオ 34,
                            TV 27, BD 16, 誕生日 14, 配信 14, 雑誌 12,
                            アップグレード受付 7, 映画 3, ファンミ 2, 一般発売 2,
                            and a scatter of one-off 先行 rows.
              ll-aqours     ラジオ 37, 一般発売 19, プレオーダー先行* 13,
                            最速先行* 5, CD先行抽選 4, アップグレード抽選 3,
                            当日券受付 2, TV 2.
              ll-nijigasaki TV 117, 一般発売 24, ラジオ 19, イベント 16,
                            最速先行* 12, 一挙放送 8, 同時視聴会 4, 当日券販売 4,
                            二次先行* 3, ファンディスク先行* 3.
              ll-liella     ラジオ 38, オフィシャル*先行 27, TV 11, 一般発売 9,
                            アップグレード* 9, 最速先行* 8, 当日券販売 7,
                            Liella! CLUB先行 12, ファミリーマート先行 3,
                            お渡し会 2, 一般抽選 2.
              ll-musical    舞台 13, and 24 promoter-named round rows
                            (イープラス/チケットぴあ/ローソンチケット/公式/2.5フレンズ).
              ll-hasunosora With×MEETS 84, 一般発売 65, 最速先行* 63, ラジオ 58,
                            プレミア公開 15, 同時視聴会 12, 雑誌 11, 二次先行* 6,
                            アップグレード* 6, 当日券* 5, バーチャルライブ 5.
              ll-ikizurai   ラジオ 61, 一般発売 14, アップグレード* 8, 最速先行* 8,
                            プレオーダー先行* 6, 追加先行 3, 見切れ席販売 3.
              ll-lovuca     大型大会 20, 発売 19, エリア予選 11, イベント 10,
                            生配信 3.  (This is the trading-card franchise.)

            The SEPARATOR IS AN ASCII COLON PLUS SPACE ("ライブ: X"), not the
            full-width "：" the plan guessed. Event prefixes below therefore
            carry their colon, which is load-bearing: `ライブ映像無料公開:` is a
            real ikizurai summary and a bare `ライブ` prefix would take it.

  DROPPED CATEGORIES, all deliberate: 誕生日・CD・BD・TV・ラジオ・生放送・配信・
  雑誌・一挙放送・同時視聴会・発売・大型大会・エリア予選・With×MEETS. Birthdays,
  discs, broadcasts and streams are the noise the owner asked to drop; the
  lovuca tournaments are card-game qualifiers, not performances.

  THE ONE ACCEPTED GAP, recorded rather than papered over: the round-row
  vocabulary is OPEN-ENDED, because each promoter names its own round --
  ll-liella alone produced 24 distinct heads in twelve months (オフィシャル5次先行,
  Liella! CLUB先行, ファミリーマート先行, いち早プレリザーブ先行, 一般抽選, ...).
  `include_prefixes` matches with `str.startswith`, so a promoter-named round
  can only be caught by naming it, and a list of names rots the week a new
  ticket agency appears. TICKET_PREFIXES below therefore holds only the GENERIC
  Japanese ticketing terms, which are stable and cover the FIRST round of
  essentially every campaign (最速先行 / 一般発売). A missed second round is not
  a missed concert: the campaign is already a lead through its first round, and
  triage verifies every round against the official page anyway (design doc §6).
  Widening this to a `contains`/regex matcher is the obvious next step IF the
  gap ever bites; it needs a per-ENTRY deadline flag to stay honest, which the
  per-FEED `dates_are` cannot express.
==============================================================================
"""

import logging
from dataclasses import dataclass
from datetime import date

import httpx

from app.db.service import DiscoveredInput
from app.domain.eventernote import ActorEvent
from app.domain.ics_read import parse_ics
from app.fetching import FetchError, PinnedHost, fetch_html

log = logging.getLogger(__name__)

ALLOWED_HOST = "calendar.google.com"
# Same UA the Eventernote sweep sends: one app, one name, whoever is being read.
USER_AGENT = "dekimasen.app/1.0 (event discovery)"
# A calendar body is bigger than a page by nature -- MEASURED on 2026-08-03, the
# main LL-Fans feed is 1.41 MB and grows every week, which is 70% of
# fetching.DEFAULT_MAX_BYTES. Left at the default, the largest and most valuable
# feed would start failing silently one day, counted only as "1 failed" in a log
# line. Raised with the measurement written down rather than tuned by feel.
MAX_FEED_BYTES = 5_000_000


class CalendarFetchError(Exception):
    """A feed could not be fetched. One feed failing must not cost the others."""


@dataclass(frozen=True)
class CalendarFeed:
    """One public `.ics` this app reads, and how to read it.

    `dates_are` says what a DTSTART MEANS in this feed -- a performance date or
    an application deadline -- and is what `DiscoveredEvent.date_is_deadline`
    is set from. `include_prefixes` empty means take every VEVENT.
    """

    key: str
    label: str
    url: str
    dates_are: str  # "deadline" | "event"
    include_prefixes: tuple[str, ...] = ()


def _ics_url(calendar_id: str) -> str:
    """A Google calendar id -> its public `.ics`. The `@` must be percent-encoded."""
    return (
        f"https://{ALLOWED_HOST}/calendar/ical/"
        f"{calendar_id.replace('@', '%40')}/public/basic.ics"
    )


# Performance categories on the LL-Fans MAIN calendar. Each carries its colon;
# see the probe record above for why.
EVENT_PREFIXES = ("ライブ:", "イベント:", "舞台:", "ファンミ:", "3DCGミュージカル:")
# Generic Japanese ticketing terms, deliberately WITHOUT a trailing colon: the
# round type sits between the term and the colon ("最速先行 受付終了 (23:59): X").
# "オフィシャル" rather than "オフィシャル先行" because the real feed spells it
# both オフィシャル先行2次 and オフィシャル2次先行.
TICKET_PREFIXES = (
    "最速先行",
    "オフィシャル",
    "一般発売",
    "一般抽選",
    "二次先行",
    "追加先行",
    "当日券",
    "アップグレード",
    "プレオーダー",
)

CALENDAR_FEEDS: tuple[CalendarFeed, ...] = (
    CalendarFeed(
        key="imas-tix",
        label="imas 申込期限",
        url=_ics_url("maruamyu.net_4mns4sokdr9nfhrpglg6so9450@group.calendar.google.com"),
        dates_are="deadline",
    ),
    CalendarFeed(
        key="ll-main",
        label="LL-Fans メイン",
        url=_ics_url("c_i1b3gbmjmbhqa0bong3ag68pj0@group.calendar.google.com"),
        dates_are="event",
        include_prefixes=EVENT_PREFIXES,
    ),
    CalendarFeed(
        key="ll-aqours",
        label="LL-Fans Aqours",
        url=_ics_url("c_fqe30janocv0k76kf63qa2i6hk@group.calendar.google.com"),
        dates_are="deadline",
        include_prefixes=TICKET_PREFIXES,
    ),
    CalendarFeed(
        key="ll-nijigasaki",
        label="LL-Fans 虹ヶ咲",
        url=_ics_url("c_r5tf64n6deed8dkmj6h7r6tr90@group.calendar.google.com"),
        dates_are="deadline",
        include_prefixes=TICKET_PREFIXES,
    ),
    CalendarFeed(
        key="ll-liella",
        label="LL-Fans Liella!",
        url=_ics_url("c_mg5gu0t8fltuhvfr203tsg5qng@group.calendar.google.com"),
        dates_are="deadline",
        include_prefixes=TICKET_PREFIXES,
    ),
    # The musical feed is the one SUB read as an EVENT feed: its 舞台 rows are
    # the only place the stage runs appear (the main calendar does not carry
    # them), while its own round rows are all promoter-named and unreachable by
    # prefix anyway.
    CalendarFeed(
        key="ll-musical",
        label="LL-Fans ミュージカル",
        url=_ics_url(
            "c_b0e2c7d76bf5b353777f7c1a4d5eda9da8ab3b3d36d9cce10fb44c0a269b6f59"
            "@group.calendar.google.com"
        ),
        dates_are="event",
        include_prefixes=("舞台:",),
    ),
    CalendarFeed(
        key="ll-hasunosora",
        label="LL-Fans 蓮ノ空",
        url=_ics_url(
            "c_50b432266bf298e686d867169129d16b9845448a5cb8749909757ea027c55f97"
            "@group.calendar.google.com"
        ),
        dates_are="deadline",
        include_prefixes=TICKET_PREFIXES,
    ),
    CalendarFeed(
        key="ll-ikizurai",
        label="LL-Fans イキヅライブ！",
        url=_ics_url(
            "c_a649fef56bf7ff476ad7a43ac7877644775a89caf940e2fe5368074f46e28595"
            "@group.calendar.google.com"
        ),
        dates_are="deadline",
        include_prefixes=TICKET_PREFIXES,
    ),
    # The trading-card feed: fan meetings are real ticketed events, the 大型大会 /
    # エリア予選 tournaments and 発売 product dates are not this app's business.
    CalendarFeed(
        key="ll-lovuca",
        label="LL-Fans ラブカ",
        url=_ics_url(
            "c_4df0b7ac414b06de1282ff07d37c2781eb3425859a749f05e7c98530fae09451"
            "@group.calendar.google.com"
        ),
        dates_are="event",
        include_prefixes=("イベント:",),
    ),
)


async def fetch_feed(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Fetch one public `.ics`. `transport` is test-only.

    Mirrors `discovery.fetch_actor_events`, down to catching FetchError -- the
    BASE class, so both HostNotAllowed and FetchFailed become the one error a
    sweep knows how to skip past.
    """
    try:
        return await fetch_html(
            url,
            policy=PinnedHost(ALLOWED_HOST),
            user_agent=USER_AGENT,
            max_bytes=MAX_FEED_BYTES,
            transport=transport,
        )
    except FetchError as exc:
        raise CalendarFetchError(str(exc)) from exc


def feed_leads(
    feed: CalendarFeed, ics_text: str, today_jst: date
) -> tuple[list[DiscoveredInput], int]:
    """One feed's body -> the leads it offers, and its unreadable-row count.

    A pure adapter, and deliberately the only place the feed table meets the
    lead pipeline. Two filters, in this order:

      1. `include_prefixes` -- empty takes everything. A SUMMARY the list does
         not want is DROPPED, and is NOT counted as skipped: skipped means
         UNREADABLE, and folding a working filter into it would make a healthy
         feed read as a rotting one on the status line.
      2. the past. `event.date >= today_jst`, so TODAY counts -- the same
         boundary `domain/eventernote.future_events` uses, because a deadline
         closing tonight is exactly the one worth surfacing.

    Raises `IcsError` (from `parse_ics`) when the body is not an iCalendar at
    all; the caller counts that feed failed, like a failed fetch.
    """
    calendar = parse_ics(ics_text)
    leads: list[DiscoveredInput] = []
    for event in calendar.events:
        if feed.include_prefixes and not event.summary.startswith(feed.include_prefixes):
            continue
        if event.date < today_jst:
            continue
        leads.append(
            DiscoveredInput(
                # Namespaced, so a calendar UID can never collide with an
                # Eventernote numeric id in the single-column UNIQUE.
                event=ActorEvent(
                    event_id=f"{feed.key}:{event.uid}",
                    title=event.summary,
                    date=event.date,
                    venue=event.location,
                ),
                # No tag surfaced this: a feed is not a subscription.
                tag_id=None,
                source=feed.key,
                date_is_deadline=feed.dates_are == "deadline",
            )
        )
    return leads, calendar.skipped
