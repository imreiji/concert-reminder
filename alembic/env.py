"""Alembic environment.

Reads the database URL from app settings (single source of truth) and strips
the +aiosqlite driver suffix — migrations run over a plain sync connection,
which keeps this file boring.
"""

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.config import settings          # noqa: E402
from app.db.models import Base           # noqa: E402

target_metadata = Base.metadata
sync_url = settings.database_url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # SQLite can't ALTER much; batch mode rebuilds tables
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sync_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
