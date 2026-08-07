"""The edit pages' "what's missing" notice: which locale variants are unfilled.

Pure enough to have no core dependency at all -- it reads rows and asks
`domain/translations.py` what is absent.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.db.models import (
    Concert,
    ConcertDay,
    Round,
    Tag,
)
from app.domain.translations import SLOT_LABEL, missing_variants
from app.domain.types import (
    TagKind,
)
from app.i18n import gettext as _

# ── Translation gaps (the edit pages' "what's missing" notice) ────────────


@dataclass(frozen=True)
class VariantGap:
    """One language, and the fields on this record still missing it.

    Grouped by LANGUAGE rather than by field because that is the shape of
    the work: an editor who can write 中文 wants one list of everything
    waiting for them, not the same language repeated down a column.
    """

    language: str  # "日本語" / "English" / "中文"
    fields: tuple[str, ...]  # localized field labels, in record order


def _regroup_gaps(pairs: Iterable[tuple[str, tuple[str, ...]]]) -> list[VariantGap]:
    """(field label, missing slots) pairs -> one VariantGap per language.

    Slot order stays ja/en/zh (missing_variants' own order) and field order
    stays the caller's, which is the order the fields appear on the page --
    so the notice reads top-to-bottom like the form under it.
    """
    by_slot: dict[str, list[str]] = {}
    for label, missing in pairs:
        for slot in missing:
            by_slot.setdefault(slot, []).append(label)
    return [
        VariantGap(language=SLOT_LABEL[slot], fields=tuple(by_slot[slot]))
        for slot in ("ja", "en", "zh")
        if slot in by_slot
    ]


def concert_variant_gaps(
    concert: Concert, days: Sequence[ConcertDay], rounds: Sequence[Round]
) -> list[VariantGap]:
    """What this concert is still missing, in the viewer's language.

    Read-only and advisory: `edit_concert` never blocks on any of this (see
    tests/test_variant_enforcement.py's asymmetry section). The phase ships
    without a backfill, so nearly every pre-i18n record has a Japanese title
    and nothing else -- enforcing here would wall the owner out of their own
    catalogue. Naming the gap while they are looking at the record is the
    whole intervention.

    Same field set the create boundary enforces, and the same row wording
    ("Leg 2 label"), so the notice and a 422 never disagree. `days`/`rounds`
    are passed in rather than read off `concert` so no relationship is
    lazy-loaded mid-render, and they are numbered in the order the edit page
    renders them.

    There is no venue field here: a leg's venue is a VENUE tag, whose own
    name variants are governed on the Tags page, not per concert. (The old
    Concert.venue/_en/_zh columns were dropped once every venue lived on a
    tag.)
    """
    pairs: list[tuple[str, tuple[str, ...]]] = [
        (_("Title"), missing_variants(
            concert.title or "", concert.title_en or "", concert.title_zh or "",
            mandatory=True,
        )),
        (_("Notes"), missing_variants(
            concert.notes or "", concert.notes_en or "", concert.notes_zh or "",
        )),
    ]
    for n, d in enumerate(days, 1):
        pairs.append((
            _("Leg {n} label").format(n=n),
            missing_variants(d.label or "", d.label_en or "", d.label_zh or ""),
        ))
    for n, r in enumerate(rounds, 1):
        pairs.append((
            _("Round {n} label").format(n=n),
            missing_variants(r.label or "", r.label_en or "", r.label_zh or ""),
        ))
    return _regroup_gaps(pairs)


def tag_variant_gaps(tag: Tag) -> list[VariantGap]:
    """The same notice for a tag. `city` only applies to a VENUE."""
    pairs: list[tuple[str, tuple[str, ...]]] = [
        (_("Name"), missing_variants(
            tag.name or "", tag.name_en or "", tag.name_zh or "", mandatory=True,
        )),
    ]
    if tag.kind is TagKind.VENUE:
        pairs.append((_("City"), missing_variants(
            tag.city or "", tag.city_en or "", tag.city_zh or "",
        )))
    return _regroup_gaps(pairs)
