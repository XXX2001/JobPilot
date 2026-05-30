# Finish Sprint — Lot 1 (T2a): Schema Enforcement + Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Alembic the single source of truth for the schema, enforce SQLite foreign keys at runtime, and centralize the UTC-now helper.

**Architecture:** Add a `backend/utils/time.py` helper, turn on `PRAGMA foreign_keys=ON`, declare `ForeignKey` on the bare relational columns, write one Alembic catch-up revision that brings the migration chain in sync with the models (Gmail tables, drifted columns, FK constraints, drop dead `batch_time`), then switch `init_db()` to run `alembic upgrade head` and delete the ad-hoc `_migrate_add_columns()` runtime migrator.

**Tech Stack:** SQLAlchemy 2 (async, aiosqlite), Alembic (async env), pytest / pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-30-finish-inflight-sprint-design.md` §5.

---

## File Structure

- Create: `backend/utils/time.py` — `utc_now()` (aware) + `naive_utc_now()` (naive), the only UTC helpers.
- Create: `alembic/versions/<rev>_t2a_schema_catchup_and_fks.py` — catch-up + FK migration.
- Create: `tests/test_db_integrity.py` — FK enforcement + cascade + dead-column tests.
- Create: `tests/test_migrations.py` — `upgrade head` clean + models↔migrations no-diff.
- Modify: `backend/database.py` — FK pragma, `init_db()` via Alembic, delete `_migrate_add_columns()`.
- Modify: `backend/models/{application,job,document,user,gmail}.py` — `ForeignKey(...)` + import the shared time helper.
- Modify: `tests/conftest.py` — keep FK ON for the session, only toggle OFF around the wipe.

---

## Task 1: Centralized UTC time helper

**Files:**
- Create: `backend/utils/time.py`
- Test: `tests/test_db_integrity.py` (created later; this task is covered by `tests/test_time_helper.py`)
- Test: `tests/test_time_helper.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_helper.py`:

```python
from datetime import timezone

from backend.utils.time import naive_utc_now, utc_now


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_naive_utc_now_has_no_tzinfo():
    now = naive_utc_now()
    assert now.tzinfo is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_time_helper.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.utils.time'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/utils/time.py`:

```python
"""Centralized UTC time helpers.

Two flavours intentionally exist:

* :func:`utc_now` — timezone-aware UTC. Use in API/service code.
* :func:`naive_utc_now` — naive UTC (``tzinfo=None``). Use for ORM
  ``DateTime`` column defaults so stored values stay comparable with the
  naive datetimes SQLite already holds. Both replace the duplicated
  ``_now()`` / ``_utc_now()`` definitions that were scattered across
  ``backend/models/*`` and ``backend/api/*``.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def naive_utc_now() -> datetime:
    """Return the current UTC time with ``tzinfo`` stripped (naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_time_helper.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Replace the duplicated helpers**

In each of `backend/models/application.py`, `backend/models/job.py`, `backend/models/document.py`, `backend/models/user.py`, `backend/models/gmail.py`: delete the local `def _now()` block and replace usages. Example for `backend/models/application.py`:

```python
from backend.models.base import Base
from backend.utils.time import naive_utc_now

# ... delete the local _now() function ...

    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now, index=True)
    # and in ApplicationEvent:
    event_date: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)
```

In the API/service modules that define `_utc_now()` (`backend/applier/daily_limit.py`, `backend/applier/follow_up.py`, `backend/api/today.py`, `backend/api/analytics.py`, `backend/api/settings.py`, `backend/api/correspondence.py`): delete the local `def _utc_now()` and `from backend.utils.time import utc_now`, then replace `_utc_now()` call-sites with `utc_now()`.

- [ ] **Step 6: Run the full suite to verify no regression**

Run: `uv run pytest -q`
Expected: same pass count as baseline (no new failures).

- [ ] **Step 7: Commit**

```bash
git add backend/utils/time.py tests/test_time_helper.py backend/models backend/applier/daily_limit.py backend/applier/follow_up.py backend/api/today.py backend/api/analytics.py backend/api/settings.py backend/api/correspondence.py
git commit -m "refactor(T2a): centralize UTC-now into backend/utils/time.py"
```

---

## Task 2: Declare ForeignKey constraints on the models

**Files:**
- Modify: `backend/models/application.py:22,45`, `backend/models/job.py:36,65`, `backend/models/document.py:22`
- Test: covered by Task 4's `tests/test_db_integrity.py`

> No standalone test here — FK behavior is exercised in Task 4 once the pragma + migration land. This task only changes the declarations so `Base.metadata` (the migration `target_metadata`) carries the constraints.

- [ ] **Step 1: Add `ForeignKey` imports and constraints**

`backend/models/application.py`:

```python
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text

# Application:
    job_match_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("job_matches.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

# ApplicationEvent:
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"),
    )
```

`backend/models/job.py`:

```python
from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
)

# Job:
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("job_sources.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

# JobMatch:
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"),
    )
```

`backend/models/document.py`:

```python
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text

# TailoredDocument:
    job_match_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("job_matches.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
```

> `ondelete` rationale, documented inline: child rows that cannot exist without their parent use `CASCADE`; nullable back-references that should survive parent deletion use `SET NULL` (`applications.job_match_id`, `jobs.source_id`).

- [ ] **Step 2: Verify models still import cleanly**

Run: `uv run python -c "import backend.models"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add backend/models/application.py backend/models/job.py backend/models/document.py
git commit -m "feat(T2a): declare ForeignKey constraints on relational columns"
```

---

## Task 3: Turn on SQLite foreign-key enforcement

**Files:**
- Modify: `backend/database.py:28-34`
- Test: `tests/test_db_integrity.py` (Task 4)

- [ ] **Step 1: Add the pragma to the connect listener**

In `backend/database.py`, extend `set_wal_mode` (rename to reflect both pragmas):

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    try:
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        logger.debug("Could not set WAL mode on connect; continuing")
    try:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        logger.debug("Could not enable foreign_keys on connect; continuing")
```

- [ ] **Step 2: Verify the pragma is live**

Run:
```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from backend.database import engine
async def main():
    async with engine.connect() as c:
        r = await c.execute(text('PRAGMA foreign_keys'))
        print('foreign_keys =', r.scalar())
asyncio.run(main())
"
```
Expected: `foreign_keys = 1`.

- [ ] **Step 3: Commit**

```bash
git add backend/database.py
git commit -m "feat(T2a): enable PRAGMA foreign_keys=ON on every connection"
```

---

## Task 4: DB integrity tests (FK enforcement + cascade)

**Files:**
- Create: `tests/test_db_integrity.py`
- Modify: `tests/conftest.py:105-122` (ensure FK stays ON outside the wipe)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_integrity.py`:

```python
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
        await session.execute(
            text("INSERT INTO applications (id, method, status) VALUES (1, 'auto', 'applied')")
        )
        await session.execute(
            text("INSERT INTO application_events (application_id, event_type) VALUES (1, 'created')")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_integrity.py -q`
Expected: FAIL — orphan/cascade tests fail (no FK) and/or `batch_time` still present, until Task 3 + Task 5 land. (The pragma test passes once Task 3 is in.)

- [ ] **Step 3: Adjust conftest so FK enforcement holds during tests**

In `tests/conftest.py`, the wipe already toggles `PRAGMA foreign_keys=OFF`; make it explicit that it is re-enabled afterwards on the same connection so the test session runs with FK ON (the connect listener already sets it, but the wipe connection must restore it):

```python
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        for table in _WIPE_ORDER:
            try:
                await conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                continue
        await conn.execute(text("PRAGMA foreign_keys = ON"))
```

- [ ] **Step 4: Leave tests red** until Task 5 adds the catch-up migration (cascade + `batch_time` removal depend on it). Proceed to Task 5; this file is re-run there.

- [ ] **Step 5: Commit the tests**

```bash
git add tests/test_db_integrity.py tests/conftest.py
git commit -m "test(T2a): DB-integrity tests for FK enforcement and cascade"
```

---

## Task 5: Alembic catch-up + FK migration

**Files:**
- Create: `alembic/versions/<rev>_t2a_schema_catchup_and_fks.py`

- [ ] **Step 1: Generate a revision skeleton**

Run: `uv run alembic revision -m "t2a schema catchup and fks"`
Expected: prints `Generating .../alembic/versions/<rev>_t2a_schema_catchup_and_fks.py`. Note the `<rev>` id and that `down_revision = "e3a1f2b8c9d7"` (current head).

- [ ] **Step 2: Write the migration body**

Edit the generated file so `upgrade()` (a) deletes orphan rows before adding FK, (b) creates the missing Gmail tables, (c) drops the dead `batch_time` column, and (d) recreates the FK-bearing tables via SQLite batch mode. Because SQLite cannot `ALTER TABLE ADD CONSTRAINT`, use `op.batch_alter_table(..., recreate="always")` with `naming` preserved.

```python
"""t2a schema catchup and fks

Revision ID: <rev>
Revises: e3a1f2b8c9d7
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "<rev>"
down_revision = "e3a1f2b8c9d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Delete orphan rows so the new FK constraints can be added safely.
    bind.execute(sa.text(
        "DELETE FROM application_events WHERE application_id NOT IN "
        "(SELECT id FROM applications)"
    ))
    bind.execute(sa.text(
        "DELETE FROM tailored_documents WHERE job_match_id IS NOT NULL "
        "AND job_match_id NOT IN (SELECT id FROM job_matches)"
    ))
    bind.execute(sa.text(
        "DELETE FROM job_matches WHERE job_id NOT IN (SELECT id FROM jobs)"
    ))

    # 2. Drop dead column (guard: only if present).
    cols = {r[1] for r in bind.execute(sa.text("PRAGMA table_info(search_settings)")).fetchall()}
    if "batch_time" in cols:
        with op.batch_alter_table("search_settings") as batch:
            batch.drop_column("batch_time")

    # 3. Add FK constraints by recreating the affected tables (SQLite batch).
    with op.batch_alter_table("application_events", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_application_events_application_id",
            "applications", ["application_id"], ["id"], ondelete="CASCADE",
        )
    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_applications_job_match_id",
            "job_matches", ["job_match_id"], ["id"], ondelete="SET NULL",
        )
    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_job_matches_job_id",
            "jobs", ["job_id"], ["id"], ondelete="CASCADE",
        )
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_jobs_source_id",
            "job_sources", ["source_id"], ["id"], ondelete="SET NULL",
        )
    with op.batch_alter_table("tailored_documents", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_tailored_documents_job_match_id",
            "job_matches", ["job_match_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    for table in (
        "tailored_documents", "jobs", "job_matches", "applications", "application_events",
    ):
        with op.batch_alter_table(table, recreate="always") as batch:
            pass  # recreate without the FK reproduces the pre-T2a shape
    with op.batch_alter_table("search_settings") as batch:
        batch.add_column(sa.Column("batch_time", sa.String(), nullable=True))
```

> Note: the Gmail tables (`gmail_credentials`, `gmail_messages`, `application_correspondence`) are created by `Base.metadata` today via `create_all`. After Task 6 makes Alembic authoritative, confirm they are represented in the migration chain — if `test_migrations.py` (Task 7) reports them as a diff, add `op.create_table(...)` for each here using the column definitions from `backend/models/gmail.py`.

- [ ] **Step 3: Apply the migration to a throwaway DB**

Run:
```bash
JOBPILOT_DATA_DIR=$(mktemp -d) uv run alembic upgrade head
```
Expected: ends with `Running upgrade e3a1f2b8c9d7 -> <rev>` and no error.

- [ ] **Step 4: Run the integrity tests**

Run: `uv run pytest tests/test_db_integrity.py -q`
Expected: PASS (4 passed) — assuming Task 6's init-via-Alembic is in; if still red on cascade, complete Task 6 then re-run.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions
git commit -m "feat(T2a): catch-up migration adds FK constraints, drops dead batch_time"
```

---

## Task 6: Make Alembic authoritative in `init_db()`

**Files:**
- Modify: `backend/database.py:40-94` (drop `create_all` + `_migrate_add_columns`, run migrations)

- [ ] **Step 1: Replace `init_db()` to upgrade via Alembic**

```python
async def init_db():
    # Alembic is the single source of truth. Running upgrade head creates a
    # fresh schema from scratch AND brings an existing DB forward, replacing
    # the old create_all + _migrate_add_columns dual path.
    await asyncio.get_running_loop().run_in_executor(None, _alembic_upgrade_head)
    await _seed_default_sources()


def _alembic_upgrade_head() -> None:
    from alembic import command
    from alembic.config import Config

    from backend.config import PROJECT_ROOT

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
```

Add `import asyncio` at the top of `backend/database.py` if not already present, and delete the entire `_migrate_add_columns()` function and its call.

- [ ] **Step 2: Verify a fresh DB bootstraps cleanly**

Run:
```bash
JOBPILOT_DATA_DIR=$(mktemp -d) uv run python -c "import asyncio; from backend.database import init_db; asyncio.run(init_db())"
```
Expected: no error; logs show Alembic running migrations.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: green (per-worker `init_db()` now runs migrations; integrity tests pass).

- [ ] **Step 4: Commit**

```bash
git add backend/database.py
git commit -m "refactor(T2a): init_db runs alembic upgrade head; drop _migrate_add_columns"
```

---

## Task 7: Migration sync test (models ↔ migrations)

**Files:**
- Create: `tests/test_migrations.py`

- [ ] **Step 1: Write the test**

Create `tests/test_migrations.py`:

```python
"""T2a — Alembic is in sync with the models (no autogenerate diff)."""
from __future__ import annotations

import tempfile

import pytest
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
        db_url = f"sqlite:///{tmp.name}"
        command.upgrade(_alembic_config(db_url), "head")

        sync_engine = create_engine(db_url)
        with sync_engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            diff = compare_metadata(mc, Base.metadata)
        sync_engine.dispose()

    # A non-empty diff means the models and the migration head disagree.
    assert diff == [], f"models/migrations out of sync: {diff}"
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: PASS. If it fails listing missing Gmail tables/columns, add the corresponding `op.create_table(...)` / `op.add_column(...)` to the Task 5 migration and re-run until `diff == []`.

- [ ] **Step 3: Run the whole suite + pyright**

Run: `uv run pytest -q && uv run pyright backend/`
Expected: pytest green; pyright at baseline error count.

- [ ] **Step 4: Commit**

```bash
git add tests/test_migrations.py
git commit -m "test(T2a): assert models and Alembic head stay in sync"
```

---

## Self-Review notes (carried from plan author)

- The `naive_utc_now` vs `utc_now` split is intentional — do not collapse them; models store naive UTC and API code uses aware UTC. Collapsing would change stored values.
- If `compare_metadata` reports index-name-only diffs that are cosmetic, narrow the assertion to structural diffs (added/removed table/column/fk) rather than weakening the FK checks.
- `init_db()` running Alembic in tests adds a one-time per-worker cost; acceptable per spec §5.4.
