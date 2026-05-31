"""T2a — DB integrity: FK enforcement is real, cascade works, drift is gone."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.database import AsyncSessionLocal, engine


@pytest.mark.asyncio
async def test_foreign_keys_pragma_is_on():
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA foreign_keys"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_orphan_application_event_is_rejected():
    """Inserting an application_event with a dangling application_id fails."""
    async with AsyncSessionLocal() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO application_events (application_id, event_type) "
                    "VALUES (999999, 'x')"
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_cascade_delete_removes_child_events():
    """Deleting an application cascades to its application_events."""
    async with AsyncSessionLocal() as session:
        # ``created_at`` / ``event_date`` are NOT NULL with Python-side
        # defaults only (no DB server_default), so a raw INSERT must supply
        # them explicitly.
        # ``method='manual'`` so the row satisfies the N2-T3 conditional CHECK
        # ``ck_applications_job_match_required`` without needing a match (this
        # test only exercises FK cascade to application_events).
        await session.execute(
            text(
                "INSERT INTO applications (id, method, status, created_at) "
                "VALUES (1, 'manual', 'applied', datetime('now'))"
            )
        )
        await session.execute(
            text(
                "INSERT INTO application_events (application_id, event_type, event_date) "
                "VALUES (1, 'created', datetime('now'))"
            )
        )
        await session.commit()

        await session.execute(text("DELETE FROM applications WHERE id = 1"))
        await session.commit()

        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM application_events WHERE application_id = 1")
            )
        ).scalar()
        assert remaining == 0


@pytest.mark.asyncio
async def test_dead_batch_time_column_is_gone():
    async with engine.connect() as conn:
        cols = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(search_settings)"))).fetchall()
        }
    assert "batch_time" not in cols
