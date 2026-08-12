"""Tag catalogue: the vocabulary Home, Discover and the concert page organise by.

The biggest feature extracted from `service.py`, and it earns its own module
for the reason `domain/tags_yaml.py` and `domain/tags_diff.py` are two: this is
tag STORAGE and mutation, those are the file format and the comparison. Slug
minting (`assign_tag_slug`, `create_tag_row`) lives here because invariant 3
names it as the single path a Tag row may be constructed through.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.core import _now
from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    Tag,
    TagMember,
    TagSubscription,
)
from app.domain.slugs import tag_slug_base
from app.domain.tags_diff import ImportPlan, TagPlan
from app.domain.tags_yaml import RESTORE_NOTES, TagExport, tags_to_yaml
from app.domain.translations import missing_variants
from app.domain.types import (
    ALLOWED_PARENT_KINDS,
    TagKind,
)
from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml

# ── Tags ─────────────────────────────────────────────────────────────────


async def assign_tag_slug(session: AsyncSession, tag: Tag) -> str:
    """Give `tag` a unique handle. Call after `session.add(tag)`, before commit.

    The handle is a tag's identity -- names are not unique (owner ruling
    2026-07-29) -- so this is the single place one is minted, and every create
    path goes through it.

    When the name yields no ASCII at all (a Japanese-only tag) the base is the
    KIND, so the de-duplication below numbers it: `artist`, `artist-2`. The spec
    called for `{kind}-{id}`, and that is not buildable -- the id needs a flush,
    a flush needs a non-null slug, and `slug` is NOT NULL, so it would take a
    throwaway placeholder written purely to be overwritten. The row id bought
    nothing anyway: a handle only has to be unique and improvable, and it is
    stable from the moment it is assigned either way.

    De-duplication reads the DB AND the pending session, because a caller may
    add several tags before committing (the catalogue import will) and two
    pending rows must not agree on a handle -- the unique constraint would only
    catch that at flush time, by which point the useful context is gone.

    `no_autoflush` is load-bearing: `tag` is already in `session.new` with a
    null slug, and letting the SELECT autoflush it would hit the NOT NULL
    constraint before this function ever gets to fill the column in.
    """
    base = tag_slug_base(tag.name, tag.name_en) or tag.kind.value
    with session.no_autoflush:
        taken = {
            slug for (slug,) in await session.execute(
                select(Tag.slug).where(Tag.slug.is_not(None))
            )
        }
    taken |= {t.slug for t in session.new if isinstance(t, Tag) and t.slug}
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    tag.slug = candidate
    return candidate


async def create_tag_row(
    session: AsyncSession,
    *,
    name: str,
    kind: TagKind,
    slug: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    parent_id: int | None = None,
    voiced_by_tag_id: int | None = None,
    region: str | None = None,
    city: str | None = None,
    city_en: str | None = None,
    city_zh: str | None = None,
    address: str | None = None,
    location_url: str | None = None,
    eventernote_url: str | None = None,
    created_by: int | None = None,
) -> Tag:
    """Build and add a Tag. The ONE place a tag row is constructed.

    `slug=None` means MINT one (`assign_tag_slug` de-duplicates); a value means
    the caller already owns the handle and it is used verbatim. That distinction
    is the whole reason this exists: the three editor routes generate a handle,
    while the catalogue import carries handles in the file and must not have them
    silently renamed. A caller passing a slug is responsible for having
    normalised it -- `domain.tags_yaml.parse_tags` does.

    Does NOT commit, and does NOT notify: creating a tag is not attaching one
    (invariant 4), which is why `quick_create_tag` is silent too.
    """
    tag = Tag(
        name=name.strip(),
        kind=kind,
        slug=slug,
        name_en=name_en,
        name_zh=name_zh,
        parent_id=parent_id,
        # CHARACTER-only, and the caller owns the check that it names an
        # ARTIST -- this constructor validates nothing (the catalogue importer
        # warns-and-skips where the editor route 422s, and both would lose
        # their voice if the rule moved down here).
        voiced_by_tag_id=voiced_by_tag_id,
        region=region,
        city=city,
        city_en=city_en,
        city_zh=city_zh,
        address=address,
        location_url=location_url,
        eventernote_url=eventernote_url,
        created_by=created_by,
    )
    session.add(tag)
    if slug is None:
        await assign_tag_slug(session, tag)
    return tag


async def would_create_tag_cycle(
    session: AsyncSession, tag_id: int, parent_id: int
) -> bool:
    """Would parenting `tag_id` to `parent_id` close a loop?

    GROUP -> GROUP made loops possible for the first time, and nothing in this
    codebase walks parent_id transitively -- so a cycle would not be noticed
    until something did, and then it would hang rather than fail. The guard
    belongs at the write boundary, which is the only place a loop can be
    created.

    The `seen` set is not belt-and-braces: it terminates the walk on data that
    is ALREADY looped (written before this guard existed, or by a direct DB
    edit), where following parents alone would spin forever.
    """
    if tag_id == parent_id:
        return True
    seen: set[int] = {tag_id}
    cursor: int | None = parent_id
    while cursor is not None:
        if cursor in seen:
            # Reaching tag_id means the proposed parent is BELOW us, so the
            # edge would close a loop. Reaching any other repeat means the
            # table already contains a loop that does not involve us -- not a
            # new cycle, but the reason the walk must stop rather than spin.
            return cursor == tag_id
        seen.add(cursor)
        cursor = await session.scalar(select(Tag.parent_id).where(Tag.id == cursor))
    return False


@dataclass
class TagImportReport:
    """What an import did, for the result page. HANDLES, not ids: the operator
    reads this next to the file they pasted."""

    created: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    # REFUSED, not merely untouched: a kind mismatch. Kept distinct from
    # `unchanged` because "nothing to do" and "I would not touch this" read the
    # same in a count and mean very different things to whoever pasted the file.
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)



async def concert_export_yaml(session: AsyncSession, concert: Concert) -> str:
    """One concert as a draft-vocabulary YAML document.

    Shared by GET /concerts/{event_id}/export.yaml and the admin catalogue
    zip, which must not drift: a restore file that differed from the one an
    editor downloads would be a second format nobody agreed to.

    Loads the legs with their venue_tag eagerly. ConcertDay.venue_tag is
    lazy="raise", so a missed selectinload here is a MissingGreenlet 500
    rather than a slow export. Emits the tags' CANONICAL columns, never
    loc() -- an export is data, and its contents must not change with
    whoever happened to download it.
    """
    await session.refresh(concert, ["days", "rounds", "tags"])
    # The legs, with their venue tags: the export's city/venue/venue_address
    # come off the tag when the leg has one, and ConcertDay.venue_tag is
    # lazy="raise", so the eager load below is load-bearing -- without it every
    # export is a MissingGreenlet 500. A leg with NO venue tag exports no venue.
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .options(selectinload(ConcertDay.venue_tag))
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    days_by_id = {d.id: d.label for d in days}
    # The tag's CANONICAL columns, never loc(): an export is data, and its
    # contents must not change with whoever happened to download it.
    yaml_days = [
        YamlDay(
            label=d.label, label_en=d.label_en, label_zh=d.label_zh,
            starts_at_utc=d.starts_at_utc,
            city=d.venue_tag.city if d.venue_tag else None,
            venue=d.venue_tag.name if d.venue_tag else None,
            venue_address=d.venue_tag.address if d.venue_tag else None,
            venue_handle=d.venue_tag.slug if d.venue_tag else None,
            doors_at_utc=d.doors_at_utc,
            # Provenance, carried so an export -> re-import round trip keeps the
            # exact-match the discovery diff depends on. A leg that predates
            # discovery has none and the key is simply not written.
            eventernote_event_id=d.eventernote_event_id,
        )
        for d in days
    ]
    # ja label of every round on this concert, keyed by id -- so the export
    # can name a requires-link by LABEL (a restore has no ids to reuse) the
    # same way applies_to already names a leg by label.
    round_labels_by_id = {r.id: r.label for r in concert.rounds}
    yaml_rounds = [
        YamlRound(
            label=r.label, label_en=r.label_en, label_zh=r.label_zh, kind=r.kind.value,
            applies_to_labels=[days_by_id[d] for d in (r.applies_to or []) if d in days_by_id],
            opens_at_utc=r.opens_at_utc, closes_at_utc=r.closes_at_utc,
            results_at_utc=r.results_at_utc, payment_deadline_at_utc=r.payment_deadline_at_utc,
            url=r.url, notes=r.notes,
            requires_label=(
                round_labels_by_id.get(r.required_item_round_id)
                if r.required_item_round_id else None
            ),
        )
        for r in concert.rounds
    ]

    return concert_to_yaml(
        # So a re-import lands on this exact URL rather than minting a new one.
        event_id=concert.event_id,
        title=concert.title,
        kind=concert.kind.value if concert.kind else None,
        franchises=[t.name for t in concert.tags if t.kind is TagKind.FRANCHISE],
        groups=[t.name for t in concert.tags if t.kind is TagKind.GROUP],
        # CHARACTERS, without which `export.zip` is not a faithful backup of an
        # im@s concert: on a restore the derived seiyuu survives (she is an
        # ARTIST row) but the character is lost, and with her the reason the
        # concert reads the way it does. import_commit has accepted
        # `character_tags` since the kind shipped; only the file could not
        # express one.
        characters=[t.name for t in concert.tags if t.kind is TagKind.CHARACTER],
        artists=[t.name for t in concert.tags if t.kind is TagKind.ARTIST],
        venues=[t.name for t in concert.tags if t.kind is TagKind.VENUE],
        series_handles={
            "franchises": [t.slug for t in concert.tags if t.kind is TagKind.FRANCHISE],
            "groups": [t.slug for t in concert.tags if t.kind is TagKind.GROUP],
            "characters": [t.slug for t in concert.tags if t.kind is TagKind.CHARACTER],
            "artists": [t.slug for t in concert.tags if t.kind is TagKind.ARTIST],
        },
        days=yaml_days, rounds=yaml_rounds, notes=concert.notes,
        title_en=concert.title_en, title_zh=concert.title_zh,
        organizer=concert.organizer, categories=concert.categories,
        notes_en=concert.notes_en, notes_zh=concert.notes_zh,
        eventernote_url=concert.eventernote_url, official_url=concert.official_url,
        source_url=concert.source_url,
        performers=(
            [line.strip() for line in concert.performers_text.splitlines() if line.strip()]
            if concert.performers_text else []
        ),
    )



async def current_tag_exports(session: AsyncSession) -> list[TagExport]:
    """The whole catalogue as TagExport rows, ordered kind then handle.

    ONE builder, shared by the zip export and the import differ. Two would
    drift, and a differ comparing against a slightly different snapshot than the
    export wrote is the sort of bug that only surfaces months later, in a
    restore, when it is least welcome.
    """
    tags = list((await session.execute(
        select(Tag).options(selectinload(Tag.members)).order_by(Tag.kind, Tag.slug)
    )).scalars())
    by_id = {t.id: t for t in tags}
    return [
        TagExport(
            handle=t.slug, name=t.name, kind=t.kind.value,
            name_en=t.name_en, name_zh=t.name_zh,
            parent=by_id[t.parent_id].slug if t.parent_id in by_id else None,
            # The seiyuu's HANDLE, never her id: an id means nothing across a
            # restore into an empty database. `apply_tag_import` runs the same
            # conversion in reverse, in its second pass.
            voiced_by=(
                by_id[t.voiced_by_tag_id].slug if t.voiced_by_tag_id in by_id else None
            ),
            members=tuple(sorted(m.slug for m in t.members)),
            region=t.region, city=t.city, city_en=t.city_en, city_zh=t.city_zh,
            address=t.address, location_url=t.location_url,
            eventernote_url=t.eventernote_url,
        )
        for t in tags
    ]


async def api_tag_rows(
    session: AsyncSession,
    *,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """The tag vocabulary as JSON rows, plus the pre-paging total.

    Built from `current_tag_exports`, the ONE builder of the catalogue
    snapshot, so the API, the zip export and the differ all describe the same
    thing. Ordering comes from there too -- (kind, slug) -- and slug is
    UNIQUE, so the sort is already totally ordered and safe to page: no
    tiebreaker is needed on top of it (contrast `web/paging.py`'s warning
    about non-unique sort keys, which does not apply here).

    Handles, never ids or names: invariant 3. This is what stops an agent
    inventing tag names that match nothing.

    OWNER RULING (2026-08-08): this endpoint stays ANY-VALID-TOKEN, including
    `eventernote_url`, `address` and `location_url` -- fields the web UI
    shows only to editors (`templates/tags.html`). That is a deliberate
    crossing, not an accident of reusing `current_tag_exports`, and the
    justification is NOT "/discover is public" (that argument covers
    `/concerts`, whose catalogue really is a mirror of a public page, not
    this). It is that the tag VOCABULARY itself carries nothing sensitive:
    `eventernote_url` and `location_url` are public third-party URLs, and
    `address` is a public venue address -- none of the three is personal data,
    and unlike `/leads` this endpoint names no one. The tier stays as it is;
    do not tighten it without a fresh ruling.
    """
    exports = await current_tag_exports(session)
    if kind:
        exports = [e for e in exports if e.kind == kind]
    total = len(exports)
    window = exports[offset : offset + limit]
    return [
        {
            "handle": e.handle,
            "name": e.name,
            "name_en": e.name_en,
            "name_zh": e.name_zh,
            "kind": e.kind,
            "parent": e.parent,
            "voiced_by": e.voiced_by,
            "members": list(e.members),
            "region": e.region,
            "city": e.city,
            "city_en": e.city_en,
            "city_zh": e.city_zh,
            "address": e.address,
            "location_url": e.location_url,
            "eventernote_url": e.eventernote_url,
        }
        for e in window
    ], total


async def catalogue_export_files(session: AsyncSession) -> list[tuple[str, str]]:
    """(path in zip, text) for the whole catalogue, deterministically ordered.

    CATALOGUE TABLES ONLY -- concerts, days, rounds, qualifiers, tags,
    tag_members. Never a JOIN to a user table, and `created_by` is never
    emitted: nothing to leak beats a filter to get wrong. `users`,
    `web_sessions`, `round_outcomes`, `concert_subscriptions`, `leg_opt_outs`,
    `reminder_rules`, `reminder_queue`, `notifications` and `delivery_log` are
    all personal and none is touched.
    """
    exports = await current_tag_exports(session)
    files = [("tags.yaml", tags_to_yaml(exports)), ("RESTORE.txt", RESTORE_NOTES)]

    concerts = list((await session.execute(
        select(Concert).order_by(Concert.event_id)
    )).scalars())
    for concert in concerts:
        files.append(
            (f"concerts/{concert.event_id}.yaml", await concert_export_yaml(session, concert))
        )
    return files


@dataclass
class ImportChoices:
    """What the operator decided, keyed by (handle, field) and (handle, member).

    Values are the literal strings "mine"/"theirs" and "add"/"remove" and
    nothing else. The browser never sends a VALUE -- the data always comes from
    re-parsing the pasted file -- so a forged form cannot inject anything, only
    choose between two things that were already in front of it.
    """

    fields: dict[tuple[str, str], str] = field(default_factory=dict)
    members: dict[tuple[str, str], str] = field(default_factory=dict)


# The COMPARABLE_FIELDS that hold a HANDLE rather than a value. They compare like
# any other string, but nothing can WRITE one until every tag in the file exists,
# so pass 1 skips them and pass 2 resolves them.
#
# Today the ORM attribute a missing entry would hit does not exist (the columns
# are `parent_id`/`voiced_by_tag_id`), so the setattr would be an inert write to
# an unmapped attribute -- but it would still put the handle in `report.filled`,
# claiming pass 1 wrote something it cannot write, and it would become a real
# corruption the day a relationship of that name is added.
_HANDLE_FIELDS = frozenset({"parent", "voiced_by"})


def _takes_handle(entry: TagPlan, choices: ImportChoices, name: str) -> bool:
    """Does this handle field get written at all?

    A new tag takes everything the file says; an existing one takes a FILL
    automatically (writing into emptiness loses nothing) and a CONFLICT only when
    the operator picked the file's value. No answer means keep mine.
    """
    return (
        entry.is_new
        or name in entry.fills
        or choices.fields.get((entry.handle, name)) == "theirs"
    )


async def apply_tag_import(
    session: AsyncSession,
    plan: ImportPlan,
    choices: ImportChoices,
    created_by: int | None = None,
) -> TagImportReport:
    """Write what the plan says, resolved by the operator's choices.

    EVERY DEFAULT IS THE ONE THAT CHANGES NOTHING: an unanswered conflict keeps
    the catalogue's value, and a member removal happens only when explicitly
    chosen. A truncated or forged form therefore cannot overwrite or delete.

    Two passes, for the same reason the original importer had two: `parent` and
    members are HANDLES, so nothing can resolve until every tag in the file
    exists.

    Writes TagMember rows directly rather than through `attach_tag`, which is
    deliberate -- attach_tag is about CONCERT attachment and carries invariant
    3's expansion with it, and this must touch no concert at all.

    Does not commit; the caller owns the transaction, so a rejected file leaves
    nothing behind.
    """
    report = TagImportReport(warnings=list(plan.warnings))
    by_slug = {
        slug: tag_id for tag_id, slug in await session.execute(select(Tag.id, Tag.slug))
    }

    for entry in plan.tags:
        if entry.kind_mismatch:
            report.skipped.append(entry.handle)
            continue
        tag = entry.incoming
        if entry.is_new:
            row = await create_tag_row(
                session,
                name=tag.name, kind=tag.kind, slug=tag.handle,
                name_en=tag.name_en, name_zh=tag.name_zh,
                region=tag.region, city=tag.city, city_en=tag.city_en,
                city_zh=tag.city_zh, address=tag.address,
                location_url=tag.location_url, eventernote_url=tag.eventernote_url,
                created_by=created_by,
            )
            await session.flush()
            by_slug[tag.handle] = row.id
            report.created.append(entry.handle)
            continue

        row = await session.get(Tag, by_slug[entry.handle])
        touched = False
        for name, value in entry.fills.items():
            if name in _HANDLE_FIELDS:
                continue  # a handle, not a value -- resolved in pass 2
            setattr(row, name, value)
            touched = True
        resolved = False
        for conflict in entry.conflicts:
            if choices.fields.get((entry.handle, conflict.field)) != "theirs":
                continue  # KEEP MINE is the default, and "no answer" means it
            if conflict.field in _HANDLE_FIELDS:
                resolved = True
                continue  # resolved in pass 2
            setattr(row, conflict.field, conflict.incoming)
            resolved = True
        if touched:
            report.filled.append(entry.handle)
        if resolved:
            report.resolved.append(entry.handle)
        if not touched and not resolved and not entry.needs_choice:
            report.unchanged.append(entry.handle)

    # Pass 2: parent, voiced_by and membership, once every tag exists.
    for entry in plan.tags:
        if entry.kind_mismatch or entry.handle not in by_slug:
            continue
        row = await session.get(Tag, by_slug[entry.handle])
        wanted_parent = entry.incoming.parent
        if _takes_handle(entry, choices, "parent") and wanted_parent:
            parent_id = by_slug.get(wanted_parent)
            allowed = ALLOWED_PARENT_KINDS.get(row.kind, ())
            if parent_id is None:
                report.warnings.append(
                    f"{entry.handle}: parent {wanted_parent!r} is in neither the file "
                    f"nor the catalogue -- left without a parent"
                )
            # The SAME table POST /tags enforces, deliberately shared rather
            # than restated: this path kept a franchise-only rule of its own
            # after the editor widened, and a catalogue file therefore could not
            # express a subunit at all.
            elif (await session.get(Tag, parent_id)).kind not in allowed:
                report.warnings.append(
                    f"{entry.handle}: a {row.kind.value} tag cannot have "
                    f"{wanted_parent!r} as a parent -- left without one"
                )
            # GROUP -> GROUP made loops possible for the first time, and this is
            # a write path fed by a hand-editable file, so the guard belongs
            # here as much as on the route.
            elif await would_create_tag_cycle(session, row.id, parent_id):
                report.warnings.append(
                    f"{entry.handle}: parent {wanted_parent!r} would close a loop -- "
                    f"left without a parent"
                )
            else:
                row.parent_id = parent_id

        # Her seiyuu, by handle. The TARGET must be an ARTIST, checked here for
        # the same reason `parent` and `members` are checked three lines either
        # side: a blank column makes this an auto-applied FILL, so a hand-edited
        # `voiced_by: k-arena` would be written with nobody deciding anything --
        # and the next `attach_tag` of that character materialises whatever the
        # id names onto the concert, so a VENUE renders as a performer and its
        # followers get a "new event" DM out of `handle_newly_tagged`.
        #
        # `detach_tag`'s kind guard does NOT cover this: it protects the SOURCE
        # (a non-character carrying voiced_by) and says nothing about the target.
        #
        # Refusing a non-ARTIST also refuses SELF-voicing for free. A character
        # pointed at herself is a real trap: `performer_clusters` puts her in
        # `paired_seiyuu` and filters her out of `entries`, so she vanishes from
        # the Performing panel altogether.
        wanted_voice = entry.incoming.voiced_by
        if _takes_handle(entry, choices, "voiced_by") and wanted_voice:
            voice_id = by_slug.get(wanted_voice)
            if voice_id is None:
                report.warnings.append(
                    f"{entry.handle}: voiced_by {wanted_voice!r} is in neither the file "
                    f"nor the catalogue -- left unvoiced"
                )
            elif (voice := await session.get(Tag, voice_id)).kind is not TagKind.ARTIST:
                report.warnings.append(
                    f"{entry.handle}: voiced_by {wanted_voice!r} is a "
                    f"{voice.kind.value}, and only an artist can voice a character "
                    f"-- left unvoiced"
                )
            else:
                row.voiced_by_tag_id = voice_id

        # A NEW tag simply gets the file's members; an existing one only gets
        # the additions the operator ticked.
        additions = list(entry.incoming.members) if entry.is_new else entry.member_additions
        for member in additions:
            if not entry.is_new and choices.members.get((entry.handle, member)) != "add":
                continue
            member_id = by_slug.get(member)
            if member_id is None:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is in neither the file nor the "
                    f"catalogue -- that membership dropped"
                )
                continue
            if (await session.get(Tag, member_id)).kind is TagKind.GROUP:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is a group, and groups do not "
                    f"nest -- dropped"
                )
                continue
            session.add(
                TagMember(group_tag_id=by_slug[entry.handle], member_tag_id=member_id)
            )
        for member in entry.member_removals:
            if choices.members.get((entry.handle, member)) != "remove":
                continue  # NEVER by default -- the one destructive act in here
            member_id = by_slug.get(member)
            if member_id is not None:
                await session.execute(
                    delete(TagMember).where(
                        TagMember.group_tag_id == by_slug[entry.handle],
                        TagMember.member_tag_id == member_id,
                    )
                )
    return report


async def find_tags_by_name_and_kind(
    session: AsyncSession, name: str, kind: TagKind
) -> list[Tag]:
    """EVERY tag of this kind with this name, case-insensitively, oldest first.

    PLURAL because names are not unique and never will be: two performers may
    genuinely share one (owner ruling, 2026-07-29). A name is a hint for a
    human, never an identity -- `slug` is the identity.

    This replaced two single-result lookups, `find_tag_by_name` (name only) and
    `find_tag_by_name_and_kind`, both of which used `scalar_one_or_none` and so
    raised `MultipleResultsFound` the moment a duplicate existed. Neither
    survives: deleting them is what removes the bug class, rather than leaving a
    function that is safe only while the data happens to cooperate. A caller
    wanting "one" must say which one it means -- `[0]` for the oldest.

    Ordered by id so that choice is deterministic.
    """
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag)
        .where(sa_func.lower(Tag.name) == name.strip().lower(), Tag.kind == kind)
        .order_by(Tag.id)
    )
    return list(res.scalars())


async def group_members(session: AsyncSession, group_tag_id: int) -> list[Tag]:
    res = await session.execute(
        select(Tag)
        .join(TagMember, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id == group_tag_id)
        .order_by(Tag.name)
    )
    return list(res.scalars())


async def members_by_group(
    session: AsyncSession, group_tag_ids: Sequence[int]
) -> dict[int, list[Tag]]:
    """Every listed group's members in ONE query, ordered by name.

    The per-group `group_members` above is still correct for a single group;
    this exists because /tags and /preferences each wanted the map for every
    group at once and built it with a dict comprehension -- 65 round trips on
    the live catalogue.

    Every requested id gets an entry: a group with no members yields an empty
    list, because callers index this map per group and a missing key is a
    different bug in each of them.
    """
    out: dict[int, list[Tag]] = {gid: [] for gid in group_tag_ids}
    if not out:
        return out
    res = await session.execute(
        select(TagMember.group_tag_id, Tag)
        .join(Tag, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id.in_(list(out)))
        .order_by(TagMember.group_tag_id, Tag.name)
    )
    for group_id, tag in res:
        out[group_id].append(tag)
    return out


@dataclass(frozen=True)
class PerformerEntry:
    """One chip. `seiyuu` is set ONLY when the tag is a CHARACTER and her voice
    actor is ALSO attached to this concert -- the both-ends rule. Otherwise it
    is None and the chip renders plain, which is what makes a lone character
    and a lone artist look identical, deliberately."""

    tag: Tag
    seiyuu: Tag | None = None


@dataclass(frozen=True)
class PerformerCluster:
    """One labelled row of the concert page's Performing panel: a GROUP tag
    and the concert's attached performers belonging to it. `group is None` is
    the trailing cluster of performers in no attached group. `depth` is 1 when
    this group's DIRECT parent is an attached GROUP -- a subunit with no parent
    group present is an ordinary top-level cluster (owner rule)."""

    group: Tag | None
    performers: tuple[PerformerEntry, ...] = ()
    depth: int = 0


async def performer_clusters(
    session: AsyncSession, concert: Concert
) -> list[PerformerCluster]:
    """The concert's attached performers, grouped by their attached GROUP tags.

    CALLER PRECONDITION: `concert.tags` must already be loaded (selectinload
    or a refresh) -- a bare `session.get(Concert, ...)` hands this the very
    MissingGreenlet the rest of the docstring is about.

    DISPLAY ONLY. The input is the materialized `concert_tags` set exactly as
    invariant 3 left it (attach expanded it once, editors pruned it); nothing
    here writes, and a later membership edit still never reaches an existing
    concert -- it only changes which cluster an already-attached artist falls
    into.

    Three things about the shape of this, each deliberate:

    * It is service-side, not a template loop. The relationship a template
      would have to reach for is `Tag.members`, a lazy self-referential m2m,
      and touching that during async template rendering raises
      `MissingGreenlet` -- a 500 this project has shipped once. This function
      never touches the relationship at all: it reads the `tag_members`
      association table directly, in one awaited query, so there is no lazy
      load left to fire.
    * `group_members()` is NOT reused. It is per-group, so a franchise concert
      with several units would issue one query per unit. Membership is read
      here in ONE batched query over `tag_members` for the attached group ids.
    * A performer in two attached groups appears under BOTH (owner decision,
      2026-07-27): the repetition is information -- she really is in both --
      and deduplicating would leave the main group's cluster looking
      incomplete to exactly the reader who came to see it. Do not "optimize"
      it away.

    Two relationships are DRAWN here, and both obey one rule: a relationship
    shows only when BOTH of its ends are attached to this concert.

    * A CHARACTER pairs with her seiyuu into one entry (the split-pill chip)
      only when that seiyuu is attached too, and she is then dropped from the
      standalone list because she is rendered inside the pill. Either end
      alone renders exactly as an ordinary artist does.
    * A GROUP nests under its parent only when the parent is an attached
      GROUP. "Attached GROUP", not "attached tag": a group's parent_id is
      usually a FRANCHISE, and a franchise opens no cluster to nest beneath,
      so asking the looser question would drop every franchise bill's
      performer blocks off the page.

    Both are DERIVED per concert, never stored -- which is also why nothing
    here needs provenance for a seiyuu: she is paired exactly when some
    attached character names her, the same derivation the prune rule and the
    editor already use.
    """
    groups = [t for t in concert.tags if t.kind is TagKind.GROUP]
    people = [
        t for t in concert.tags if t.kind in (TagKind.ARTIST, TagKind.CHARACTER)
    ]
    attached_ids = {t.id for t in concert.tags}
    by_id = {t.id: t for t in concert.tags}

    # Pair each character with her seiyuu, but only when BOTH ends are here.
    # The seiyuu is then dropped from the standalone list: she is rendered
    # inside the split pill. A seiyuu attached in her own right survives this
    # filter and is listed as herself (owner rule) -- and she reaches the
    # trailer for free, because a group's members are CHARACTER tags now, so
    # she is not in members_by_group at all.
    paired_seiyuu: set[int] = {
        t.voiced_by_tag_id for t in people
        if t.kind is TagKind.CHARACTER
        and t.voiced_by_tag_id is not None
        and t.voiced_by_tag_id in attached_ids
    }
    # `people` keeps concert.tags' order (Tag.name) inside every cluster.
    entries = [
        PerformerEntry(
            t,
            by_id[t.voiced_by_tag_id]
            if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id in attached_ids
            else None,
        )
        for t in people
        if t.id not in paired_seiyuu
    ]

    # Solo-artist concerts are common and have nothing to look up.
    if not groups:
        return [PerformerCluster(None, tuple(entries))] if entries else []

    res = await session.execute(
        select(TagMember.group_tag_id, TagMember.member_tag_id).where(
            TagMember.group_tag_id.in_([g.id for g in groups])
        )
    )
    members_by_group: dict[int, set[int]] = {g.id: set() for g in groups}
    for group_tag_id, member_tag_id in res:
        members_by_group[group_tag_id].add(member_tag_id)

    # Parent-first ordering with depth, and NO second query: `concert.tags`
    # already carries every attached group, so a parent is present exactly
    # when its id is among them. A `session.get(Tag, g.parent_id)` here would
    # be one SELECT per unit on the franchise bills this is for.
    group_ids = {g.id for g in groups}
    children: dict[int, list[Tag]] = {}
    roots: list[Tag] = []
    for g in groups:
        if g.parent_id in group_ids:
            children.setdefault(g.parent_id, []).append(g)
        else:
            roots.append(g)

    clusters: list[PerformerCluster] = []
    emitted: set[int] = set()

    def _emit(g: Tag, depth: int) -> None:
        if g.id in emitted:
            return
        emitted.add(g.id)
        clusters.append(
            PerformerCluster(
                g,
                tuple(e for e in entries if e.tag.id in members_by_group[g.id]),
                depth,
            )
        )
        # Depth stays 1 however deep the chain runs: the rail is one indent,
        # not a ladder. Recursing anyway is what keeps a third rung ON the
        # page -- emitting a root's direct children only would drop it.
        for child in children.get(g.id, ()):
            _emit(child, 1)

    for g in roots:
        _emit(g, 0)
    # A parent cycle (A under B under A, or a group that is its own parent)
    # has no root between its members, so the walk above never reaches them.
    # `apply_tag_import` writes parent_id and is not cycle-guarded, so the
    # shape is reachable from a hand-edited catalogue file -- and an attached
    # group must appear on the page whatever its parent says. This is also
    # why no separate self-parent guard is needed above: a 1-cycle is a cycle
    # and lands here like any other. `emitted` makes the sweep a no-op in the
    # ordinary case, and stops the walk from recursing through a cycle.
    for g in groups:
        _emit(g, 0)

    grouped = {mid for ids in members_by_group.values() for mid in ids}
    trailer = tuple(e for e in entries if e.tag.id not in grouped)
    if trailer:
        clusters.append(PerformerCluster(None, trailer))
    return clusters


async def resolve_group_member(
    session: AsyncSession, group_id: int, member_id: int
) -> tuple[Tag, Tag] | None:
    """Both tags plus proof that `member_id` really is a member of the GROUP
    tag `group_id` -- None if any part of that doesn't hold.

    Retroactive-apply bulk-attaches a tag to every active concert carrying
    another tag, queueing a notification per subscriber, so an unvalidated
    (group, member) pair would let any arbitrary pairing fan out a large DM
    wave. This only decides which pairs may be asked about; it does not
    change what gets attached (see the Group Tag Expansion invariant)."""
    group = await session.get(Tag, group_id)
    member = await session.get(Tag, member_id)
    if group is None or member is None or group.kind is not TagKind.GROUP:
        return None
    if await session.get(TagMember, (group_id, member_id)) is None:
        return None
    return group, member


async def active_concerts_missing_member(
    session: AsyncSession, group_id: int, member_id: int, now: datetime | None = None
) -> list[Concert]:
    """Concerts tagged with `group_id` that don't already carry `member_id`
    and have at least one live (non-cancelled) leg whose date hasn't
    passed -- the set the Tags page's retroactive-apply confirmation
    offers to bulk-attach an artist to. "Active" means the concert still has
    a live leg in the future -- the same live-leg reading the concert page's
    leg sections use, expressed here as SQL rather than shared with them,
    because this module sits below web/routes/ in the dependency direction."""
    now = now or _now()
    res = await session.execute(
        select(Concert)
        .join(ConcertTag, ConcertTag.concert_id == Concert.id)
        .where(ConcertTag.tag_id == group_id)
    )
    candidates = list(res.scalars())
    already_tagged = set((await session.execute(
        select(ConcertTag.concert_id).where(ConcertTag.tag_id == member_id)
    )).scalars())

    out = []
    for c in candidates:
        if c.id in already_tagged:
            continue
        await session.refresh(c, ["days"])
        live_starts = [d.starts_at_utc for d in c.days if not d.cancelled]
        if not live_starts or max(live_starts) < now:
            continue
        out.append(c)
    return out


@dataclass(frozen=True)
class TagCounts:
    """Everything a tag chip and its dialog display about what the tag costs
    to change: how many concerts carry it, how many users follow it, how many
    members it has (groups only), and how many of its concerts are still
    upcoming (>=1 non-cancelled leg not yet past)."""

    concerts: int = 0
    followers: int = 0
    members: int = 0
    upcoming: int = 0


async def tag_directory_context(session: AsyncSession, now: datetime | None = None) -> dict:
    """Every count and grouping the Tags directory page needs, in one pass --
    the route stays assembly-only. No N+1: concert/follower/member counts come
    from three GROUP BY aggregates, and the per-concert "active" reading
    (>=1 non-cancelled leg not yet past -- the same live-leg definition
    active_concerts_missing_member uses) is computed once over all days.

    Returns a dict with:
      counts             -- {tag_id: TagCounts}
      franchise_families -- [(franchise Tag, [(group Tag, [member Tag, ...], depth),
                            ...]), ...] in franchise name order; groups in name
                            order, each immediately followed by its subunits at
                            depth+1 (see the walk below -- no group is ever
                            omitted, whatever its parent_id says)
      no_franchise_groups-- the same row triples for groups under no franchise
      venue_regions      -- [(region_name, [venue Tag, ...]), ...] alpha, "No region" last
      ungrouped_performers -- ARTIST tags that are no group's member, name order
      summary            -- {concerts, franchises, groups, performers, venues,
                            untranslated}
      eligible_members   -- {group_id: [(member Tag, n_eligible_concerts), ...]}
      seiyuu_of          -- {character tag_id: performer Tag | None}, resolved
                            off the loaded tag list (Tag.voiced_by is not a
                            loaded relationship)
    """
    now = now or _now()
    tags = list((await session.execute(select(Tag).order_by(Tag.name))).scalars())
    by_id = {t.id: t for t in tags}

    # ── three GROUP BY aggregates ──
    concert_rows = (await session.execute(
        select(ConcertTag.tag_id, func.count()).group_by(ConcertTag.tag_id)
    )).all()
    concerts_by_tag = dict(concert_rows)
    follower_rows = (await session.execute(
        select(TagSubscription.tag_id, func.count()).group_by(TagSubscription.tag_id)
    )).all()
    followers_by_tag = dict(follower_rows)
    member_rows = (await session.execute(
        select(TagMember.group_tag_id, func.count()).group_by(TagMember.group_tag_id)
    )).all()
    members_by_group = dict(member_rows)

    # ── the active/upcoming reading, computed once over all days ──
    day_rows = (await session.execute(
        select(ConcertDay.concert_id, ConcertDay.starts_at_utc, ConcertDay.cancelled)
    )).all()
    live_future_concert_ids: set[int] = set()
    for concert_id, starts_at, cancelled in day_rows:
        if not cancelled and starts_at >= now:
            live_future_concert_ids.add(concert_id)
    all_concert_tag_rows = (await session.execute(
        select(ConcertTag.concert_id, ConcertTag.tag_id)
    )).all()
    upcoming_by_tag: dict[int, int] = {}
    for concert_id, tag_id in all_concert_tag_rows:
        if concert_id in live_future_concert_ids:
            upcoming_by_tag[tag_id] = upcoming_by_tag.get(tag_id, 0) + 1

    counts = {
        t.id: TagCounts(
            concerts=concerts_by_tag.get(t.id, 0),
            followers=followers_by_tag.get(t.id, 0),
            members=members_by_group.get(t.id, 0),
            upcoming=upcoming_by_tag.get(t.id, 0),
        )
        for t in tags
    }

    # ── membership map (group_id -> [member Tag, ...] in name order) ──
    tag_member_rows = (await session.execute(
        select(TagMember.group_tag_id, TagMember.member_tag_id)
    )).all()
    members_of: dict[int, list[Tag]] = {}
    grouped_member_ids: set[int] = set()
    for group_id, member_id in tag_member_rows:
        member = by_id.get(member_id)
        if member is not None:
            members_of.setdefault(group_id, []).append(member)
            grouped_member_ids.add(member_id)
    for members in members_of.values():
        members.sort(key=lambda m: m.name)

    franchises = [t for t in tags if t.kind is TagKind.FRANCHISE]
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    artists = [t for t in tags if t.kind is TagKind.ARTIST]
    venues = [t for t in tags if t.kind is TagKind.VENUE]

    # ── group rows, with subunits nested under their parent group ──
    # GROUP -> GROUP became legal on 2026-08-01, and until this walk existed
    # the two buckets below ("under this franchise" / "no parent at all")
    # named no bucket for a group parented to a GROUP. A subunit therefore
    # fell out of the chips directory entirely -- and so did every performer
    # whose only membership was in it, since `grouped_member_ids` already
    # counted them as grouped and `ungrouped_performers` skips those. A
    # signed-in non-editor saw neither anywhere on /tags, the table view
    # being editor-only.
    #
    # The walk happens HERE and yields a FLAT, pre-ordered list of
    # (group, members, depth). Not a children map recursed over in the
    # template: a parent cycle would loop forever there, and a cycle is
    # reachable (older rows predate `would_create_tag_cycle`, and nothing
    # walks parent_id transitively to notice). `seen` terminates the walk and
    # `leftovers` below carries the property this fix is actually about --
    # EVERY group renders exactly once, whatever its parent_id says.
    children_of: dict[int, list[Tag]] = {}
    for g in groups:
        parent = by_id.get(g.parent_id) if g.parent_id else None
        if parent is not None and parent.kind is TagKind.GROUP:
            children_of.setdefault(parent.id, []).append(g)
    walked: set[int] = set()

    def subunit_member_ids(g: Tag, seen: set[int] | None = None) -> set[int]:
        """Every member of g's subunits, transitively.

        Its own `seen` set, not the walk's `walked`: this runs BEFORE the walk
        reaches those children, and sharing the set would make a parent's
        de-dup depend on visit order. A parent cycle is reachable (rows predate
        `would_create_tag_cycle`), so this needs its own guard or it recurses
        forever.
        """
        seen = set() if seen is None else seen
        if g.id in seen:
            return set()
        seen.add(g.id)
        out: set[int] = set()
        for child in children_of.get(g.id, []):
            out |= {m.id for m in members_of.get(child.id, [])}
            out |= subunit_member_ids(child, seen)
        return out

    def group_rows(g: Tag, depth: int = 0) -> list[tuple[Tag, list[Tag], int]]:
        if g.id in walked:
            return []
        walked.add(g.id)
        # A member who also belongs to one of this group's subunits renders
        # under the subunit and nowhere else (owner, 2026-08-12, THIS PAGE
        # ONLY -- the concert page keeps the repetition, because a bill is a
        # lineup and a catalogue is not). Measured on the live catalogue:
        # 485 member chips -> 343, and 6 parent rows become empty.
        absorbed = subunit_member_ids(g)
        own = [m for m in members_of.get(g.id, []) if m.id not in absorbed]
        rows = [(g, own, depth)]
        for child in children_of.get(g.id, []):
            rows.extend(group_rows(child, depth + 1))
        return rows

    franchise_families = [
        (f, [row for g in groups if g.parent_id == f.id for row in group_rows(g)])
        for f in franchises
    ]
    no_franchise_groups = [
        row for g in groups if g.parent_id is None for row in group_rows(g)
    ]
    # Whatever the walk never reached: a group inside a parent cycle, or one
    # whose parent_id names a tag that is neither a franchise nor a group. It
    # renders at the top of the parentless bucket rather than nowhere at all.
    no_franchise_groups += [
        row for g in groups if g.id not in walked for row in group_rows(g)
    ]

    # ── venues by region, "No region" last ──
    by_region: dict[str, list[Tag]] = {}
    for v in venues:
        by_region.setdefault(v.region or "No region", []).append(v)
    venue_regions = [
        (name, by_region[name])
        for name in sorted(by_region, key=lambda r: (r == "No region", r))
    ]

    ungrouped_performers = [a for a in artists if a.id not in grouped_member_ids]

    # Characters keyed to the performer who voices her, for the split pill.
    # Resolved HERE off the already-loaded tag list: Tag.voiced_by is not a
    # loaded relationship, and a lazy load during async template rendering is
    # a MissingGreenlet 500. A character whose seiyuu is unset -- or whose
    # seiyuu tag was deleted, since the FK is ON DELETE SET NULL -- maps to
    # None and renders as a plain chip.
    seiyuu_of = {
        t.id: by_id.get(t.voiced_by_tag_id)
        for t in tags
        if t.kind is TagKind.CHARACTER
    }

    # ── eligible members per group (powers the apply-to-existing links) ──
    eligible_members: dict[int, list[tuple[Tag, int]]] = {}
    for g in groups:
        entries: list[tuple[Tag, int]] = []
        for member in members_of.get(g.id, []):
            concerts = await active_concerts_missing_member(session, g.id, member.id, now)
            if concerts:
                entries.append((member, len(concerts)))
        eligible_members[g.id] = entries

    summary = {
        "concerts": (await session.execute(
            select(func.count()).select_from(Concert)
        )).scalar_one(),
        "franchises": len(franchises),
        "groups": len(groups),
        "performers": len(artists),
        "venues": len(venues),
        # How many tags still have a hole in their NAME trio -- the backlog,
        # made countable. Deliberately the name only: a VENUE's city trio is
        # all-or-nothing AND optional, so a venue with no city at all is
        # legitimately complete, and folding it in would make one number mean
        # two different things. Same rule the create backstop, the browser
        # guard and the edit-page notice use -- never re-implemented here.
        "untranslated": sum(
            1 for t in tags
            if missing_variants(
                t.name or "", t.name_en or "", t.name_zh or "", mandatory=True,
            )
        ),
    }

    return {
        "counts": counts,
        "franchise_families": franchise_families,
        "no_franchise_groups": no_franchise_groups,
        "venue_regions": venue_regions,
        "ungrouped_performers": ungrouped_performers,
        "summary": summary,
        "eligible_members": eligible_members,
        "seiyuu_of": seiyuu_of,
    }


def match_tag_ids_by_slug(
    slugs: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve HANDLES to ids: (matched ids, unmatched handles).

    EXACT, unlike its by-name sibling, and that is the entire point: a handle
    identifies one tag, so there is no first-tag-wins rule to explain and no
    locale variant to match by accident. Ids come back deduplicated in
    first-mention order; unmatched handles keep their input order so the preview
    can list them verbatim.
    """
    by_slug = {t.slug: t.id for t in tags}
    ids: list[int] = []
    missing: list[str] = []
    for slug in slugs:
        tag_id = by_slug.get(slug)
        if tag_id is None:
            missing.append(slug)
        elif tag_id not in ids:
            ids.append(tag_id)
    return ids, missing


def match_venue_tag_id_by_slug(slug: str | None, venue_tags: Sequence[Tag]) -> int | None:
    """A leg's venue by handle. Exact, for the same reason as above."""
    if not slug:
        return None
    return next((t.id for t in venue_tags if t.slug == slug), None)


def match_venue_tag_id(name: str | None, venue_tags: Sequence[Tag]) -> int | None:
    """The id of the VENUE tag whose canonical `name` matches `name`, or None.

    The ramen.events parse scrapes ONE free-text venue name per event
    (`ParsedConcert.venue_name`); the import preview uses this to pre-select
    that venue in each parsed leg's picker, so the common case -- a venue
    that already has a tag -- needs no click. No match leaves the picker on
    its empty option, which is the editor's cue to mint the tag inline.

    Matching is deliberately narrow: trimmed, case-insensitive, against the
    canonical `name` column ONLY. Not name_en/name_zh (the scrape is the
    site's own rendering, and a locale variant matching by accident would
    silently bind the wrong venue), and not fuzzy (a wrong pre-selection is
    worse than none -- the editor has to notice it to undo it).

    Trimming is Python's `str.strip()`, which drops U+3000 (ideographic
    space) alongside U+0020 -- venue text pasted from Japanese sites carries
    it, and exactly that mismatch bit the earlier venue migration. This is
    also why the comparison happens HERE over an already-loaded tag list
    rather than as a SQL `lower(trim(...))`: SQLite's trim() knows only
    U+0020, so pushing it down would silently reintroduce the bug.
    """
    if not name:
        return None
    needle = name.strip().casefold()
    if not needle:
        return None
    for tag in venue_tags:
        if tag.name and tag.name.strip().casefold() == needle:
            return tag.id
    return None


def match_tag_ids_by_name(
    names: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve draft-supplied tag NAMES to ids: (matched ids, unmatched names).

    The pasted-draft path's counterpart to match_venue_tag_id above, with one
    deliberate difference: it matches name_en and name_zh too, not just the
    canonical column. A draft is written by an agent that read sources in
    whichever language the site used, and every tag name here is resolved
    into a picker the editor immediately SEES (a wrong match is a lit chip
    to un-click, not a silently bound FK) -- the accidental-locale-match
    risk that keeps the venue matcher narrow doesn't apply.

    Same trim reasoning as the neighbor: Python's str.strip() drops U+3000,
    and the comparison stays in Python over the already-loaded tag list so
    SQLite's U+0020-only trim() can never be substituted in.

    Ids come back deduplicated in first-mention order; unmatched names keep
    their input order so the preview can list them verbatim.

    Two more rules the callers depend on:

    - COLLISIONS ARE FIRST-TAG-WINS. A name can legitimately match several
      tags at once (one tag's `name`, another's `name_en`), and the scan
      breaks at the first hit in `tags` order -- so the winner is whichever
      the caller's query listed first, not a "best" match. That is
      deliberate rather than arbitrary: the result is a pre-selected picker
      chip the editor sees and can un-click, so a stable, cheap rule beats
      a scoring one nobody can predict.
    - BLANK NAMES DROP FROM BOTH LISTS. A name that is empty or whitespace
      once trimmed contributes neither an id nor an unmatched entry -- it
      simply vanishes. Drafts routinely carry blank list entries from a
      trailing YAML dash, and surfacing those as "couldn't find ''" in the
      preview would be noise, not information. Callers must therefore not
      assume len(matched) + len(unmatched) == len(names).
    """
    matched: list[int] = []
    unmatched: list[str] = []
    for name in names:
        needle = name.strip().casefold()
        if not needle:
            continue
        for tag in tags:
            if any(
                col and col.strip().casefold() == needle
                for col in (tag.name, tag.name_en, tag.name_zh)
            ):
                if tag.id not in matched:
                    matched.append(tag.id)
                break
        else:
            unmatched.append(name.strip())
    return matched, unmatched


async def tag_picker_context(session: AsyncSession) -> dict:
    """Data the shared tag-picker partial needs: tags grouped by kind, plus
    the two lookup maps its client-side script reads (group->members for
    auto-populating artists, and id->name for rendering selected chips).
    Returns plain dicts, NOT pre-serialized JSON -- the template hands them
    to Jinja's `| tojson`, which must serialize the object itself so it can
    escape `<`/`>`/`&` out of the surrounding <script> block.
    Shared by the new-concert form and the URL-import draft form."""
    tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
    by_kind: dict[str, list[Tag]] = {}
    for t in tags:
        by_kind.setdefault(t.kind.value, []).append(t)
    groups_data = {}
    # ONE query for every group's members, not one per group: this ran ~65 round
    # trips on the live catalogue, on every GET /concerts/new, /concerts/{id}/edit
    # and import preview. members_by_group guarantees an entry per requested id,
    # so the per-group indexing below cannot KeyError on a memberless group.
    group_tags = by_kind.get("group", [])
    members_map = await members_by_group(session, [g.id for g in group_tags])
    for g in group_tags:
        members = members_map[g.id]
        groups_data[g.id] = {
            "name": g.name,
            "franchise": g.parent_id,
            # SPLIT BY KIND, and load-bearing rather than tidy. A GROUP's members
            # may be ARTIST tags, CHARACTER tags or a mix -- the im@s reformat
            # produces the second -- and the picker posts each row into its own
            # field: `members` feeds autoArtists() -> artist_tags,
            # `character_members` feeds autoCharacters() -> character_tags.
            #
            # Unsplit, ticking such a group put CHARACTER ids into artist_tags,
            # `resolve_tags(..., ARTIST)` answered 422 and the concert could not
            # be created at all. Worse, the workaround an editor reaches for
            # after that -- × the auto-added chips -- SUCCEEDED and attached the
            # group alone: the creation form expands with expand=False, so
            # neither the characters nor (via attach_tag's chained step) their
            # seiyuu ever landed, and a follower of the performer was not
            # matched. The loud half and the silent half are one bug.
            #
            # A member of any OTHER kind is dropped rather than defaulted into
            # the artist row: a franchise or venue somehow made a member is not
            # a performer, and offering it as one only reproduces the 422.
            "members": [
                {"id": m.id, "name": m.name} for m in members if m.kind is TagKind.ARTIST
            ],
            "character_members": [
                {"id": m.id, "name": m.name}
                for m in members
                if m.kind is TagKind.CHARACTER
            ],
        }
    # {character id: her seiyuu's ARTIST id}. The picker reads it for ONE thing:
    # keeping a derived seiyuu OUT of the artist row while her character is
    # selected (owner ruling 2026-08-01 -- she is auto-correlated and shown as
    # `cv. xxx`, never offered as a tick). Without it, a seiyuu who is also a
    # direct ARTIST member of a selected group is re-ticked by autoArtists(),
    # posted as artist_tags, and therefore lands in edit_concert's `after_ids`
    # -- so she is never detached when her character goes, which is the prune
    # rule unreachable from the editor all over again.
    character_seiyuu = {
        t.id: t.voiced_by_tag_id
        for t in by_kind.get("character", [])
        if t.voiced_by_tag_id is not None
    }
    tag_names = {t.id: t.name for t in tags}
    # Which tags must show their handle beside their name: ONLY those sharing a
    # (name, kind) with another tag. Two identical chips are unusable, but
    # showing every handle would put noise on the overwhelming majority that do
    # not collide. Decided HERE rather than in the template's JS, because "are
    # these the same tag to a reader" is a question about the data.
    #
    # A parallel map rather than restructuring tag_names, whose {id: name} shape
    # several templates' inline scripts already read -- far smaller blast radius
    # than changing a contract in place.
    by_name_and_kind: dict[tuple[str, str], list[int]] = {}
    for t in tags:
        by_name_and_kind.setdefault((t.name.strip().lower(), t.kind.value), []).append(t.id)
    slug_by_id = {t.id: t.slug for t in tags}
    tag_disambiguators = {
        tag_id: slug_by_id[tag_id]
        for ids in by_name_and_kind.values()
        if len(ids) > 1
        for tag_id in ids
    }
    return {
        "by_kind": by_kind,
        "groups": groups_data,
        "character_seiyuu": character_seiyuu,
        "tag_names": tag_names,
        "tag_disambiguators": tag_disambiguators,
    }


async def _is_attached(session: AsyncSession, concert_id: int, tag_id: int) -> bool:
    res = await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )
    return res.scalar_one_or_none() is not None


async def attach_tag(
    session: AsyncSession, concert_id: int, tag: Tag, expand: bool = True
) -> list[Tag]:
    """Attach a tag to a concert. Returns the list of tags newly attached.

    THE EXPANSION RULE (agreed semantics): attaching a GROUP tag also
    attaches every current member — at this moment only. Editors may then
    remove individual members (not performing); nothing re-adds them unless
    the group tag itself is detached and re-attached. Group membership
    edits never touch existing concerts.

    expand=False is for the creation form, where the editor picks artists
    explicitly (pre-checked from the group) — expansion there would undo
    their unchecks.
    """
    added: list[Tag] = []
    if not await _is_attached(session, concert_id, tag.id):
        session.add(ConcertTag(concert_id=concert_id, tag_id=tag.id))
        added.append(tag)
        if expand and tag.kind is TagKind.GROUP:
            for member in await group_members(session, tag.id):
                if not await _is_attached(session, concert_id, member.id):
                    session.add(ConcertTag(concert_id=concert_id, tag_id=member.id))
                    added.append(member)

    # THE CHAINED STEP. Every character now attached pulls in its seiyuu.
    # Without it a group-credited im@s show materialises characters only, and
    # tracked_concert_ids -- which matches materialised rows -- never matches
    # anyone following the performer. That is the whole feature.
    #
    # Bounded by construction, and NOT the nested-groups rule returning: a
    # seiyuu is an ARTIST, so group -> character -> seiyuu terminates in two
    # steps and cannot recurse.
    #
    # Deliberately NOT gated on `expand`. That flag exists so the creation
    # form's explicit artist list is not overridden; attaching the seiyuu
    # overrides nothing, and gating it would leave concerts made on that form
    # unmatched for her followers.
    seiyuu_ids = {
        t.voiced_by_tag_id for t in added
        if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id is not None
    }
    for seiyuu_id in sorted(seiyuu_ids):
        if not await _is_attached(session, concert_id, seiyuu_id):
            seiyuu = await session.get(Tag, seiyuu_id)
            if seiyuu is not None:
                session.add(ConcertTag(concert_id=concert_id, tag_id=seiyuu.id))
                added.append(seiyuu)

    await session.flush()
    return added


async def detach_tag(
    session: AsyncSession,
    concert_id: int,
    tag_id: int,
    keep_tag_ids: Collection[int] = (),
) -> None:
    """Remove a tag from a concert -- and, for a CHARACTER, her seiyuu with her.

    Owner rule (2026-08-01), with TWO refinements, both load-bearing:

    * the seiyuu goes ONLY IF no other still-attached character shares her. A
      seiyuu can voice two characters on one bill, and detaching her because
      one was pruned would silently drop the other's performer.
    * the seiyuu goes ONLY IF the caller has not said it is keeping her.
      `keep_tag_ids` is that statement, and it is what makes the concert
      editor's detach-then-attach order safe: `edit_concert` computes the
      final tag set up front, so a seiyuu the editor ticked EXPLICITLY while
      unticking her character is in that set and must not be cascaded off.
      Without it she is in `keep_ids & before_ids` -- in NEITHER of the
      route's two diffs -- so nothing puts her back, the first save loses her
      silently, and a second identical save restores her. The set is the
      caller's DESIRED end state, not the current attachment, so it stays a
      statement of intent rather than a read of the row this call is deleting.

      It stayed load-bearing through the 2026-08-01 ruling that a derived
      seiyuu is never PRE-ticked, which was expected to make it inert and did
      not: ticking her is still a gesture the picker offers, and it now means
      something sharper than it used to -- "credit the performer, not the
      character" -- so honouring it matters more, not less. Measured by
      removing the parameter and running the suite; two editor tests fail.

    KNOWN EDGE, accepted rather than solved: concert_tags does not record WHY a
    tag was attached -- group expansion has had that blind spot since it
    shipped -- so a seiyuu who was ALSO there in her own right is removed when
    the character is pruned, and the editor re-adds her. Building provenance to
    fix that would touch every attach path for a rare case. Under the ruling
    that case is contradictory data rather than a supported state (an event
    credits the character OR the performer), so the missing provenance is now
    the DEFINITION of the behaviour: a standalone tick over an attached
    character is accepted, not remembered, and re-reads as derived.
    """
    tag = await session.get(Tag, tag_id)
    await _detach_one(session, concert_id, tag_id)

    if tag is None or tag.kind is not TagKind.CHARACTER or tag.voiced_by_tag_id is None:
        await session.flush()
        return

    if tag.voiced_by_tag_id in keep_tag_ids:
        await session.flush()
        return

    still_needed = await session.scalar(
        select(func.count())
        .select_from(ConcertTag)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(
            ConcertTag.concert_id == concert_id,
            Tag.kind == TagKind.CHARACTER,
            Tag.voiced_by_tag_id == tag.voiced_by_tag_id,
        )
    )
    if not still_needed:
        await _detach_one(session, concert_id, tag.voiced_by_tag_id)
    await session.flush()


async def _detach_one(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    """The single-row delete detach_tag used to be."""
    row = (await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()
