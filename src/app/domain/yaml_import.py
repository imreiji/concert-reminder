"""Parse a pasted YAML draft into a ParsedConcert.

The other half of domain/yaml_export.py: the same vocabulary, read back.
The producer is normally an agent following .claude/skills/add-concert, but
the parser trusts nothing -- it is exactly as tolerant as the ramen.events
parser (domain/ingest.py): warnings over failures, so a slightly-off draft
still renders a preview the editor can fix, instead of bouncing them back
to a blank form.

Hard failure (DraftError) only when there is nothing to render a preview
FROM: not YAML, not a mapping, no title, or the YAML itself is hostile
(nesting too deep for PyYAML's recursive-descent parser, which raises
RecursionError rather than YAMLError -- its own except branch, because
such a draft may be well-formed YAML and deserves to be told so).
Everything else degrades to a warning carried on the result and shown in
the preview's warning strip: unknown kinds fall back, malformed datetimes
blank the field, a container where text belongs blanks the field and says
which one, and unknown keys are ignored loudly (they usually mean the
skill and this parser have drifted apart -- the warning is the drift
alarm).

Pure function: no I/O. Datetimes are naive JST wall-clock (see
domain/draft.py). yaml.safe_load ONLY -- a draft is pasted text from
outside the trust boundary.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

import yaml

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import ConcertKind, RoundKind

_TOP_KEYS = {
    # "slug" is TOLERATED, not used: it predates event_id and meant
    # slugify(title). Exports stopped emitting it; older drafts still parse.
    "slug", "event_id", "title", "title_en", "title_zh", "kind", "organizer",
    "categories", "series", "series_handles", "venues", "performers",
    "eventernote_url", "official_url", "source_url", "performances", "rounds",
    "notes", "notes_en", "notes_zh",
}
_SERIES_KEYS = {"franchises", "groups", "characters", "artists"}
_DAY_KEYS = {
    "label", "label_en", "label_zh", "city", "venue", "venue_address", "venue_handle",
    "doors_jst", "starts_at_jst", "eventernote_event_id",
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


def _text(value, where: str | None = None, warnings: list[str] | None = None) -> str | None:
    """A scalar as trimmed text, or None. YAML may hand back numbers etc.

    A list/dict is never valid text for a scalar field -- str()'ing one can
    be exponentially expensive on an anchor/alias fan-out DAG (a tiny
    payload whose aliases share sub-structure), so containers are rejected
    without ever being stringified.

    It warns like _dt does when the caller passes `where`/`warnings`, so a
    list organizer or a mapping label leaves a drift alarm instead of a
    silent blank. The warning names the FIELD and the type ONLY -- putting
    the value in it would be that same str(), which is precisely the cost
    the guard exists to avoid, so never interpolate `value` here.
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        if where is not None and warnings is not None:
            warnings.append(
                f"{where}: expected text, got a {type(value).__name__} -- left blank"
            )
        return None
    text = str(value).strip()
    return text or None


def _names(value, where: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{where}: expected a list, got {type(value).__name__} -- ignored")
        return []
    return [t for i, v in enumerate(value, start=1) if (t := _text(v, f"{where} #{i}", warnings))]


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
    if isinstance(value, (list, dict)):
        warnings.append(
            f"{where}: expected 'YYYY-MM-DD HH:MM', got a {type(value).__name__} -- left blank"
        )
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
    text = _text(value, "kind", warnings)
    if text is None:
        return None
    try:
        return ConcertKind(text.lower())
    except ValueError:
        warnings.append(f"kind: {text!r} isn't a concert kind -- left unset")
        return None


def _round_kind(value, where: str, warnings: list[str]) -> RoundKind:
    text = _text(value, f"{where} kind", warnings)
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
    except RecursionError as exc:
        # Its own branch, and its own sentence. The text here may be perfectly
        # well-formed YAML -- it is just nested deeper than PyYAML's
        # recursive-descent parser can walk -- so "that doesn't parse as YAML"
        # was false, and CPython's own "maximum recursion depth exceeded" says
        # nothing a draft author can act on. This is what the old
        # `{exc or 'nesting too deep'}` fallback was reaching for; an exception
        # is always truthy, so that branch could never actually fire.
        raise DraftError(
            "that draft nests too deeply to read -- flatten the nested "
            "lists/mappings and paste it again"
        ) from exc
    except yaml.YAMLError as exc:
        raise DraftError(f"that doesn't parse as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftError("a draft is a YAML mapping (key: value lines) -- this isn't one")
    # Declared before the title parse so _text has somewhere to warn -- a
    # container title raises below anyway, but the list has to exist first.
    warnings: list[str] = []
    title = _text(data.get("title"), "title", warnings)
    if title is None:
        raise DraftError("the draft has no title -- 'title:' is the one required key")

    _warn_unknown(data, _TOP_KEYS, "draft", warnings)

    series = data.get("series") or {}
    if not isinstance(series, dict):
        warnings.append("series: expected a mapping -- ignored")
        series = {}
    _warn_unknown(series, _SERIES_KEYS, "series", warnings)

    # The handle block, written by an export and omitted by an agent. Where a
    # kind appears here it is AUTHORITATIVE -- see ParsedConcert -- so the
    # route resolves these first and ignores the matching name list.
    handles = data.get("series_handles") or {}
    if not isinstance(handles, dict):
        warnings.append("series_handles: expected a mapping -- ignored")
        handles = {}
    _warn_unknown(handles, _SERIES_KEYS, "series_handles", warnings)

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
            label=_text(raw.get("label"), f"{where} label", warnings) or f"Day {i}",
            starts_at_jst=_dt(raw.get("starts_at_jst"), f"{where} starts_at_jst", warnings),
            label_en=_text(raw.get("label_en"), f"{where} label_en", warnings),
            label_zh=_text(raw.get("label_zh"), f"{where} label_zh", warnings),
            doors_at_jst=_dt(raw.get("doors_jst"), f"{where} doors_jst", warnings),
            venue_name=_text(raw.get("venue"), f"{where} venue", warnings),
            venue_city=_text(raw.get("city"), f"{where} city", warnings),
            venue_address=_text(raw.get("venue_address"), f"{where} venue_address", warnings),
            venue_handle=_text(raw.get("venue_handle"), f"{where} venue_handle", warnings),
            # Through _text like every other scalar, so a YAML integer id
            # (464372 unquoted) arrives as the string the column stores, and a
            # list/dict is refused rather than stringified.
            eventernote_event_id=_text(
                raw.get("eventernote_event_id"), f"{where} eventernote_event_id", warnings
            ),
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
            label=_text(raw.get("label"), f"{where} label", warnings) or f"Round {i}",
            kind=_round_kind(raw.get("kind"), where, warnings),
            opens_at_jst=_dt(raw.get("apply_opens_jst"), f"{where} apply_opens_jst", warnings),
            closes_at_jst=_dt(raw.get("apply_closes_jst"), f"{where} apply_closes_jst", warnings),
            url=_text(raw.get("url"), f"{where} url", warnings),
            label_en=_text(raw.get("label_en"), f"{where} label_en", warnings),
            label_zh=_text(raw.get("label_zh"), f"{where} label_zh", warnings),
            results_at_jst=_dt(raw.get("results_jst"), f"{where} results_jst", warnings),
            payment_at_jst=_dt(
                raw.get("payment_deadline_jst"), f"{where} payment_deadline_jst", warnings
            ),
            notes=_text(raw.get("notes"), f"{where} notes", warnings),
            applies_to_labels=_names(raw.get("applies_to"), f"{where} applies_to", warnings),
        ))

    return ParsedConcert(
        title=title,
        venue_name=None,  # drafts carry venues per leg, never event-level
        days=days,
        rounds=rounds,
        # The same list object the _text calls below still append to -- kwargs
        # evaluate left to right, and ParsedConcert stores the reference rather
        # than a copy, so their warnings do land on the returned draft.
        #
        # BOTH halves are load-bearing, and the failure mode is silent. Swap
        # ParsedConcert for anything that COPIES this list on assignment --
        # pydantic, attrs with a converter, a plain dataclass that grows
        # `field(default_factory=...)` plus a `__post_init__` that rebuilds it,
        # even `warnings=list(warnings)` typed here for tidiness -- and the
        # roughly a dozen field warnings raised by the kwargs below this line
        # vanish from the returned draft. Nothing raises and no test fails on
        # the mechanism: a draft with a bad `title_en` simply previews with one
        # fewer warning than it earned. Keep the plain dataclass, or hoist
        # every `_text` call above the constructor before changing it.
        warnings=warnings,
        title_en=_text(data.get("title_en"), "title_en", warnings),
        title_zh=_text(data.get("title_zh"), "title_zh", warnings),
        notes=_text(data.get("notes"), "notes", warnings),
        notes_en=_text(data.get("notes_en"), "notes_en", warnings),
        notes_zh=_text(data.get("notes_zh"), "notes_zh", warnings),
        event_id=_text(data.get("event_id"), "event_id", warnings),
        organizer=_text(data.get("organizer"), "organizer", warnings),
        categories=_text(data.get("categories"), "categories", warnings),
        kind=_concert_kind(data.get("kind"), warnings),
        source_url=_text(data.get("source_url"), "source_url", warnings),
        official_url=_text(data.get("official_url"), "official_url", warnings),
        eventernote_url=_text(data.get("eventernote_url"), "eventernote_url", warnings),
        performers_text="\n".join(performers) or None,
        franchise_names=_names(series.get("franchises"), "series.franchises", warnings),
        group_names=_names(series.get("groups"), "series.groups", warnings),
        character_names=_names(series.get("characters"), "series.characters", warnings),
        artist_names=_names(series.get("artists"), "series.artists", warnings),
        franchise_handles=_names(
            handles.get("franchises"), "series_handles.franchises", warnings
        ),
        group_handles=_names(handles.get("groups"), "series_handles.groups", warnings),
        character_handles=_names(
            handles.get("characters"), "series_handles.characters", warnings
        ),
        artist_handles=_names(handles.get("artists"), "series_handles.artists", warnings),
    )


# -- Multi-document paste ---------------------------------------------------
#
# An agent researching discovery leads can hand back many drafts in one
# paste, `---`-separated the way a YAML stream already allows. `parse_draft`
# above stays untouched -- it only ever sees ONE document -- and everything
# below is a layer that splits a paste into documents and runs it per
# document, so one bad document costs nothing but itself.


@dataclass(frozen=True)
class ParsedDraft:
    """One document out of a multi-document paste, alongside its own parse.

    `text` is the document's exact source slice, kept verbatim (not
    re-serialized) so a later re-parse -- the preview page, after the batch
    has been stored -- reproduces `parsed` exactly, the same way pasting that
    one document alone would have. Storing only `parsed` would freeze
    today's parser against tomorrow's; storing only `text` would mean
    reparsing eagerly here for nothing.
    """

    text: str
    parsed: ParsedConcert


@dataclass(frozen=True)
class DraftBatch:
    """The result of parsing a multi-document paste.

    `drafts` holds every document that parsed; `errors` holds one message
    per document that raised `DraftError`, each prefixed with that
    document's 1-based position in the paste -- with fifty documents in a
    paste, "a draft failed" says nothing, "document 37" says everything.
    A document is never dropped silently: it lands in exactly one of the
    two tuples, except an empty document (pure formatting -- a trailing
    separator, a blank stanza), which `split_documents` already discards
    before either tuple is built.
    """

    drafts: tuple[ParsedDraft, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def split_documents(text: str) -> list[str]:
    """Split a `---`-separated paste into per-document source text.

    Deliberately NOT `text.split("---")`: a `---` inside a quoted scalar or
    a block scalar (`notes: |` / `notes: >`) is ordinary content, not a
    document boundary, and Japanese concert notes pasted as free text are
    exactly the kind of thing that contains one -- a naive split would cut
    that document in half.

    Instead this uses `yaml.scan` (PyYAML's lexer) to find real
    `DocumentStartToken`s and slices the ORIGINAL text between them, so each
    returned string is the exact source for one document -- including
    whichever leading `---` line it had, which `yaml.safe_load` accepts
    without complaint. Scanning, not `compose_all`/`safe_load_all`: both of
    those walk the WHOLE stream in one pass and raise on the first bad
    document, which would silently discard every document after it -- the
    opposite of what this function exists for. Scanning is lexical (it
    tracks indentation, quoting and flow context to find token boundaries,
    but never validates that the tokens form a well-formed document), so it
    keeps finding every later boundary even when an earlier document is
    broken enough that `parse_draft` will go on to reject it -- verified
    against PyYAML directly: an unclosed `[` mid-document does not stop the
    scanner from finding the `---` after it, only the later parse.
    Malformed enough to break even the SCANNER (not just the parser) is the
    one case this can't isolate; that failure is rare enough (scanning
    tolerated an unclosed flow sequence in testing) that the whole text is
    handed to `parse_draft` as one document, which reports the real error
    instead of this function raising one of its own.

    Empty documents -- a trailing separator, a stray blank `---\n---\n` --
    are dropped: they're formatting, not a document with nothing in it.
    """
    try:
        tokens = yaml.scan(text, Loader=yaml.SafeLoader)
        starts = [0] + [
            tok.start_mark.index for tok in tokens if isinstance(tok, yaml.DocumentStartToken)
        ]
    except yaml.YAMLError:
        # The rare document broken enough to defeat the scanner itself, not
        # just the parser -- see the docstring. One un-splittable chunk,
        # handed whole to parse_draft, beats raising here and losing the
        # entire paste.
        starts = [0]
    starts = sorted(set(starts))
    boundaries = starts + [len(text)]
    # boundaries[1:] is deliberately one element shorter -- this pairs each
    # start with the next (a sliding window), not two equal-length sequences.
    chunks = [text[a:b] for a, b in zip(boundaries, boundaries[1:], strict=False)]
    return [chunk for chunk in chunks if chunk.strip() and chunk.strip() != "---"]


def parse_drafts(text: str) -> DraftBatch:
    """Split a paste into documents and run `parse_draft` over each.

    One malformed document must not cost the others -- at fifty concerts a
    typo in document 2 cannot lose documents 1 and 3, so a `DraftError` is
    caught per document and turned into an entry in `errors` rather than
    propagating. An empty paste (or one that is nothing but separators) is
    reported as an error too, not as a quietly empty, "successful" batch --
    silence there would read as "nothing to import" instead of "you pasted
    the wrong thing".
    """
    documents = split_documents(text)
    if not documents:
        return DraftBatch(errors=("the paste is empty -- there's nothing to import",))

    drafts: list[ParsedDraft] = []
    errors: list[str] = []
    for i, document in enumerate(documents, start=1):
        try:
            parsed = parse_draft(document)
        except DraftError as exc:
            errors.append(f"document {i}: {exc}")
            continue
        drafts.append(ParsedDraft(text=document, parsed=parsed))
    return DraftBatch(drafts=tuple(drafts), errors=tuple(errors))
