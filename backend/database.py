from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event
from contextlib import asynccontextmanager
from backend.config import settings
import logging

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


@asynccontextmanager
async def db_session() -> AsyncSession:
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
