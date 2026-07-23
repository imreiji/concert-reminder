"""Parse a pasted YAML draft into a ParsedConcert.

The other half of domain/yaml_export.py: the same vocabulary, read back.
The producer is normally an agent following .claude/skills/add-concert, but
the parser trusts nothing -- it is exactly as tolerant as the ramen.events
parser (domain/ingest.py): warnings over failures, so a slightly-off draft
still renders a preview the editor can fix, instead of bouncing them back
to a blank form.

Hard failure (DraftError) only when there is nothing to render a preview
FROM: not YAML, not a mapping, or no title. Everything else degrades to a
warning carried on the result and shown in the preview's warning strip:
unknown kinds fall back, malformed datetimes blank the field, unknown keys
are ignored loudly (they usually mean the skill and this parser have
drifted apart -- the warning is the drift alarm).

Pure function: no I/O. Datetimes are naive JST wall-clock (see
domain/draft.py). yaml.safe_load ONLY -- a draft is pasted text from
outside the trust boundary.
"""

from datetime import date, datetime

import yaml

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import ConcertKind, RoundKind

_TOP_KEYS = {
    "slug", "title", "title_en", "title_zh", "kind", "organizer",
    "categories", "series", "venues", "performers", "eventernote_url",
    "official_url", "source_url", "performances", "rounds", "notes",
    "notes_en", "notes_zh",
}
_SERIES_KEYS = {"franchises", "groups", "artists"}
_DAY_KEYS = {
    "label", "label_en", "label_zh", "city", "venue", "venue_address",
    "doors_jst", "starts_at_jst",
}
_ROUND_KEYS = {
    "label", "label_en", "label_zh", "kind", "applies_to",
    "apply_opens_jst", "apply_closes_jst", "results_jst",
    "payment_deadline_jst", "url", "notes",
}


class DraftError(Exception):
    """The pasted text can't produce a preview at all."""


def _warn_unknown(mapping: dict, known: set[str], where: str, warnings: list[str]) -> None:
    for key in mapping:
        if key not in known:
            warnings.append(
                f"{where}: unknown key {key!r} ignored -- the draft and the app "
                "may be on different schema versions"
            )


def _text(value) -> str | None:
    """A scalar as trimmed text, or None. YAML may hand back numbers etc."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _names(value, where: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{where}: expected a list, got {type(value).__name__} -- ignored")
        return []
    return [t for v in value if (t := _text(v))]


def _dt(value, where: str, warnings: list[str]) -> datetime | None:
    """Naive JST from 'YYYY-MM-DD HH:MM' (the yaml_export/web-form format).

    YAML itself may resolve a value with seconds to a datetime, or a bare
    date to a date -- accept the former, warn on the latter (a deadline
    without a time of day is not usable data, and JST midnight would be a
    silently wrong guess).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            warnings.append(f"{where}: timezone-aware datetime -- treated as JST wall-clock")
            return value.replace(tzinfo=None)
        return value
    if isinstance(value, date):
        warnings.append(f"{where}: date without a time -- left blank, fill it in the form")
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    warnings.append(f"{where}: couldn't read {text!r} as 'YYYY-MM-DD HH:MM' -- left blank")
    return None


def _concert_kind(value, warnings: list[str]) -> ConcertKind | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return ConcertKind(text.lower())
    except ValueError:
        warnings.append(f"kind: {text!r} isn't a concert kind -- left unset")
        return None


def _round_kind(value, where: str, warnings: list[str]) -> RoundKind:
    text = _text(value)
    if text is None:
        warnings.append(f"{where}: no kind -- defaulted to 'other'")
        return RoundKind.OTHER
    try:
        return RoundKind(text.lower())
    except ValueError:
        warnings.append(f"{where}: unknown kind {text!r} -- defaulted to 'other'")
        return RoundKind.OTHER


def parse_draft(text: str) -> ParsedConcert:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DraftError(f"that doesn't parse as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftError("a draft is a YAML mapping (key: value lines) -- this isn't one")
    title = _text(data.get("title"))
    if title is None:
        raise DraftError("the draft has no title -- 'title:' is the one required key")

    warnings: list[str] = []
    _warn_unknown(data, _TOP_KEYS, "draft", warnings)

    series = data.get("series") or {}
    if not isinstance(series, dict):
        warnings.append("series: expected a mapping -- ignored")
        series = {}
    _warn_unknown(series, _SERIES_KEYS, "series", warnings)

    performers = _names(data.get("performers"), "performers", warnings)

    days: list[ParsedDay] = []
    raw_days = data.get("performances") or []
    if not isinstance(raw_days, list):
        warnings.append("performances: expected a list -- ignored")
        raw_days = []
    for i, raw in enumerate(raw_days, start=1):
        where = f"performance {i}"
        if not isinstance(raw, dict):
            warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        _warn_unknown(raw, _DAY_KEYS, where, warnings)
        days.append(ParsedDay(
            label=_text(raw.get("label")) or f"Day {i}",
            starts_at_jst=_dt(raw.get("starts_at_jst"), f"{where} starts_at_jst", warnings),
            label_en=_text(raw.get("label_en")),
            label_zh=_text(raw.get("label_zh")),
            doors_at_jst=_dt(raw.get("doors_jst"), f"{where} doors_jst", warnings),
            venue_name=_text(raw.get("venue")),
            venue_city=_text(raw.get("city")),
            venue_address=_text(raw.get("venue_address")),
        ))

    rounds: list[ParsedRound] = []
    raw_rounds = data.get("rounds") or []
    if not isinstance(raw_rounds, list):
        warnings.append("rounds: expected a list -- ignored")
        raw_rounds = []
    for i, raw in enumerate(raw_rounds, start=1):
        where = f"round {i}"
        if not isinstance(raw, dict):
            warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        _warn_unknown(raw, _ROUND_KEYS, where, warnings)
        rounds.append(ParsedRound(
            label=_text(raw.get("label")) or f"Round {i}",
            kind=_round_kind(raw.get("kind"), where, warnings),
            opens_at_jst=_dt(raw.get("apply_opens_jst"), f"{where} apply_opens_jst", warnings),
            closes_at_jst=_dt(raw.get("apply_closes_jst"), f"{where} apply_closes_jst", warnings),
            url=_text(raw.get("url")),
            label_en=_text(raw.get("label_en")),
            label_zh=_text(raw.get("label_zh")),
            results_at_jst=_dt(raw.get("results_jst"), f"{where} results_jst", warnings),
            payment_at_jst=_dt(
                raw.get("payment_deadline_jst"), f"{where} payment_deadline_jst", warnings
            ),
            notes=_text(raw.get("notes")),
            applies_to_labels=_names(raw.get("applies_to"), f"{where} applies_to", warnings),
        ))

    return ParsedConcert(
        title=title,
        venue_name=None,  # drafts carry venues per leg, never event-level
        days=days,
        rounds=rounds,
        warnings=warnings,
        title_en=_text(data.get("title_en")),
        title_zh=_text(data.get("title_zh")),
        notes=_text(data.get("notes")),
        notes_en=_text(data.get("notes_en")),
        notes_zh=_text(data.get("notes_zh")),
        organizer=_text(data.get("organizer")),
        categories=_text(data.get("categories")),
        kind=_concert_kind(data.get("kind"), warnings),
        source_url=_text(data.get("source_url")),
        official_url=_text(data.get("official_url")),
        eventernote_url=_text(data.get("eventernote_url")),
        performers_text="\n".join(performers) or None,
        franchise_names=_names(series.get("franchises"), "series.franchises", warnings),
        group_names=_names(series.get("groups"), "series.groups", warnings),
        artist_names=_names(series.get("artists"), "series.artists", warnings),
    )
