"""The two-pass tags importer: create what is missing, wire what it created.

Spec: docs/superpowers/specs/2026-07-30-catalogue-round-trip-design.md
"""

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Notification, Tag, TagMember
from app.db.service import (
    ImportChoices,
    apply_tag_import,
    assign_tag_slug,
    current_tag_exports,
    ensure_user,
)
from app.domain.tags_diff import plan_tag_import
from app.domain.tags_yaml import parse_tags
from app.domain.types import TagKind

ADMIN = 42


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


FILE = """
tags:
  - {handle: love-live, name: "ラブライブ！", name_en: Love Live!, kind: franchise}
  - handle: hasunosora
    name: "蓮ノ空"
    name_en: Hasunosora
    kind: group
    parent: love-live
    members: [kozue-otomune]
  - {handle: kozue-otomune, name: "乙宗梢", kind: artist}
  - handle: k-arena
    name: "Kアリーナ横浜"
    kind: venue
    region: Kanto
    city: "横浜"
    city_en: Yokohama
    address: "神奈川県横浜市"
"""


async def _import(db, text=FILE, choices=None):
    """Plan then apply, exactly as the route does."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        parsed = parse_tags(text)
        plan = plan_tag_import(parsed.tags, await current_tag_exports(s), parsed.warnings)
        report = await apply_tag_import(
            s, plan, choices or ImportChoices(), created_by=ADMIN
        )
        await s.commit()
        return report


async def test_creates_every_missing_tag_with_its_handle(db):
    report = await _import(db)
    assert sorted(report.created) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]
    assert report.skipped == []

    async with db() as s:
        tags = {t.slug: t for t in (await s.execute(select(Tag))).scalars()}
    assert set(tags) == {"love-live", "hasunosora", "kozue-otomune", "k-arena"}
    assert tags["love-live"].name_en == "Love Live!"
    assert tags["k-arena"].region == "Kanto"
    assert tags["k-arena"].city_en == "Yokohama"
    assert tags["k-arena"].address == "神奈川県横浜市"
    assert tags["kozue-otomune"].kind is TagKind.ARTIST


async def test_wires_parent_and_members_in_the_second_pass(db):
    """`parent` and `members` are HANDLES, so they can only resolve once every
    tag exists -- which is why the importer has two passes at all."""
    await _import(db)
    async with db() as s:
        tags = {t.slug: t for t in (await s.execute(select(Tag))).scalars()}
        assert tags["hasunosora"].parent_id == tags["love-live"].id
        links = (await s.execute(select(TagMember))).scalars().all()
        assert [(m.group_tag_id, m.member_tag_id) for m in links] == [
            (tags["hasunosora"].id, tags["kozue-otomune"].id)
        ]


async def test_importing_twice_changes_nothing(db):
    """Idempotence is what makes this safe to run on a populated database.

    The second run reports every tag as UNCHANGED, not `skipped` -- skipped is
    reserved for a refusal (a kind mismatch), and a count that conflates
    "nothing to do" with "I would not touch this" tells the operator nothing.
    """
    await _import(db)
    second = await _import(db)
    assert second.created == []
    assert second.filled == [] and second.resolved == []
    assert sorted(second.unchanged) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]
    assert second.skipped == []

    async with db() as s:
        assert len((await s.execute(select(Tag))).scalars().all()) == 4
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1


async def test_an_existing_tag_is_not_updated_without_a_choice(db):
    """REWRITTEN 2026-07-31, and the rule it pins has narrowed deliberately.

    It used to be "never touch an existing tag". It is now "never OVERWRITE
    unasked": a populated field survives a disagreeing file untouched, while a
    BLANK one is filled, because filling emptiness cannot lose anything. The
    owner's guarantee -- an import can never revert an edit made since the
    export -- is unchanged.
    """
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="Renamed since the export", kind=TagKind.FRANCHISE, slug="love-live"))
        await s.commit()

    await _import(db)
    async with db() as s:
        kept = (await s.execute(select(Tag).where(Tag.slug == "love-live"))).scalar_one()
        assert kept.name == "Renamed since the export", "a disagreement must not overwrite"
        assert kept.name_en == "Love Live!", "but a BLANK field is filled"


async def test_a_parent_that_is_not_a_franchise_warns_and_is_dropped(db):
    report = await _import(db, """
tags:
  - {handle: a, name: A, kind: artist}
  - {handle: g, name: G, kind: group, parent: a}
""")
    assert sorted(report.created) == ["a", "g"]
    assert any("parent" in w for w in report.warnings)
    async with db() as s:
        g = (await s.execute(select(Tag).where(Tag.slug == "g"))).scalar_one()
        assert g.parent_id is None


async def test_a_missing_reference_warns_and_the_rest_still_lands(db):
    report = await _import(db, """
tags:
  - {handle: g, name: G, kind: group, parent: nowhere, members: [ghost]}
""")
    assert report.created == ["g"]
    assert any("nowhere" in w for w in report.warnings)
    assert any("ghost" in w for w in report.warnings)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_a_group_cannot_be_a_member(db):
    """Groups do not nest -- the same rule POST /tags/{id}/members enforces."""
    report = await _import(db, """
tags:
  - {handle: g1, name: G1, kind: group}
  - {handle: g2, name: G2, kind: group, members: [g1]}
""")
    assert any("g1" in w for w in report.warnings)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_membership_of_an_existing_tag_is_left_alone_without_a_choice(db):
    """REWRITTEN 2026-07-31: membership can now be changed, but only when asked.

    With no choices supplied -- which is what a plain restore does -- an existing
    group's membership is untouched, additions included. The destructive
    direction (removal) additionally never happens by default even WITH choices
    present for other things."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="G", kind=TagKind.GROUP, slug="hasunosora"))
        await s.commit()

    await _import(db)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == [], (
            "the group already existed, so its membership must not be written"
        )


async def test_importing_queues_no_notification(db):
    """Invariant 4: creation is not attachment. Same reason quick_create_tag is
    silent -- nobody is owed a DM because taxonomy appeared."""
    await _import(db)
    async with db() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []


async def test_importing_touches_no_concert(db):
    """Invariant 3: group expansion is an attach-time act. A restored membership
    list must never rewrite an existing concert's performers."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        # NB: Concert has no `slug` column -- handles are a TAG concept; a
        # concert's URL handle is `event_id`.
        concert = Concert(event_id="c1", title="C", created_by=ADMIN)
        s.add(concert)
        group = Tag(name="G", kind=TagKind.GROUP)
        s.add(group)
        await assign_tag_slug(s, group)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
        await s.commit()
        before = len((await s.execute(select(ConcertTag))).scalars().all())

    await _import(db)
    async with db() as s:
        after = len((await s.execute(select(ConcertTag))).scalars().all())
    assert after == before


async def test_parser_warnings_reach_the_report(db):
    """A warning the parser raised is useless if the route never shows it."""
    report = await _import(db, "tags:\n  - {name: no handle here, kind: artist}\n")
    assert report.created == []
    assert any("handle" in w for w in report.warnings)


# ── Fills, conflicts, and every default that must fail safe ───────────────


async def test_a_blank_field_is_filled_without_being_asked(db):
    """The case this feature exists for: 79 artists with no eventernote_url."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="乙宗梢", kind=TagKind.ARTIST, slug="kozue"))
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", kind: artist, eventernote_url: "https://e.example/1"}
""")
    assert report.filled == ["kozue"]
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.eventernote_url == "https://e.example/1"


async def test_a_conflict_left_unanswered_keeps_mine(db):
    """The safe default: a truncated or half-submitted form overwrites nothing."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="乙宗梢", name_en="Kozue Otomune", kind=TagKind.ARTIST, slug="kozue"))
        await s.commit()

    await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", name_en: Kozue, kind: artist}
""")
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue Otomune", "unanswered must not overwrite"


async def test_a_conflict_answered_theirs_takes_the_file(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="乙宗梢", name_en="Kozue Otomune", kind=TagKind.ARTIST, slug="kozue"))
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: kozue, name: "乙宗梢", name_en: Kozue, kind: artist}
""", choices=ImportChoices(fields={("kozue", "name_en"): "theirs"}))
    assert report.resolved == ["kozue"]
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue"


async def test_a_member_is_removed_only_when_explicitly_chosen(db):
    """The only destructive operation here, so it never happens by default."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        group = Tag(name="G", kind=TagKind.GROUP, slug="g")
        member = Tag(name="M", kind=TagKind.ARTIST, slug="m")
        s.add_all([group, member])
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=member.id))
        await s.commit()

    file = "tags:\n  - {handle: g, name: G, kind: group}\n  - {handle: m, name: M, kind: artist}\n"
    await _import(db, file)
    async with db() as s:
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1, (
            "no choice given -> the member stays"
        )

    await _import(db, file, choices=ImportChoices(members={("g", "m"): "remove"}))
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_a_member_addition_applies_when_chosen(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add_all([
            Tag(name="G", kind=TagKind.GROUP, slug="g"),
            Tag(name="M", kind=TagKind.ARTIST, slug="m"),
        ])
        await s.commit()

    await _import(
        db,
        "tags:\n  - {handle: g, name: G, kind: group, members: [m]}\n"
        "  - {handle: m, name: M, kind: artist}\n",
        choices=ImportChoices(members={("g", "m"): "add"}),
    )
    async with db() as s:
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1


async def test_a_kind_mismatch_writes_nothing_at_all(db):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Tag(name="Hall", kind=TagKind.VENUE, slug="hall"))
        await s.commit()

    report = await _import(db, """
tags:
  - {handle: hall, name: Hall, kind: artist, city: "横浜"}
""")
    assert report.created == [] and report.filled == []
    assert any("kind" in w for w in report.warnings)
    async with db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "hall"))).scalar_one()
        assert row.kind is TagKind.VENUE and row.city is None


async def test_a_choice_naming_a_handle_not_in_the_file_is_ignored(db):
    """A forged form must not reach past what was pasted."""
    report = await _import(db, choices=ImportChoices(
        fields={("not-in-file", "name"): "theirs"},
        members={("also-not", "x"): "remove"},
    ))
    assert sorted(report.created) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]
