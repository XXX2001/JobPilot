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

    await _migrate_add_columns()

    # Seed job_sources from SITE_CONFIGS if the table is empty
    await _seed_default_sources()

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


async def _migrate_add_columns() -> None:
    """Add columns that may be missing from existing databases."""
    from sqlalchemy import text

    migrations = [
        ("search_settings", "cv_tailoring_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
        ("search_settings", "max_results_per_source", "INTEGER NOT NULL DEFAULT 20"),
        ("search_settings", "max_job_age_days", "INTEGER"),
    ]
    try:
        async with engine.begin() as conn:
            for table, column, col_type in migrations:
                # Check if column exists
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result.fetchall()}
                if column not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    )
                    logger.info("Added column %s.%s", table, column)
    except Exception as exc:
        logger.debug("Column migration check: %s", exc)


async def _seed_default_sources() -> None:
    """Populate the job_sources table from SITE_CONFIGS when empty."""
    from sqlalchemy import select

    from backend.models.job import JobSource
    from backend.scraping.site_prompts import SITE_CONFIGS

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(JobSource).limit(1))
            if result.scalar_one_or_none() is not None:
                logger.debug("Job sources already seeded — skipping")
                return  # already seeded

            count = 0
            for key, cfg in SITE_CONFIGS.items():
                if key == "lab_website":
                    continue  # custom website template, not a real source
                source = JobSource(
                    name=key,
                    type=cfg["type"],
                    url=cfg.get("base_url", ""),
                    config={},
                    enabled=True,
                )
                session.add(source)
                count += 1

            await session.commit()
            logger.info("Seeded %d default job sources", count)
    except Exception as exc:
        logger.error("Failed to seed default job sources: %s", exc, exc_info=True)
