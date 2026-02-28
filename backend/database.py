"""Database utilities for JobPilot.

Note: Some CI/LSP environments running inside this agent may not have
SQLAlchemy installed. We keep runtime imports but add type ignores and
silencing comments to reduce noisy diagnostics from the language server.
"""

import logging
from contextlib import asynccontextmanager

from sqlalchemy import event  # type: ignore
from sqlalchemy.ext.asyncio import (  # type: ignore
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.jobpilot_data_dir}/jobpilot.db",
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def set_wal_mode(dbapi_conn, connection_record):
    try:
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        # some environments may not allow changing journal mode here
        logger.debug("Could not set WAL mode on connect; continuing")


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    from backend.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager  # type: ignore
async def db_session() -> AsyncSession:  # type: ignore[override]
    """Provide an async session context manager.

    The asynccontextmanager typing is a bit strict for some LSPs; the
    type: ignore above keeps pyright from complaining in this workspace.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
