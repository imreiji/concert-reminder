"""The tags vocabulary: serializer AND parser, deliberately in one module.

Splitting a format's two halves across two files is how the catalogue
round-trip hole opened -- an export looked complete until something had to read
it back, and only then did it turn out a tag had no identity to key on. Keeping
both halves here means a field cannot be added to one side without the other
staring at you.

Pure: no I/O, no ORM. The caller adapts ORM rows into `TagExport` and consumes
`ParsedTag`, exactly as `yaml_export`/`yaml_import` do for concerts.

`parse_tags` follows `parse_draft`'s philosophy: WARNINGS OVER FAILURES. One bad
row is skipped and named; only a file that cannot yield any tags at all raises.
A restore is run under stress, months later, and refusing the whole file over
one typo is the wrong trade.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import yaml

from app.domain.slugs import slug_core
from app.domain.types import TagKind

_TAG_KEYS = {
    "handle", "name", "kind", "name_en", "name_zh", "parent", "voiced_by",
    "members", "region", "city", "city_en", "city_zh", "address",
    "location_url", "eventernote_url",
}

# Written into every export. Its own constant so the serializer stays a pure
# function of its input and the text lives next to the format it describes.
RESTORE_NOTES = """\
dekimasen.app catalogue export

WHAT IS IN HERE
  tags.yaml            every tag, with its handle
  concerts/*.yaml      one draft per concert, named by its event_id
  RESTORE.txt          this file

NO PERSONAL DATA. This is built from the catalogue tables only -- it never
touches users, sessions, outcomes, subscriptions, reminders or delivery logs --
and it does not record who created anything.

HOW TO PUT IT BACK, IN THIS ORDER
  1. Paste tags.yaml into /admin/import/tags. Tags that already exist are
     skipped, never updated, so this is safe to run twice.
  2. Paste each concerts/*.yaml into /concerts/import (the "paste a draft" box)
     and commit the preview.

ORDER MATTERS. Tags first: a concert draft refers to its tags by handle, and a
handle that does not exist yet cannot be bound.

A concert that still exists will answer 409 rather than be duplicated. That is
deliberate -- it is how re-importing a file you already restored announces
itself.
"""


class TagsFileError(Exception):
    """The text cannot yield any tags at all."""


@dataclass(frozen=True)
class TagExport:
    handle: str
    name: str
    kind: str
    name_en: str | None = None
    name_zh: str | None = None
    parent: str | None = None
    # CHARACTER-specific: the HANDLE of the ARTIST who voices her. A handle, not
    # an id and not a name -- ids are meaningless across a restore and names are
    # not unique. The adapter on either side converts (service.current_tag_exports
    # id -> handle, service.apply_tag_import handle -> id).
    voiced_by: str | None = None
    members: tuple[str, ...] = ()
    region: str | None = None
    city: str | None = None
    city_en: str | None = None
    city_zh: str | None = None
    address: str | None = None
    location_url: str | None = None
    eventernote_url: str | None = None


@dataclass
class ParsedTag:
    handle: str
    name: str
    kind: TagKind
    name_en: str | None = None
    name_zh: str | None = None
    parent: str | None = None
    voiced_by: str | None = None
    members: list[str] = field(default_factory=list)
    region: str | None = None
    city: str | None = None
    city_en: str | None = None
    city_zh: str | None = None
    address: str | None = None
    location_url: str | None = None
    eventernote_url: str | None = None


@dataclass
class ParsedTagFile:
    tags: list[ParsedTag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def tags_to_yaml(tags: Sequence[TagExport]) -> str:
    """Serialize for a restore file: readable, and byte-stable.

    Sorted by (kind, handle) rather than caller order, so two exports of an
    unchanged catalogue are identical and the file diffs cleanly -- which is
    most of its value as a backup. Empty fields are OMITTED rather than emitted
    as `null`, because a person reads this and `city: null` on every artist is
    noise.
    """
    rows = []
    for tag in sorted(tags, key=lambda t: (t.kind, t.handle)):
        row: dict[str, object] = {"handle": tag.handle, "name": tag.name, "kind": tag.kind}
        for key in ("name_en", "name_zh", "parent", "voiced_by", "region", "city",
                    "city_en", "city_zh", "address", "location_url",
                    "eventernote_url"):
            value = getattr(tag, key)
            if value:
                row[key] = value
        if tag.members:
            row["members"] = list(tag.members)
        rows.append(row)
    return yaml.dump(
        {"tags": rows}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def _text(value, where: str, warnings: list[str]) -> str | None:
    """A string, or None with a warning. NEVER str() a list or dict.

    Same guard as `yaml_import._text`: stringifying a nested structure turns a
    YAML alias bomb into a plausible-looking value instead of a rejected one.
    """
    if value is None:
        return None
    if isinstance(value, list | dict):
        warnings.append(f"{where}: expected text, got {type(value).__name__} -- ignored")
        return None
    text = str(value).strip()
    return text or None


def _handles(value, where: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{where}: expected a list, got {type(value).__name__} -- ignored")
        return []
    out = []
    for i, item in enumerate(value, start=1):
        raw = _text(item, f"{where} #{i}", warnings)
        if raw is None:
            continue
        normalised = slug_core(raw)
        if not normalised:
            warnings.append(f"{where} #{i}: {raw!r} is not a usable handle -- dropped")
            continue
        out.append(normalised)
    return out


def parse_tags(text: str) -> ParsedTagFile:
    """A pasted tags file -> the tags it describes, plus what was wrong with it."""
    try:
        data = yaml.safe_load(text)
    except RecursionError as exc:
        raise TagsFileError(
            "that file nests too deeply to read -- flatten it and try again"
        ) from exc
    except yaml.YAMLError as exc:
        raise TagsFileError(f"that does not parse as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TagsFileError("a tags file is a mapping with a `tags:` list at the top")
    rows = data.get("tags")
    if not isinstance(rows, list):
        raise TagsFileError("no `tags:` list found -- is this a tags export?")

    out = ParsedTagFile()
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        where = f"tag #{i}"
        if not isinstance(row, dict):
            out.warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        unknown = sorted(set(row) - _TAG_KEYS)
        if unknown:
            out.warnings.append(f"{where}: unknown key(s) {', '.join(unknown)} -- ignored")

        raw_handle = _text(row.get("handle"), f"{where} handle", out.warnings)
        handle = slug_core(raw_handle) if raw_handle else ""
        if not handle:
            out.warnings.append(
                f"{where}: no usable handle -- skipped, it cannot be identified"
            )
            continue
        if raw_handle != handle:
            out.warnings.append(f"{where}: handle {raw_handle!r} normalised to {handle!r}")
        if handle in seen:
            out.warnings.append(f"{where}: duplicate handle {handle!r} -- the first one wins")
            continue

        name = _text(row.get("name"), f"{where} name", out.warnings)
        if not name:
            out.warnings.append(f"{where} ({handle}): no name -- skipped, a tag cannot render")
            continue
        raw_kind = _text(row.get("kind"), f"{where} kind", out.warnings)
        try:
            kind = TagKind(raw_kind)
        except ValueError:
            out.warnings.append(f"{where} ({handle}): unknown kind {raw_kind!r} -- skipped")
            continue

        seen.add(handle)
        parent = _text(row.get("parent"), f"{where} parent", out.warnings)
        # A HANDLE like `parent`, so it takes the same normalisation: a person
        # hand-editing this file will type the seiyuu's name, and storing that
        # verbatim would resolve against nothing on the way back in.
        voiced_by = _text(row.get("voiced_by"), f"{where} voiced_by", out.warnings)
        out.tags.append(ParsedTag(
            handle=handle,
            name=name,
            kind=kind,
            name_en=_text(row.get("name_en"), f"{where} name_en", out.warnings),
            name_zh=_text(row.get("name_zh"), f"{where} name_zh", out.warnings),
            parent=slug_core(parent) if parent else None,
            voiced_by=slug_core(voiced_by) if voiced_by else None,
            members=_handles(row.get("members"), f"{where} members", out.warnings),
            region=_text(row.get("region"), f"{where} region", out.warnings),
            city=_text(row.get("city"), f"{where} city", out.warnings),
            city_en=_text(row.get("city_en"), f"{where} city_en", out.warnings),
            city_zh=_text(row.get("city_zh"), f"{where} city_zh", out.warnings),
            address=_text(row.get("address"), f"{where} address", out.warnings),
            location_url=_text(row.get("location_url"), f"{where} location_url", out.warnings),
            eventernote_url=_text(
                row.get("eventernote_url"), f"{where} eventernote_url", out.warnings
            ),
        ))
    return out
