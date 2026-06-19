"""T2a — Alembic is in sync with the models (no autogenerate diff)."""
from __future__ import annotations

import tempfile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from backend.config import PROJECT_ROOT
from backend.models import Base


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_is_clean_and_in_sync():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        # ``alembic/env.py`` runs in async mode, so the upgrade needs an async
        # driver URL. ``compare_metadata`` reflects via a plain sync engine on
        # the same file.
        command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{tmp.name}"), "head")

        sync_engine = create_engine(f"sqlite:///{tmp.name}")
        with sync_engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            diff = compare_metadata(mc, Base.metadata)
        sync_engine.dispose()

    # A non-empty diff means the models and the migration head disagree.
    assert diff == [], f"models/migrations out of sync: {diff}"
