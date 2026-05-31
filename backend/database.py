"""Database utilities for JobPilot.

Note: Some CI/LSP environments running inside this agent may not have
SQLAlchemy installed. We keep runtime imports but add type ignores and
silencing comments to reduce noisy diagnostics from the language server.
"""

import asyncio
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
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    try:
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        # some environments may not allow changing journal mode here
        logger.debug("Could not set WAL mode on connect; continuing")
    try:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        logger.debug("Could not enable foreign_keys on connect; continuing")


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    # Alembic is the single source of truth. Running upgrade head creates a
    # fresh schema from scratch AND brings an existing DB forward, replacing
    # the old create_all + _migrate_add_columns dual path.
    await asyncio.get_running_loop().run_in_executor(None, _alembic_upgrade_head)
    await _seed_default_sources()


def _alembic_upgrade_head() -> None:
    """Bring the database to the Alembic head, reconciling both provenances.

    Runs in a worker thread (``run_in_executor``) because Alembic's env drives
    its own ``asyncio.run`` loop. Detection uses a short-lived *sync* engine so
    no nested event loop is involved.

    Three cases:

    * **Fresh DB** (no application tables): ``upgrade head`` builds everything
      from base.
    * **Legacy create_all DB** (application tables exist but there is no
      ``alembic_version`` stamp): Alembic would otherwise replay the initial
      migration against already-existing tables and die with
      ``table ... already exists``. We ``stamp`` it at the pre-T2a head
      (``e3a1f2b8c9d7``) so ONLY the idempotent T2a catch-up migration runs.
    * **Already-managed DB**: ``upgrade head`` is a normal forward migration.

    Any genuine migration failure propagates — the app lifespan fails fast.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from backend.config import PROJECT_ROOT

    db_path = f"{settings.jobpilot_data_dir}/jobpilot.db"

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # Point Alembic at the same database the async engine uses. The ini ships a
    # fixed relative URL (``data/jobpilot.db``); without this override Alembic
    # would migrate the wrong file under a custom JOBPILOT_DATA_DIR (e.g. the
    # per-worker test databases).
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(sync_engine).get_table_names())
    finally:
        sync_engine.dispose()

    if "alembic_version" not in tables and "applications" in tables:
        logger.info(
            "Existing pre-Alembic database detected; stamping at %s before upgrade",
            "e3a1f2b8c9d7",
        )
        command.stamp(cfg, "e3a1f2b8c9d7")

    command.upgrade(cfg, "head")


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
