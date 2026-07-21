"""Timezone discipline for the whole app.

Rules (see README):
  1. The database stores UTC only, always timezone-aware.
  2. Event times are ENTERED in JST (that's how Japanese ticketing announces them).
  3. Times are DISPLAYED in the user's timezone, with JST alongside.

These helpers are the only place conversions happen. If a datetime bug ever
appears, it lives here or in a caller that bypassed here.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
JST = ZoneInfo("Asia/Tokyo")


def jst_to_utc(naive_jst: datetime) -> datetime:
    """Interpret a naive datetime as JST and return aware UTC.

    Web forms submit naive datetimes; the form contract is 'this is JST'.
    """
    if naive_jst.tzinfo is not None:
        raise ValueError("expected a naive datetime (form input); got aware")
    return naive_jst.replace(tzinfo=JST).astimezone(UTC)


def utc_to_local(aware_utc: datetime, tz_name: str) -> datetime:
    """Convert stored UTC to a user's timezone for display."""
    if aware_utc.tzinfo is None:
        raise ValueError("stored datetimes must be timezone-aware UTC")
    return aware_utc.astimezone(ZoneInfo(tz_name))


def utc_to_jst(aware_utc: datetime) -> datetime:
    return utc_to_local(aware_utc, "Asia/Tokyo")


# Hand-built weekday/month names, same Windows-portability spirit as the
# hand-built day number below: no strftime locale games, no new dependency.
# Index by dt.weekday() (Mon=0). zh uses the bare 一...日 form ("周六" reads
# fine but the compact form matches ticketing-site convention).
_WEEKDAY = {
    "ja": ("月", "火", "水", "木", "金", "土", "日"),
    "zh": ("一", "二", "三", "四", "五", "六", "日"),
}


def _norm(locale: str) -> str:
    return locale if locale in _WEEKDAY else "en"


def fmt_dual(aware_utc: datetime, tz_name: str, locale: str = "en") -> str:
    """'Sat 2026-08-01 19:00 JST (07:00 ADT)' — the one-line display format.

    Still the right shape for Discord embeds and any plain-text context, where a
    two-line HTML render is not possible. The web uses fmt_dual_lines instead.
    locale selects the date pattern ("en"/"ja"/"zh"); an unrecognized locale
    falls back to "en". The time portion stays numeric in every locale.
    """
    jst = utc_to_jst(aware_utc)
    local = utc_to_local(aware_utc, tz_name)
    locale = _norm(locale)
    if locale == "en":
        return f"{jst:%a %Y-%m-%d %H:%M} JST ({local:%H:%M %Z})"
    wd = _WEEKDAY[locale][jst.weekday()]
    wrapped = f"({wd})" if locale == "ja" else f"（{wd}）"
    return f"{jst:%Y-%m-%d}{wrapped} {jst:%H:%M} JST ({local:%H:%M %Z})"


def fmt_dual_lines(aware_utc: datetime, tz_name: str, locale: str = "en") -> tuple[str, str]:
    """The two-line web display shape, as (date_line, time_line).

    Line one is a bold weekday+day+month ('Sat 1 Aug'); line two is the dual
    time 'HH:MM JST · HH:MM <user-zone>'. Same invariant-1 contract as fmt_dual:
    both zones ALWAYS present and JST first -- this is a presentation reshape of
    the same data, never a revert to terse relative text. locale selects the
    date_line pattern ("en"/"ja"/"zh", falling back to "en"); time_line stays
    numeric in every locale.
    """
    jst = utc_to_jst(aware_utc)
    local = utc_to_local(aware_utc, tz_name)
    locale = _norm(locale)
    # The day is built by hand rather than with a strftime directive: %-d (glibc)
    # and %#d (Windows) are both non-portable, and the owner runs Windows.
    if locale == "en":
        date_line = f"{jst:%a} {jst.day} {jst:%b}"
    else:
        wd = _WEEKDAY[locale][jst.weekday()]
        wrapped = f"({wd})" if locale == "ja" else f"（{wd}）"
        date_line = f"{jst.month}月{jst.day}日{wrapped}"
    time_line = f"{jst:%H:%M} JST · {local:%H:%M %Z}"
    return date_line, time_line


def fmt_day_month(aware_utc: datetime, locale: str = "en") -> str:
    """'12 Oct' -- day-month, no year, no weekday, no zone.

    For PERFORMANCE dates (a concert's start day), never for a deadline: a
    date is a fact about the world, not a moment you must act by, so it
    carries none of dual time's JST/local apparatus (invariant 1 governs
    deadlines, not this). Always JST, like every other date in the app. Same
    hand-built day as fmt_dual_lines, for the same Windows strftime reason.
    locale selects the pattern ("en"/"ja"/"zh", falling back to "en").
    """
    jst = utc_to_jst(aware_utc)
    if _norm(locale) == "en":
        return f"{jst.day} {jst:%b}"
    return f"{jst.month}月{jst.day}日"
