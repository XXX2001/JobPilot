"""N2-T4 — covering indexes for hot query paths exist after `upgrade head`.

The canary ``test_migrations.py`` proves model ``__table_args__`` and the
migration agree (``compare_metadata == []``); this test independently asserts
the three composite indexes are physically present in the bootstrapped DB by
querying ``sqlite_master``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.database import engine

EXPECTED_INDEXES = (
    "ix_job_matches_status_batch_date_score",
    "ix_tailored_documents_match_doc_created",
    "ix_applications_status_created",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("index_name", EXPECTED_INDEXES)
async def test_covering_index_exists(index_name: str):
    async with engine.connect() as conn:
        found = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND name = :n"
                ),
                {"n": index_name},
            )
        ).scalar()
    assert found == index_name, f"missing index {index_name!r}"
