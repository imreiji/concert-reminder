"""Async database engine and session factory.

WAL mode is enabled on every connection: it lets readers proceed while a
write is in flight, which is what makes 'bot + web + scheduler sharing one
SQLite file' comfortable.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")  # SQLite defaults FKs OFF; we want them ON
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    """FastAPI dependency: one session per request."""
    async with SessionMaker() as session:
        yield session
