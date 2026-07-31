"""What an import WOULD do, computed before anything is written.

Pure: no I/O, no ORM. Takes the parsed file and a snapshot of the current
catalogue, returns a plan the route can render and the service can apply.

Its own module rather than joining `tags_yaml.py`: that one is about the FORMAT
(serialize and parse), this is about COMPARISON. A module with three jobs is how
a file starts growing unwieldy.

The rule, per field: a blank on the DB side is a FILL (writing into emptiness
cannot lose anything, so it needs no decision); a blank in the file changes
nothing; equal values change nothing; and two different values are a CONFLICT
somebody has to resolve.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.tags_yaml import ParsedTag, TagExport

# The eleven fields that participate. `kind` is compared but never offered (see
# plan_tag_import), and `handle` is the match key so it cannot differ. A field
# missing from this tuple would silently never fill and never conflict -- it
# would just be ignored -- which is why a test pins both the count and the set.
COMPARABLE_FIELDS = (
    "name",
    "name_en",
    "name_zh",
    "parent",
    "region",
    "city",
    "city_en",
    "city_zh",
    "address",
    "location_url",
    "eventernote_url",
)


def _blank(value: str | None) -> bool:
    """NULL or whitespace-only.

    Every writer normalises "" -> None today, but a differ that depends on that
    holding forever is a differ waiting to break.
    """
    return value is None or not str(value).strip()


@dataclass(frozen=True)
class FieldConflict:
    field: str
    current: str
    incoming: str


@dataclass
class TagPlan:
    handle: str
    incoming: ParsedTag
    is_new: bool = False
    kind_mismatch: bool = False
    fills: dict[str, str] = field(default_factory=dict)
    conflicts: list[FieldConflict] = field(default_factory=list)
    member_additions: list[str] = field(default_factory=list)
    member_removals: list[str] = field(default_factory=list)

    @property
    def needs_choice(self) -> bool:
        """Does a person have to decide something about this tag?"""
        return bool(self.conflicts or self.member_additions or self.member_removals)


@dataclass
class ImportPlan:
    tags: list[TagPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def created(self) -> list[TagPlan]:
        return [t for t in self.tags if t.is_new]

    @property
    def changed(self) -> list[TagPlan]:
        """Existing tags with something to apply automatically (fills only)."""
        return [t for t in self.tags if not t.is_new and t.fills and not t.kind_mismatch]

    @property
    def conflicted(self) -> list[TagPlan]:
        return [t for t in self.tags if t.needs_choice]


def plan_tag_import(
    incoming: Sequence[ParsedTag],
    current: Sequence[TagExport],
    warnings: Sequence[str] = (),
) -> ImportPlan:
    """Compare the file against the catalogue. Writes nothing, decides nothing.

    A catalogue tag the file does not mention is UNTOUCHED and unmentioned --
    nothing is ever deleted by an import.
    """
    plan = ImportPlan(warnings=list(warnings))
    by_handle = {t.handle: t for t in current}

    for tag in incoming:
        existing = by_handle.get(tag.handle)
        if existing is None:
            plan.tags.append(TagPlan(handle=tag.handle, incoming=tag, is_new=True))
            continue

        # kind is compared but NOT choosable: a venue arriving as an artist could
        # orphan a leg whose venue_tag_id points at it. Warn loudly and touch
        # nothing -- fills included.
        if existing.kind != tag.kind.value:
            plan.tags.append(TagPlan(handle=tag.handle, incoming=tag, kind_mismatch=True))
            # Kinds are QUOTED rather than given articles: "a artist" is the
            # bug you get from f-stringing an article, and the operator needs
            # the exact values anyway.
            plan.warnings.append(
                f"{tag.handle}: kind disagrees -- the file says {tag.kind.value!r}, "
                f"the catalogue says {existing.kind!r}. Skipped entirely; nothing "
                f"about this tag was touched."
            )
            continue

        entry = TagPlan(handle=tag.handle, incoming=tag)
        for name in COMPARABLE_FIELDS:
            mine = getattr(existing, name)
            theirs = getattr(tag, name)
            if _blank(theirs):
                continue
            if _blank(mine):
                entry.fills[name] = theirs
            elif str(mine).strip() != str(theirs).strip():
                entry.conflicts.append(
                    FieldConflict(field=name, current=str(mine), incoming=str(theirs))
                )

        mine_members = set(existing.members)
        their_members = set(tag.members)
        entry.member_additions = sorted(their_members - mine_members)
        entry.member_removals = sorted(mine_members - their_members)
        plan.tags.append(entry)

    return plan
