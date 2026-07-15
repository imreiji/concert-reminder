"""Seed a demo concert so the bot is testable before the web UI exists.

    uv run python scripts/seed_demo.py           # create/refresh demo data
    uv run python scripts/seed_demo.py --clean   # remove it

Creates one concert ("DEMO: Hasunosora 5th Live") owned by the first ID in
EDITOR_WHITELIST, with a realistic window chain — including one window that
closes ~5 minutes from now, so a `/remindme` with days_before=0 produces a
real DM within a couple of scheduler ticks.

Safe to re-run: it deletes and recreates the demo concert each time
(cascades clean up windows/days/rules/queue rows).
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.models import Concert, ConcertDay, User, Window  # noqa: E402
from app.db.session import SessionMaker  # noqa: E402
from app.domain.types import WindowKind  # noqa: E402

DEMO_TITLE = "DEMO: Hasunosora 5th Live"


async def main(clean: bool) -> None:
    now = datetime.now(UTC)

    async with SessionMaker() as session:
        # Remove any previous demo concert (cascades take the rest).
        res = await session.execute(select(Concert).where(Concert.title == DEMO_TITLE))
        for old in res.scalars():
            await session.delete(old)
        await session.flush()

        if clean:
            await session.commit()
            print("demo data removed")
            return

        editor_ids = sorted(settings.editor_ids)
        if not editor_ids:
            print("EDITOR_WHITELIST is empty — put your Discord ID in .env first")
            return
        owner_id = editor_ids[0]

        if await session.get(User, owner_id) is None:
            session.add(User(discord_id=owner_id, username="seed-owner"))
            await session.flush()

        concert = Concert(
            title=DEMO_TITLE,
            franchise="Hasunosora",
            venue="幕張メッセ",
            notes="Seeded demo data — safe to delete",
            created_by=owner_id,
        )
        session.add(concert)
        await session.flush()

        session.add_all(
            [
                ConcertDay(
                    concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=21)
                ),
                ConcertDay(
                    concert_id=concert.id, label="Day 2", starts_at_utc=now + timedelta(days=22)
                ),
                # The instant-gratification window: closes in ~5 minutes.
                Window(
                    concert_id=concert.id,
                    kind=WindowKind.LOTTERY_ROUND,
                    label="最速先行 (closes in 5 min — DM test)",
                    opens_at_utc=now - timedelta(days=1),
                    closes_at_utc=now + timedelta(minutes=5),
                    url="https://example.com/lottery",
                ),
                Window(
                    concert_id=concert.id,
                    kind=WindowKind.ELIGIBILITY_ITEM_SALE,
                    label="シリアル対象CD発売",
                    opens_at_utc=now + timedelta(days=1),
                    closes_at_utc=now + timedelta(days=8),
                ),
                Window(
                    concert_id=concert.id,
                    kind=WindowKind.LOTTERY_ROUND,
                    label="2次先行",
                    opens_at_utc=now + timedelta(days=9),
                    closes_at_utc=now + timedelta(days=13),
                ),
                Window(
                    concert_id=concert.id,
                    kind=WindowKind.RESULT_ANNOUNCEMENT,
                    label="当落発表",
                    opens_at_utc=now + timedelta(days=15),
                ),
                Window(
                    concert_id=concert.id,
                    kind=WindowKind.STREAM_TICKET_SALE,
                    label="Day 1 配信チケット",
                    opens_at_utc=now + timedelta(days=16),
                    closes_at_utc=now + timedelta(days=21),
                ),
            ]
        )
        await session.commit()
        print(f"seeded '{DEMO_TITLE}' (concert id {concert.id}, owner {owner_id})")
        print("try: /upcoming  ->  /remindme concert:DEMO anchor:closes days_before:0")
        print("the 5-minute window should DM you within a tick or two of it closing")


if __name__ == "__main__":
    asyncio.run(main(clean="--clean" in sys.argv))
