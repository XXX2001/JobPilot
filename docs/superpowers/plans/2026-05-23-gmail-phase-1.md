# Gmail Integration Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship read-only Gmail sync — connect a single Google account, poll inbox every 5 min, classify messages with heuristics only, and let the user manually link emails to existing `Application` rows from a dedicated Inbox page.

**Architecture:** Pure-async pipeline built on `httpx` (no `google-auth` / `google-api-python-client` — the repo's existing Gemini wrapper already proves the raw-HTTP pattern). One new APScheduler instance (the first real `.start()` call in the repo), three new SQLAlchemy models, one new SvelteKit page, three new FastAPI routers. Refresh tokens encrypted with the existing Fernet `CREDENTIAL_KEY` scheme (mirrors `SiteCredential`). No LLM calls. No Pub/Sub. No label writes. No status state machine.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy async, SQLite (WAL), `httpx`, **new dep:** `apscheduler[asyncio]>=3.10`. Frontend: SvelteKit 2 + Svelte 5 + Tailwind.

**Spec:** [`docs/reports/2026-05-22-audit/03-gmail-integration-design.md`](../../reports/2026-05-22-audit/03-gmail-integration-design.md) (open questions resolved 2026-05-23: single account, polling-only, heuristic-only, reuse `ConnectionManager.register_handler`).

**Sub-PR cadence (mirrors `qw-1`…`qw-7`):** `gm-1` schema → `gm-2` OAuth core → `gm-3` OAuth routes → `gm-4` heuristics → `gm-5` sync worker → `gm-6` scheduler wire-up → `gm-7` correspondence API → `gm-8` WS events + status → `gm-9` Settings Connect button → `gm-10` Inbox page → `gm-11` Application-detail Linked Emails tab → `gm-12` integration smoke test.

---

## Pre-flight: Baseline

- [ ] **Step 1: Record baseline test count**

```bash
cd /home/mouad/Web-automation
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: `315 passed` (current main baseline per `2026-05-22-audit/POST-SPRINT-VERIFICATION.md`). Save this number — every task ends with this check, adding **+N** for the new tests in that task.

- [ ] **Step 2: Record baseline pyright / svelte-check counts**

```bash
.venv/bin/pyright backend 2>&1 | tail -3
cd frontend && npm run check 2>&1 | tail -5 && cd ..
```

Expected: pyright `40 errors, 7 warnings`; svelte-check `0 errors, 1 warning`. New tasks must not regress these.

- [ ] **Step 3: Verify required env vars are wired**

```bash
grep -E "GMAIL_CLIENT_ID|GMAIL_CLIENT_SECRET" .env.example || echo "NEEDS_ADDING"
```

If output is `NEEDS_ADDING`, that's expected — Task 2 adds them.

---

## File Structure

**New backend files (10)**
- `backend/models/gmail.py` — `GmailCredential`, `GmailMessage`, `ApplicationCorrespondence`
- `backend/gmail/__init__.py` — package marker
- `backend/gmail/credentials.py` — Fernet encrypt/decrypt + load/save/delete refresh tokens
- `backend/gmail/auth.py` — `GmailTokenManager` (per-process access-token cache, refresh via `httpx`)
- `backend/gmail/client.py` — thin async Gmail REST client (`history.list`, `messages.list`, `messages.get`)
- `backend/gmail/classifier_heuristics.py` — `ATS_DOMAINS`, `REJECTION_PATTERNS`, `INTERVIEW_PATTERNS`, `OFFER_PATTERNS`, single `classify(...)` function
- `backend/gmail/sync.py` — `GmailSyncWorker.sync_now(email)` orchestrating Gmail API → DB → classifier → WS
- `backend/api/gmail_auth.py` — `/api/gmail/oauth/start`, `/callback`, `/disconnect`
- `backend/api/gmail.py` — `/api/gmail/status`, `/api/gmail/sync`
- `backend/api/correspondence.py` — `/api/correspondence/*` (list / link / unlink / unlinked)

**Modified backend files (5)**
- `backend/config.py` — add `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`, `GMAIL_BACKFILL_DAYS`
- `backend/database.py:_migrate_add_columns` — add `applications.last_correspondence_at`
- `backend/models/__init__.py` — export the three new models
- `backend/api/ws_models.py` — add `GmailSyncStatus`, `GmailMessageReceived` to `WSMessage` union
- `backend/main.py` — wire `GmailSyncWorker`, start APScheduler in lifespan, mount three new routers
- `pyproject.toml` — add `apscheduler[asyncio]>=3.10`

**New frontend files (4)**
- `frontend/src/lib/api/gmail.ts` — typed wrappers for `/api/gmail/*` and `/api/correspondence/*`
- `frontend/src/lib/components/GmailConnectCard.svelte` — Settings card with Connect / Disconnect buttons + status pill
- `frontend/src/lib/components/LinkApplicationModal.svelte` — searchable Application picker invoked from the Inbox page
- `frontend/src/routes/inbox/+page.svelte` — list-unlinked + link-to-app flow

**Modified frontend files (3)**
- `frontend/src/routes/settings/+page.svelte` — mount `<GmailConnectCard />` in a new "Integrations" tab
- `frontend/src/routes/jobs/[id]/+page.svelte` — add "Linked Emails" tab (creates the tab container if absent)
- `frontend/src/lib/types/ws.ts` (or equivalent) — extend `WSMessage` union with the two new variants

**New tests (8)**
- `tests/test_gmail_models.py` — schema roundtrip + indexes
- `tests/test_gmail_credentials.py` — Fernet encrypt/decrypt + load/save/delete
- `tests/test_gmail_auth.py` — `GmailTokenManager` cache + refresh (httpx mocked)
- `tests/test_gmail_oauth_routes.py` — `/start` 302 + state token + `/callback` token exchange + `/disconnect`
- `tests/test_gmail_classifier.py` — heuristic table + confidence thresholds
- `tests/test_gmail_sync.py` — first-run back-fill + delta sync + dedup + WS event
- `tests/test_correspondence_api.py` — list / link / unlink / unlinked + `last_correspondence_at` update
- `tests/test_gmail_smoke.py` — end-to-end (OAuth callback → sync → list unlinked → link → application detail)

---

## Task 1 — gm-1: Schema, migrations, model exports

**Files:**
- Create: `backend/models/gmail.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/database.py:_migrate_add_columns`
- Test: `tests/test_gmail_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_models.py
from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text

from backend.database import AsyncSessionLocal, engine, init_db


@pytest.fixture(autouse=True)
async def _init_db_for_each_test():
    await init_db()
    yield


async def test_gmail_tables_created():
    """init_db creates all three Gmail tables."""
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "gmail_credentials" in names
    assert "gmail_messages" in names
    assert "application_correspondence" in names


async def test_applications_last_correspondence_at_column_added():
    """_migrate_add_columns adds last_correspondence_at to existing applications table."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(applications)"))
        cols = {row[1] for row in result.fetchall()}
    assert "last_correspondence_at" in cols


async def test_gmail_credential_roundtrip():
    """A GmailCredential row roundtrips through the session."""
    from backend.models.gmail import GmailCredential

    async with AsyncSessionLocal() as session:
        row = GmailCredential(
            email_address="user@example.com",
            encrypted_refresh_token="enc-token-blob",
            scopes="https://www.googleapis.com/auth/gmail.readonly",
        )
        session.add(row)
        await session.commit()

        result = await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "user@example.com")
        )
        loaded = result.scalar_one()
        assert loaded.encrypted_refresh_token == "enc-token-blob"
        assert loaded.enabled is True
        assert loaded.history_id is None


async def test_gmail_message_unique_constraint():
    """Inserting the same gmail_message_id twice raises."""
    from sqlalchemy.exc import IntegrityError

    from backend.models.gmail import GmailMessage

    async with AsyncSessionLocal() as session:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(GmailMessage(
            gmail_message_id="m-1", gmail_thread_id="t-1",
            account_email="u@e.com", from_address="r@ats.io", from_domain="ats.io",
            received_at=now,
        ))
        await session.commit()

        session.add(GmailMessage(
            gmail_message_id="m-1", gmail_thread_id="t-1",
            account_email="u@e.com", from_address="r@ats.io", from_domain="ats.io",
            received_at=now,
        ))
        with pytest.raises(IntegrityError):
            await session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_gmail_models.py -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'GmailCredential' from 'backend.models.gmail'` (the module doesn't exist yet) or `AssertionError: 'gmail_credentials' not in ...`.

- [ ] **Step 3: Create the model file**

```python
# backend/models/gmail.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GmailCredential(Base):
    """OAuth refresh token + sync cursor for one linked inbox.

    Phase 1 ships single-account; the unique key is `email_address` so a
    future multi-account release needs no migration. Refresh tokens are
    Fernet-encrypted at rest with CREDENTIAL_KEY (mirrors SiteCredential).
    Access tokens are NEVER persisted — held in-memory by GmailTokenManager.
    """

    __tablename__ = "gmail_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_address: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String, nullable=False)
    history_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GmailMessage(Base):
    """Cached metadata for one observed Gmail message. Body NOT persisted."""

    __tablename__ = "gmail_messages"
    __table_args__ = (
        Index("ix_gmail_messages_account_received",
              "account_email", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    account_email: Mapped[str] = mapped_column(String, nullable=False)
    from_address: Mapped[str] = mapped_column(String, nullable=False)
    from_domain: Mapped[str] = mapped_column(String, index=True, nullable=False)
    to_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)

    # Phase 1 classification (heuristic-only)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    category_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classified_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ats_vendor: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Phase 2 enrichment fields — declared now so we don't migrate twice;
    # all remain NULL in Phase 1.
    extracted_company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_interview_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_salary_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_questions_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ApplicationCorrespondence(Base):
    """Association object linking an Application to a GmailMessage with link-quality metadata."""

    __tablename__ = "application_correspondence"
    __table_args__ = (
        Index("ix_application_correspondence_app_created",
              "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gmail_messages.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "inbound" | "outbound"
    link_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    link_method: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
```

- [ ] **Step 4: Export the new models**

In `backend/models/__init__.py`, add the import line **after** the existing `user` import and extend `__all__`:

```python
from backend.models.gmail import (
    GmailCredential,
    GmailMessage,
    ApplicationCorrespondence,
)
```

Append to `__all__`:

```python
    "GmailCredential",
    "GmailMessage",
    "ApplicationCorrespondence",
```

- [ ] **Step 5: Add the column migration**

In `backend/database.py:_migrate_add_columns`, extend the `migrations` list:

```python
    migrations = [
        ("search_settings", "cv_tailoring_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
        ("search_settings", "max_results_per_source", "INTEGER NOT NULL DEFAULT 20"),
        ("search_settings", "max_job_age_days", "INTEGER"),
        ("applications", "last_correspondence_at", "DATETIME"),
    ]
```

- [ ] **Step 6: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_gmail_models.py -v 2>&1 | tail -10
```

Expected: `4 passed`.

- [ ] **Step 7: Full test sweep + pyright**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
.venv/bin/pyright backend/models/gmail.py 2>&1 | tail -3
```

Expected: baseline + 4 passed (319 total); pyright clean on the new file.

- [ ] **Step 8: Commit**

```bash
git add backend/models/gmail.py backend/models/__init__.py backend/database.py tests/test_gmail_models.py
git commit -m "$(cat <<'EOF'
gm-1: gmail schema — credentials, messages, correspondence tables

Adds three SQLAlchemy models for the Gmail integration (Phase 1 spec
in docs/reports/2026-05-22-audit/03-gmail-integration-design.md §3).
Phase 2 enrichment columns are declared now so we don't migrate twice;
they stay NULL until the LLM tier ships.

Also adds applications.last_correspondence_at via the existing
_migrate_add_columns helper so the FE can sort the application list
by activity without a join.
EOF
)"
```

---

## Task 2 — gm-2: Fernet credentials helper + OAuth config

**Files:**
- Create: `backend/gmail/__init__.py` (empty)
- Create: `backend/gmail/credentials.py`
- Modify: `backend/config.py`
- Modify: `.env.example`
- Test: `tests/test_gmail_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_credentials.py
from __future__ import annotations

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import (
    delete_credential,
    encrypt_refresh_token,
    decrypt_refresh_token,
    load_credential,
    save_credential,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_fernet_roundtrip():
    blob = encrypt_refresh_token("real-google-refresh-token-1234")
    assert blob != "real-google-refresh-token-1234"  # actually encrypted
    assert decrypt_refresh_token(blob) == "real-google-refresh-token-1234"


async def test_save_and_load_credential():
    async with AsyncSessionLocal() as session:
        cred = await save_credential(
            session,
            email_address="user@example.com",
            refresh_token="rt-xyz",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        await session.commit()
        assert cred.id is not None
        assert cred.encrypted_refresh_token != "rt-xyz"

    async with AsyncSessionLocal() as session:
        loaded = await load_credential(session, "user@example.com")
        assert loaded is not None
        assert decrypt_refresh_token(loaded.encrypted_refresh_token) == "rt-xyz"


async def test_save_credential_is_upsert():
    """Calling save_credential twice for the same email rotates the token."""
    async with AsyncSessionLocal() as session:
        await save_credential(session, "u@e.com", "first", ["gmail.readonly"])
        await save_credential(session, "u@e.com", "second", ["gmail.readonly"])
        await session.commit()
        loaded = await load_credential(session, "u@e.com")
    assert decrypt_refresh_token(loaded.encrypted_refresh_token) == "second"


async def test_delete_credential():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "u@e.com", "rt", ["gmail.readonly"])
        await session.commit()
    async with AsyncSessionLocal() as session:
        removed = await delete_credential(session, "u@e.com")
        await session.commit()
        assert removed is True
        assert await load_credential(session, "u@e.com") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_gmail_credentials.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'backend.gmail'`.

- [ ] **Step 3: Create the package marker and credentials module**

```python
# backend/gmail/__init__.py
```

(file is intentionally empty)

```python
# backend/gmail/credentials.py
from __future__ import annotations

from typing import Iterable, Optional

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.gmail import GmailCredential


def _fernet() -> Fernet:
    key = settings.CREDENTIAL_KEY.get_secret_value()
    if not key:
        raise RuntimeError(
            "CREDENTIAL_KEY is not set — refusing to encrypt/decrypt Gmail refresh tokens."
        )
    return Fernet(key.encode())


def encrypt_refresh_token(refresh_token: str) -> str:
    return _fernet().encrypt(refresh_token.encode()).decode()


def decrypt_refresh_token(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()


async def save_credential(
    session: AsyncSession,
    email_address: str,
    refresh_token: str,
    scopes: Iterable[str],
) -> GmailCredential:
    """Upsert a credential row, rotating the refresh token if it already exists."""
    existing = await load_credential(session, email_address)
    encrypted = encrypt_refresh_token(refresh_token)
    scope_str = " ".join(scopes)
    if existing is None:
        row = GmailCredential(
            email_address=email_address,
            encrypted_refresh_token=encrypted,
            scopes=scope_str,
        )
        session.add(row)
        return row
    existing.encrypted_refresh_token = encrypted
    existing.scopes = scope_str
    return existing


async def load_credential(
    session: AsyncSession, email_address: str
) -> Optional[GmailCredential]:
    result = await session.execute(
        select(GmailCredential).where(GmailCredential.email_address == email_address)
    )
    return result.scalar_one_or_none()


async def delete_credential(session: AsyncSession, email_address: str) -> bool:
    row = await load_credential(session, email_address)
    if row is None:
        return False
    await session.delete(row)
    return True
```

- [ ] **Step 4: Add OAuth config fields**

In `backend/config.py`, add inside the `Settings` class (after `APPLY_TIER1_ENABLED`):

```python
    # ── Gmail integration (Phase 1) ──────────────────────────────────────
    GMAIL_CLIENT_ID: str = Field("", env="GMAIL_CLIENT_ID")
    GMAIL_CLIENT_SECRET: SecretStr = SecretStr("")
    GMAIL_REDIRECT_URI: str = Field(
        "http://localhost:8000/api/gmail/oauth/callback",
        env="GMAIL_REDIRECT_URI",
    )
    GMAIL_BACKFILL_DAYS: int = Field(30, env="GMAIL_BACKFILL_DAYS")
    GMAIL_POLL_INTERVAL_MINUTES: int = Field(5, env="GMAIL_POLL_INTERVAL_MINUTES")
```

Note: keep `GMAIL_CLIENT_ID` as a plain `str` (it's a public OAuth client id, mirrors `ADZUNA_APP_ID`); `GMAIL_CLIENT_SECRET` is `SecretStr`.

- [ ] **Step 5: Document the env vars**

In `.env.example`, append:

```
# Gmail integration — leave empty to disable. Obtain at console.cloud.google.com.
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REDIRECT_URI=http://localhost:8000/api/gmail/oauth/callback
GMAIL_BACKFILL_DAYS=30
GMAIL_POLL_INTERVAL_MINUTES=5
```

- [ ] **Step 6: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_gmail_credentials.py -v 2>&1 | tail -5
```

Expected: `4 passed`.

- [ ] **Step 7: Full sweep**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: baseline + 8 (323 total).

- [ ] **Step 8: Commit**

```bash
git add backend/gmail/__init__.py backend/gmail/credentials.py backend/config.py .env.example tests/test_gmail_credentials.py
git commit -m "$(cat <<'EOF'
gm-2: gmail credentials helper + OAuth config fields

Fernet-encrypted refresh-token store reusing CREDENTIAL_KEY (same pattern
as SiteCredential). Save is upsert so re-running the OAuth flow rotates
the token cleanly. Phase 1 design §1.2.
EOF
)"
```

---

## Task 3 — gm-3: GmailTokenManager + OAuth REST routes

**Files:**
- Create: `backend/gmail/auth.py`
- Create: `backend/api/gmail_auth.py`
- Modify: `backend/main.py` (router mount + `app.state.gmail_token_manager`)
- Test: `tests/test_gmail_auth.py`
- Test: `tests/test_gmail_oauth_routes.py`

- [ ] **Step 1: Write the failing token-manager test**

```python
# tests/test_gmail_auth.py
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.auth import GmailTokenManager
from backend.gmail.credentials import save_credential


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


def _fake_token_response(access_token: str = "ya29.abc", expires_in: int = 3600):
    return httpx.Response(
        200,
        json={"access_token": access_token, "expires_in": expires_in,
              "token_type": "Bearer", "scope": "https://www.googleapis.com/auth/gmail.readonly"},
    )


async def test_first_call_refreshes_and_caches():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "u@e.com", "rt-1", ["gmail.readonly"])
        await session.commit()

    mgr = GmailTokenManager()
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        mock_inst = MockClient.return_value.__aenter__.return_value
        mock_inst.post = AsyncMock(return_value=_fake_token_response("tok-A"))

        tok = await mgr.access_token("u@e.com")
        assert tok == "tok-A"
        # Second call inside the cache window does NOT call the token endpoint again
        tok2 = await mgr.access_token("u@e.com")
        assert tok2 == "tok-A"
        assert mock_inst.post.await_count == 1


async def test_cache_expires_and_refreshes():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "u@e.com", "rt-1", ["gmail.readonly"])
        await session.commit()

    mgr = GmailTokenManager(_clock=lambda: 1000.0)
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        mock_inst = MockClient.return_value.__aenter__.return_value
        mock_inst.post = AsyncMock(side_effect=[
            _fake_token_response("tok-A", expires_in=60),
            _fake_token_response("tok-B", expires_in=60),
        ])
        assert await mgr.access_token("u@e.com") == "tok-A"

    # Advance clock past expiry-minus-buffer (60s buffer in implementation)
    mgr._clock = lambda: 2000.0
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient2:
        mock_inst2 = MockClient2.return_value.__aenter__.return_value
        mock_inst2.post = AsyncMock(return_value=_fake_token_response("tok-B"))
        assert await mgr.access_token("u@e.com") == "tok-B"


async def test_missing_credential_raises():
    mgr = GmailTokenManager()
    with pytest.raises(KeyError):
        await mgr.access_token("nobody@example.com")
```

- [ ] **Step 2: Run test (expect import error)**

```bash
.venv/bin/pytest tests/test_gmail_auth.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'backend.gmail.auth'`.

- [ ] **Step 3: Implement `GmailTokenManager`**

```python
# backend/gmail/auth.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.gmail.credentials import decrypt_refresh_token, load_credential

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_REFRESH_BUFFER_SECONDS = 60


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # epoch seconds


class GmailTokenManager:
    """Per-process cache of {email -> access_token}; refreshes on miss / near-expiry.

    Refresh tokens stay encrypted in the DB; the access token is held in-memory
    only and never persisted (per design §1.3).
    """

    def __init__(self, _clock: Callable[[], float] = time.time) -> None:
        self._cache: dict[str, _CachedToken] = {}
        self._clock = _clock

    async def access_token(self, email: str) -> str:
        now = self._clock()
        hit = self._cache.get(email)
        if hit and hit.expires_at - _REFRESH_BUFFER_SECONDS > now:
            return hit.access_token
        return await self._refresh(email)

    async def _refresh(self, email: str) -> str:
        async with AsyncSessionLocal() as session:
            cred = await load_credential(session, email)
            if cred is None:
                raise KeyError(f"No GmailCredential row for {email!r}")
            refresh_token = decrypt_refresh_token(cred.encrypted_refresh_token)

        client_id = settings.GMAIL_CLIENT_ID
        client_secret = settings.GMAIL_CLIENT_SECRET.get_secret_value()
        if not client_id or not client_secret:
            raise RuntimeError("GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET not configured")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
        resp.raise_for_status()
        payload = resp.json()
        access = payload["access_token"]
        expires_at = self._clock() + int(payload.get("expires_in", 3600))
        self._cache[email] = _CachedToken(access_token=access, expires_at=expires_at)
        return access


async def revoke_refresh_token(refresh_token: str) -> None:
    """Best-effort revoke; swallow errors so disconnect always succeeds locally."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(REVOKE_URL, data={"token": refresh_token})
    except Exception:
        pass
```

- [ ] **Step 4: Run token-manager test**

```bash
.venv/bin/pytest tests/test_gmail_auth.py -v 2>&1 | tail -5
```

Expected: `3 passed`.

- [ ] **Step 5: Write the failing OAuth-routes test**

```python
# tests/test_gmail_oauth_routes.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def app_with_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-secret")
    # Force a fresh Settings load
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_oauth_start_redirects_to_google(app_with_gmail: TestClient):
    resp = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=test-client.apps.googleusercontent.com" in loc
    assert "access_type=offline" in loc
    assert "prompt=consent" in loc
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in loc
    assert "state=" in loc


def test_oauth_start_when_unconfigured_returns_503(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "")
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/gmail/oauth/start", follow_redirects=False)
    assert resp.status_code == 503


def _fake_token_response():
    return httpx.Response(200, json={
        "access_token": "ya29.test",
        "refresh_token": "1//refresh-test",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "token_type": "Bearer",
    })


def _fake_userinfo_response():
    return httpx.Response(200, json={"emailAddress": "user@example.com", "messagesTotal": 1234})


def test_oauth_callback_exchanges_and_persists(app_with_gmail: TestClient):
    # Step 1: trigger /start so the signed state token lands in the response Location
    start = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    # Step 2: hit /callback with the same state + a fake auth code
    with patch("backend.api.gmail_auth.httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=_fake_token_response())
        inst.get = AsyncMock(return_value=_fake_userinfo_response())
        resp = app_with_gmail.get(
            f"/api/gmail/oauth/callback?code=auth-code-xyz&state={state}",
            follow_redirects=False,
        )
    # Redirect back to the SPA settings page on success
    assert resp.status_code in (302, 303)
    assert "/settings" in resp.headers["location"]

    # Verify the credential row was persisted
    import asyncio
    from backend.database import AsyncSessionLocal
    from backend.gmail.credentials import decrypt_refresh_token, load_credential

    async def _read():
        async with AsyncSessionLocal() as session:
            return await load_credential(session, "user@example.com")
    row = asyncio.run(_read())
    assert row is not None
    assert decrypt_refresh_token(row.encrypted_refresh_token) == "1//refresh-test"


def test_oauth_callback_with_bad_state_rejects(app_with_gmail: TestClient):
    resp = app_with_gmail.get(
        "/api/gmail/oauth/callback?code=anything&state=forged-state",
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_disconnect_removes_credential(app_with_gmail: TestClient):
    import asyncio
    from backend.database import AsyncSessionLocal
    from backend.gmail.credentials import save_credential, load_credential

    async def _seed():
        async with AsyncSessionLocal() as session:
            await save_credential(session, "u@e.com", "rt", ["gmail.readonly"])
            await session.commit()
    asyncio.run(_seed())

    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=httpx.Response(200, text=""))
        resp = app_with_gmail.post("/api/gmail/disconnect", json={"email": "u@e.com"})
    assert resp.status_code == 200

    async def _read():
        async with AsyncSessionLocal() as session:
            return await load_credential(session, "u@e.com")
    assert asyncio.run(_read()) is None
```

- [ ] **Step 6: Run test (expect router missing)**

```bash
.venv/bin/pytest tests/test_gmail_oauth_routes.py -v 2>&1 | tail -10
```

Expected: 404 from the routes (router not mounted yet) or `ModuleNotFoundError`.

- [ ] **Step 7: Implement the OAuth routes**

```python
# backend/api/gmail_auth.py
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.gmail.auth import TOKEN_URL, revoke_refresh_token
from backend.gmail.credentials import (
    decrypt_refresh_token,
    delete_credential,
    load_credential,
    save_credential,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gmail", tags=["gmail"])

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
PHASE_1_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_STATE_TTL_SECONDS = 600


def _sign_state() -> str:
    """Return a state token of the form `<nonce>.<ts>.<hmac>`."""
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    key = settings.CREDENTIAL_KEY.get_secret_value().encode()
    mac = hmac.new(key, f"{nonce}.{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{ts}.{mac}"


def _verify_state(token: str) -> bool:
    try:
        nonce, ts, mac = token.split(".")
    except ValueError:
        return False
    age = time.time() - int(ts)
    if age > _STATE_TTL_SECONDS or age < 0:
        return False
    key = settings.CREDENTIAL_KEY.get_secret_value().encode()
    expected = hmac.new(key, f"{nonce}.{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def _ensure_oauth_configured() -> None:
    if not settings.GMAIL_CLIENT_ID or not settings.GMAIL_CLIENT_SECRET.get_secret_value():
        raise HTTPException(
            status_code=503,
            detail="Gmail OAuth not configured — set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env",
        )


@router.get("/oauth/start")
async def oauth_start() -> RedirectResponse:
    _ensure_oauth_configured()
    params = {
        "client_id": settings.GMAIL_CLIENT_ID,
        "redirect_uri": settings.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(PHASE_1_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": _sign_state(),
        "include_granted_scopes": "true",
    }
    return RedirectResponse(f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str, error: Optional[str] = None) -> RedirectResponse:
    if error:
        logger.warning("Gmail OAuth callback error: %s", error)
        return RedirectResponse("/settings?gmail_error=" + error, status_code=302)
    if not _verify_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    _ensure_oauth_configured()

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": settings.GMAIL_CLIENT_ID,
            "client_secret": settings.GMAIL_CLIENT_SECRET.get_secret_value(),
            "redirect_uri": settings.GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        token_resp.raise_for_status()
        token_payload = token_resp.json()
        refresh_token = token_payload.get("refresh_token")
        if not refresh_token:
            raise HTTPException(
                status_code=400,
                detail="Google did not return a refresh_token — revoke the previous grant in your Google account and retry.",
            )
        access_token = token_payload["access_token"]
        granted_scopes = token_payload.get("scope", " ".join(PHASE_1_SCOPES)).split()

        profile_resp = await client.get(
            GMAIL_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile_resp.raise_for_status()
        email_address = profile_resp.json()["emailAddress"]

    async with AsyncSessionLocal() as session:
        await save_credential(
            session,
            email_address=email_address,
            refresh_token=refresh_token,
            scopes=granted_scopes,
        )
        await session.commit()

    return RedirectResponse("/settings?gmail_connected=1", status_code=302)


class DisconnectBody(BaseModel):
    email: str


@router.post("/disconnect")
async def disconnect(body: DisconnectBody) -> dict:
    async with AsyncSessionLocal() as session:
        cred = await load_credential(session, body.email)
        rt: Optional[str] = None
        if cred is not None:
            rt = decrypt_refresh_token(cred.encrypted_refresh_token)
        removed = await delete_credential(session, body.email)
        await session.commit()
    if rt:
        await revoke_refresh_token(rt)
    return {"removed": bool(removed)}
```

- [ ] **Step 8: Mount the router in main.py**

In `backend/main.py`, inside the `try:` block that mounts API routers, add:

```python
    import backend.api.gmail_auth as gmail_auth  # type: ignore
    app.include_router(gmail_auth.router)
```

Add `GmailTokenManager` to the singleton block:

```python
        from backend.gmail.auth import GmailTokenManager
        app.state.gmail_token_manager = GmailTokenManager()
```

- [ ] **Step 9: Run OAuth-routes test**

```bash
.venv/bin/pytest tests/test_gmail_oauth_routes.py -v 2>&1 | tail -15
```

Expected: `5 passed`.

- [ ] **Step 10: Full sweep + pyright**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
.venv/bin/pyright backend/gmail backend/api/gmail_auth.py 2>&1 | tail -3
```

Expected: baseline + 16 (331 total); pyright clean on new code.

- [ ] **Step 11: Commit**

```bash
git add backend/gmail/auth.py backend/api/gmail_auth.py backend/main.py \
        tests/test_gmail_auth.py tests/test_gmail_oauth_routes.py
git commit -m "$(cat <<'EOF'
gm-3: gmail OAuth — token manager + start/callback/disconnect routes

In-memory access-token cache with 60s refresh buffer (refresh tokens stay
encrypted in the DB). CSRF state token signed with CREDENTIAL_KEY via
stdlib HMAC — no extra dep needed. Disconnect best-effort revokes upstream
before deleting the local row. Phase 1 scope is gmail.readonly only.
EOF
)"
```

---

## Task 4 — gm-4: Heuristic classifier

**Files:**
- Create: `backend/gmail/classifier_heuristics.py`
- Test: `tests/test_gmail_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_classifier.py
from __future__ import annotations

import pytest

from backend.gmail.classifier_heuristics import classify


@pytest.mark.parametrize("from_address,subject,expected_category,expected_vendor", [
    # ATS acks
    ("no-reply@greenhouse.io", "We received your application", "ats_ack", "greenhouse"),
    ("notifications@hire.lever.co", "Application received — Acme Corp", "ats_ack", "lever"),
    ("careers@acme.myworkday.com", "Application received", "ats_ack", "workday"),
    ("noreply@ashbyhq.com", "Thank you for applying", "ats_ack", "ashby"),
    # Rejections
    ("recruiter@acme.com", "Update on your application — unfortunately we have decided to proceed with other candidates",
     "rejection", None),
    ("hiring@beta.io", "Regretfully informing you", "rejection", None),
    # Interview invites
    ("recruiter@gamma.com", "Interview invitation — next steps", "interview_invite", None),
    ("hr@delta.io", "Are you available for a chat next week? calendly.com/delta-hr", "interview_invite", None),
    # Offers
    ("ceo@epsilon.io", "We are pleased to extend an offer letter", "offer", None),
    # Noise
    ("newsletter@indeed.com", "10 jobs you might like", "noise", None),
    # Unknown
    ("friend@gmail.com", "lunch tomorrow?", "unknown", None),
])
def test_classify_known_patterns(from_address, subject, expected_category, expected_vendor):
    category, confidence, vendor = classify(
        from_address=from_address, subject=subject, snippet=subject,
    )
    assert category == expected_category, f"got {category!r} (conf {confidence:.2f})"
    if expected_vendor is not None:
        assert vendor == expected_vendor


def test_confidence_capped_at_0_85():
    """Heuristic confidence never exceeds 0.85 so the Phase 2 LLM can override."""
    _, confidence, _ = classify(
        from_address="no-reply@greenhouse.io",
        subject="We received your application",
        snippet="Thanks",
    )
    assert 0.0 < confidence <= 0.85


def test_ats_vendor_alone_is_ats_ack_default():
    """If the sender is an ATS but the subject doesn't match a more specific
    pattern (rejection / interview / offer), default to 'ats_ack'."""
    cat, _, vendor = classify(
        from_address="random@boards.greenhouse.io",
        subject="Some neutral subject line",
        snippet="",
    )
    assert cat == "ats_ack"
    assert vendor == "greenhouse"


def test_rejection_pattern_beats_ats_default():
    """Rejection wording inside an ATS email is still classified as rejection."""
    cat, _, vendor = classify(
        from_address="no-reply@greenhouse.io",
        subject="Update — unfortunately we have decided to proceed with other candidates",
        snippet="Thank you for applying",
    )
    assert cat == "rejection"
    assert vendor == "greenhouse"  # vendor still extracted
```

- [ ] **Step 2: Run test (expect import error)**

```bash
.venv/bin/pytest tests/test_gmail_classifier.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the classifier**

```python
# backend/gmail/classifier_heuristics.py
"""Phase 1 heuristic classifier — zero-cost, deterministic, returns
(category, confidence ∈ (0, 0.85], ats_vendor | None)."""

from __future__ import annotations

import re
from typing import Optional

# Sender domain (or fragment) → ATS vendor name. Matches use a *contains* test
# so tenant-scoped Workday hosts like "careers.acme.myworkday.com" still hit.
ATS_DOMAINS: dict[str, str] = {
    "greenhouse-mail.io": "greenhouse",
    "greenhouse.io": "greenhouse",
    "hire.lever.co": "lever",
    "jobs.lever.co": "lever",
    "myworkday.com": "workday",
    "myworkdayjobs.com": "workday",
    "ashbyhq.com": "ashby",
    "workablemail.com": "workable",
    "workable.com": "workable",
    "smartrecruiters.com": "smartrecruiters",
    "taleo.net": "taleo",
    "icims.com": "icims",
    "bamboohr.com": "bamboohr",
}

# Newsletters / job-board digests we should never escalate.
NOISE_DOMAINS: tuple[str, ...] = (
    "newsletter@indeed.com",
    "linkedin-jobs@linkedin.com",
    "alerts@glassdoor.com",
)

REJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bnot moving forward\b",
        r"\bunfortunately\b.*\b(decided|chosen)\b",
        r"\bdecided to (proceed|move forward) with other\b",
        r"\bregret(fully)? (to )?inform\b",
        r"\bnot (the )?right fit\b",
        r"\bwon't be moving forward\b",
    ]
]
INTERVIEW_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\binterview\b.*\b(invitation|invite|scheduling)\b",
        r"\b(invitation|invite)\b.*\binterview\b",
        r"\bnext step(s)?\b.*\b(call|chat|conversation)\b",
        r"\b(would|are) you available\b",
        r"\bcalendly\.com\b",
        r"\bsavvycal\.com\b",
        r"\bcal\.com\b",
    ]
]
OFFER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\boffer letter\b",
        r"\bcompensation (package|details)\b",
        r"\bpleased to extend\b",
        r"\bwelcome aboard\b",
    ]
]

_MAX_HEURISTIC_CONFIDENCE = 0.85
_KNOWN_CATEGORIES = (
    "ats_ack", "recruiter_question", "interview_invite",
    "rejection", "offer", "scheduling", "noise", "unknown",
)


def _vendor_for(from_address: str) -> Optional[str]:
    lower = from_address.lower()
    for fragment, vendor in ATS_DOMAINS.items():
        if fragment in lower:
            return vendor
    return None


def classify(
    from_address: str, subject: Optional[str], snippet: Optional[str]
) -> tuple[str, float, Optional[str]]:
    """Return (category, confidence, ats_vendor). Confidence ∈ (0, 0.85]."""
    blob = " ".join(filter(None, [subject, snippet])).strip()
    lower_addr = from_address.lower()

    if any(d in lower_addr for d in NOISE_DOMAINS):
        return "noise", _MAX_HEURISTIC_CONFIDENCE, None

    vendor = _vendor_for(from_address)

    # Subject/body patterns trump default ATS-ack
    for pat in REJECTION_PATTERNS:
        if pat.search(blob):
            return "rejection", _MAX_HEURISTIC_CONFIDENCE, vendor
    for pat in OFFER_PATTERNS:
        if pat.search(blob):
            return "offer", _MAX_HEURISTIC_CONFIDENCE, vendor
    for pat in INTERVIEW_PATTERNS:
        if pat.search(blob):
            return "interview_invite", _MAX_HEURISTIC_CONFIDENCE, vendor

    if vendor is not None:
        return "ats_ack", 0.7, vendor

    return "unknown", 0.0, None
```

- [ ] **Step 4: Run classifier tests**

```bash
.venv/bin/pytest tests/test_gmail_classifier.py -v 2>&1 | tail -20
```

Expected: `14 passed` (11 parametrized + 3 standalone).

- [ ] **Step 5: Full sweep**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: baseline + 30 (345 total).

- [ ] **Step 6: Commit**

```bash
git add backend/gmail/classifier_heuristics.py tests/test_gmail_classifier.py
git commit -m "$(cat <<'EOF'
gm-4: gmail heuristic classifier (Phase 1 — no LLM)

ATS-vendor detection + rejection / interview / offer / noise regex
tables. Confidence capped at 0.85 so Phase 2 LLM tiers can override
deterministic guesses. Single classify() function; no class needed.
EOF
)"
```

---

## Task 5 — gm-5: Gmail REST client + GmailSyncWorker

**Files:**
- Create: `backend/gmail/client.py`
- Create: `backend/gmail/sync.py`
- Test: `tests/test_gmail_sync.py`

- [ ] **Step 1: Write the failing sync-worker test**

```python
# tests/test_gmail_sync.py
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import save_credential
from backend.gmail.sync import GmailSyncWorker
from backend.models.gmail import GmailCredential, GmailMessage
from sqlalchemy import select


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def _seed_credential(email: str = "u@e.com") -> None:
    async with AsyncSessionLocal() as session:
        await save_credential(session, email, "rt", ["gmail.readonly"])
        await session.commit()


def _msg_payload(mid: str, thread: str, sender: str, subject: str, snippet: str = "..."):
    return {
        "id": mid,
        "threadId": thread,
        "snippet": snippet,
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
            {"name": "To", "value": "user@example.com"},
        ]},
        "internalDate": "1748000000000",
    }


class _FakeClient:
    """Async-context-manager-shaped stub for backend.gmail.client.GmailRestClient."""

    def __init__(self, *, list_pages, history_pages, get_messages):
        self._list_pages = list_pages
        self._history_pages = history_pages
        self._get = get_messages
        self.history_calls: list[str] = []

    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return None

    async def messages_list(self, q: str | None = None, page_token: str | None = None):
        return self._list_pages.pop(0)

    async def history_list(self, start_history_id: str, page_token: str | None = None):
        self.history_calls.append(start_history_id)
        return self._history_pages.pop(0)

    async def messages_get(self, message_id: str):
        return self._get[message_id]


async def test_first_run_backfills_from_messages_list():
    await _seed_credential()

    fake_msgs = {
        "m1": _msg_payload("m1", "t1", "no-reply@greenhouse.io", "We received your application"),
        "m2": _msg_payload("m2", "t2", "friend@gmail.com", "lunch?"),
    }
    fake = _FakeClient(
        list_pages=[{"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": None,
                     "historyId": "12345"}],
        history_pages=[],
        get_messages=fake_msgs,
    )

    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("u@e.com")
    assert synced == 2

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(GmailMessage))).scalars().all()
        assert {r.gmail_message_id for r in rows} == {"m1", "m2"}
        m1 = next(r for r in rows if r.gmail_message_id == "m1")
        assert m1.category == "ats_ack"
        assert m1.classified_by == "heuristic"
        assert m1.from_domain == "greenhouse.io"
        cred = (await session.execute(select(GmailCredential))).scalar_one()
        assert cred.history_id == "12345"
        assert cred.last_synced_at is not None


async def test_second_run_uses_history_list_delta():
    await _seed_credential()
    # Pre-seed history_id so we go down the delta path
    async with AsyncSessionLocal() as session:
        cred = (await session.execute(select(GmailCredential))).scalar_one()
        cred.history_id = "100"
        await session.commit()

    fake = _FakeClient(
        list_pages=[],
        history_pages=[{
            "history": [{"messagesAdded": [{"message": {"id": "m3", "threadId": "t3"}}]}],
            "historyId": "150",
        }],
        get_messages={"m3": _msg_payload("m3", "t3", "recruiter@acme.com",
                                          "Interview invitation — next steps")},
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("u@e.com")
    assert synced == 1
    assert fake.history_calls == ["100"]

    async with AsyncSessionLocal() as session:
        m3 = (await session.execute(
            select(GmailMessage).where(GmailMessage.gmail_message_id == "m3")
        )).scalar_one()
        assert m3.category == "interview_invite"


async def test_dedup_via_unique_constraint_doesnt_crash():
    """Re-syncing a message we've already stored is a no-op, not a crash."""
    await _seed_credential()
    fake_msgs = {"m1": _msg_payload("m1", "t1", "no-reply@greenhouse.io", "We received your app")}
    fake_a = _FakeClient(
        list_pages=[{"messages": [{"id": "m1"}], "nextPageToken": None, "historyId": "10"}],
        history_pages=[], get_messages=fake_msgs,
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake_a):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        await worker.sync_now("u@e.com")

    # Pretend Gmail re-served the same message via history.list (it does this when
    # labels change). The second sync MUST swallow the IntegrityError.
    fake_b = _FakeClient(
        list_pages=[],
        history_pages=[{"history": [{"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]}],
                        "historyId": "20"}],
        get_messages=fake_msgs,
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake_b):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("u@e.com")
    assert synced == 0  # zero NEW rows


async def test_sync_skipped_when_credential_disabled():
    await _seed_credential()
    async with AsyncSessionLocal() as session:
        cred = (await session.execute(select(GmailCredential))).scalar_one()
        cred.enabled = False
        await session.commit()

    fake = _FakeClient(list_pages=[], history_pages=[], get_messages={})
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock())
        synced = await worker.sync_now("u@e.com")
    assert synced == 0
```

- [ ] **Step 2: Run test (expect import error)**

```bash
.venv/bin/pytest tests/test_gmail_sync.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'backend.gmail.sync'`.

- [ ] **Step 3: Implement the Gmail REST client**

```python
# backend/gmail/client.py
from __future__ import annotations

from typing import Any, Optional

import httpx

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailRestClient:
    """Tiny async wrapper around the three Gmail REST endpoints Phase 1 needs."""

    def __init__(self, access_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "GmailRestClient":
        self._client = httpx.AsyncClient(timeout=30.0, headers=self._headers)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def messages_list(
        self, q: Optional[str] = None, page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        assert self._client is not None
        params: dict[str, Any] = {"maxResults": 100}
        if q:
            params["q"] = q
        if page_token:
            params["pageToken"] = page_token
        resp = await self._client.get(f"{GMAIL_BASE}/messages", params=params)
        resp.raise_for_status()
        return resp.json()

    async def history_list(
        self, start_history_id: str, page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        assert self._client is not None
        params: dict[str, Any] = {"startHistoryId": start_history_id,
                                   "historyTypes": ["messageAdded"]}
        if page_token:
            params["pageToken"] = page_token
        resp = await self._client.get(f"{GMAIL_BASE}/history", params=params)
        resp.raise_for_status()
        return resp.json()

    async def messages_get(self, message_id: str) -> dict[str, Any]:
        assert self._client is not None
        # metadata format is cheap (5 quota units) and gives us headers + snippet
        resp = await self._client.get(
            f"{GMAIL_BASE}/messages/{message_id}",
            params={"format": "metadata",
                    "metadataHeaders": ["From", "To", "Subject", "Date"]},
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Implement the sync worker**

```python
# backend/gmail/sync.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.gmail.auth import GmailTokenManager
from backend.gmail.classifier_heuristics import classify
from backend.gmail.client import GmailRestClient
from backend.gmail.credentials import load_credential
from backend.models.gmail import GmailMessage

logger = logging.getLogger(__name__)

_CONCURRENCY = 10


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _header(headers: list[dict], name: str) -> Optional[str]:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value")
    return None


def _domain_of(addr: str) -> str:
    if "<" in addr and ">" in addr:
        addr = addr.split("<", 1)[1].split(">", 1)[0]
    return addr.split("@")[-1].strip().lower()


def _parse_date(value: Optional[str], fallback_ms: Optional[str]) -> datetime:
    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
    if fallback_ms:
        try:
            return datetime.fromtimestamp(int(fallback_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return _now()


class GmailSyncWorker:
    """One worker per process — `sync_now(email)` is idempotent and re-entrant-safe per account."""

    def __init__(self, token_manager: GmailTokenManager) -> None:
        self._tokens = token_manager
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(_CONCURRENCY)

    def _lock_for(self, email: str) -> asyncio.Lock:
        return self._locks.setdefault(email, asyncio.Lock())

    async def sync_now(self, email: str) -> int:
        """Return the number of NEW rows inserted (after dedup)."""
        async with self._lock_for(email):
            return await self._sync_locked(email)

    async def _sync_locked(self, email: str) -> int:
        async with AsyncSessionLocal() as session:
            cred = await load_credential(session, email)
            if cred is None or not cred.enabled:
                return 0
            start_history_id = cred.history_id

        access = await self._tokens.access_token(email)

        async with GmailRestClient(access) as client:
            if start_history_id is None:
                msg_ids, new_history_id = await self._first_run_ids(client)
            else:
                msg_ids, new_history_id = await self._delta_ids(client, start_history_id)

            if not msg_ids:
                await self._update_cursor(email, new_history_id)
                return 0

            payloads = await asyncio.gather(*(self._safe_get(client, mid) for mid in msg_ids))

        inserted = 0
        for payload in payloads:
            if payload is None:
                continue
            if await self._persist_one(email, payload):
                inserted += 1

        await self._update_cursor(email, new_history_id)
        return inserted

    async def _first_run_ids(
        self, client: GmailRestClient
    ) -> tuple[list[str], Optional[str]]:
        ids: list[str] = []
        latest_history: Optional[str] = None
        page_token: Optional[str] = None
        q = f"newer_than:{settings.GMAIL_BACKFILL_DAYS}d category:primary"
        while True:
            page = await client.messages_list(q=q, page_token=page_token)
            ids.extend(m["id"] for m in page.get("messages", []))
            latest_history = page.get("historyId", latest_history)
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return ids, latest_history

    async def _delta_ids(
        self, client: GmailRestClient, start: str
    ) -> tuple[list[str], Optional[str]]:
        ids: list[str] = []
        latest_history: Optional[str] = start
        page_token: Optional[str] = None
        while True:
            page = await client.history_list(start, page_token=page_token)
            for entry in page.get("history", []):
                for added in entry.get("messagesAdded", []):
                    msg = added.get("message") or {}
                    if msg.get("id"):
                        ids.append(msg["id"])
            latest_history = page.get("historyId", latest_history)
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return ids, latest_history

    async def _safe_get(self, client: GmailRestClient, mid: str) -> Optional[dict[str, Any]]:
        async with self._semaphore:
            try:
                return await client.messages_get(mid)
            except Exception as exc:
                logger.warning("messages.get(%s) failed: %s", mid, exc)
                return None

    async def _persist_one(self, account_email: str, payload: dict[str, Any]) -> bool:
        headers = (payload.get("payload") or {}).get("headers") or []
        from_address = _header(headers, "From") or ""
        subject = _header(headers, "Subject")
        snippet = payload.get("snippet")
        received_at = _parse_date(
            _header(headers, "Date"), payload.get("internalDate")
        )

        category, confidence, vendor = classify(from_address, subject, snippet)

        row = GmailMessage(
            gmail_message_id=payload["id"],
            gmail_thread_id=payload.get("threadId", payload["id"]),
            account_email=account_email,
            from_address=from_address,
            from_domain=_domain_of(from_address),
            to_address=_header(headers, "To"),
            subject=subject,
            snippet=snippet,
            received_at=received_at,
            category=category,
            category_confidence=confidence,
            classified_by="heuristic",
            ats_vendor=vendor,
        )
        async with AsyncSessionLocal() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True

    async def _update_cursor(self, email: str, new_history_id: Optional[str]) -> None:
        if not new_history_id:
            return
        async with AsyncSessionLocal() as session:
            cred = await load_credential(session, email)
            if cred is None:
                return
            cred.history_id = new_history_id
            cred.last_synced_at = _now()
            await session.commit()
```

- [ ] **Step 5: Run sync-worker tests**

```bash
.venv/bin/pytest tests/test_gmail_sync.py -v 2>&1 | tail -15
```

Expected: `4 passed`.

- [ ] **Step 6: Full sweep + pyright**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
.venv/bin/pyright backend/gmail/sync.py backend/gmail/client.py 2>&1 | tail -3
```

Expected: baseline + 34 (349 total).

- [ ] **Step 7: Commit**

```bash
git add backend/gmail/client.py backend/gmail/sync.py tests/test_gmail_sync.py
git commit -m "$(cat <<'EOF'
gm-5: gmail sync worker — first-run backfill + history.list delta

Per-account asyncio lock prevents concurrent syncs from stepping on
each other; per-process semaphore (10) bounds outbound API concurrency.
Unique constraint on gmail_message_id makes dedup automatic — we swallow
the IntegrityError and report 0 new rows. Naive UTC datetimes throughout.
EOF
)"
```

---

## Task 6 — gm-6: APScheduler wire-up

**Files:**
- Modify: `pyproject.toml`
- Modify: `backend/main.py` (lifespan)
- Test: `tests/test_gmail_scheduler.py`

- [ ] **Step 1: Add APScheduler dep**

In `pyproject.toml`, append to `dependencies`:

```toml
    "apscheduler>=3.10",
```

Install:

```bash
.venv/bin/python -m pip install 'apscheduler>=3.10'
```

- [ ] **Step 2: Write the failing scheduler test**

```python
# tests/test_gmail_scheduler.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import save_credential


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_scheduler_invokes_sync_for_every_enabled_credential():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "a@e.com", "rt1", ["gmail.readonly"])
        await save_credential(session, "b@e.com", "rt2", ["gmail.readonly"])
        await session.commit()

    mock_sync = AsyncMock(return_value=2)
    with patch("backend.main.GmailSyncWorker") as Worker:
        Worker.return_value.sync_now = mock_sync
        from backend.main import _run_gmail_poll  # added in this task
        await _run_gmail_poll()

    assert {c.args[0] for c in mock_sync.await_args_list} == {"a@e.com", "b@e.com"}


async def test_scheduler_skips_disabled_credentials():
    async with AsyncSessionLocal() as session:
        cred = await save_credential(session, "a@e.com", "rt", ["gmail.readonly"])
        cred.enabled = False
        await session.commit()

    mock_sync = AsyncMock()
    with patch("backend.main.GmailSyncWorker") as Worker:
        Worker.return_value.sync_now = mock_sync
        from backend.main import _run_gmail_poll
        await _run_gmail_poll()
    mock_sync.assert_not_awaited()
```

- [ ] **Step 3: Run test (expect missing symbol)**

```bash
.venv/bin/pytest tests/test_gmail_scheduler.py -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name '_run_gmail_poll' from 'backend.main'`.

- [ ] **Step 4: Add the poll helper + scheduler to `main.py`**

In `backend/main.py`, at module level (above `lifespan`):

```python
async def _run_gmail_poll() -> None:
    """Cron entrypoint — iterate enabled credentials, run sync for each."""
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.gmail.sync import GmailSyncWorker
    from backend.models.gmail import GmailCredential

    token_mgr = getattr(app.state, "gmail_token_manager", None)
    if token_mgr is None:
        return
    worker = GmailSyncWorker(token_manager=token_mgr)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(GmailCredential).where(GmailCredential.enabled.is_(True))
        )).scalars().all()
        emails = [r.email_address for r in rows]
    for email in emails:
        try:
            await worker.sync_now(email)
        except Exception:
            logger.exception("Gmail poll failed for %s", email)
```

Inside `lifespan`, after the WS handler registration block, add:

```python
    # ── APScheduler: poll Gmail every N minutes (Phase 1) ─────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler()
        interval = max(1, int(settings.GMAIL_POLL_INTERVAL_MINUTES))
        scheduler.add_job(_run_gmail_poll, "interval", minutes=interval, id="gmail_poll")
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Gmail poller scheduled every %d minute(s)", interval)
    except Exception as exc:
        logger.warning("Could not start Gmail scheduler: %s", exc, exc_info=True)
```

And in the shutdown block (after `yield`):

```python
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
```

- [ ] **Step 5: Run scheduler tests**

```bash
.venv/bin/pytest tests/test_gmail_scheduler.py -v 2>&1 | tail -10
```

Expected: `2 passed`.

- [ ] **Step 6: Smoke-test startup with scheduler**

```bash
.venv/bin/pytest tests/test_smoke.py tests/test_health.py -v 2>&1 | tail -5
```

Expected: still green.

- [ ] **Step 7: Full sweep**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: baseline + 36 (351 total).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml backend/main.py tests/test_gmail_scheduler.py
git commit -m "$(cat <<'EOF'
gm-6: apscheduler — poll every GMAIL_POLL_INTERVAL_MINUTES (default 5)

First real AsyncIOScheduler.start() in the repo (the morning_batch.py
import was inert scaffolding — removed in NM-01). Iterates ENABLED
credentials each tick; per-account failures are logged and don't break
the cycle. Shutdown is wait=False so the lifespan doesn't hang.
EOF
)"
```

---

## Task 7 — gm-7: Correspondence + Gmail-status REST API

**Files:**
- Create: `backend/api/correspondence.py`
- Create: `backend/api/gmail.py`
- Modify: `backend/main.py` (mount the two new routers)
- Test: `tests/test_correspondence_api.py`

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_correspondence_api.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal, init_db
from backend.models.application import Application
from backend.models.gmail import GmailMessage


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_app() -> int:
    async with AsyncSessionLocal() as session:
        app = Application(method="manual", status="applied", applied_at=_now())
        session.add(app)
        await session.commit()
        return app.id


async def _seed_msg(mid: str = "m1", category: str = "ats_ack") -> int:
    async with AsyncSessionLocal() as session:
        msg = GmailMessage(
            gmail_message_id=mid, gmail_thread_id=f"t-{mid}",
            account_email="u@e.com", from_address="no-reply@greenhouse.io",
            from_domain="greenhouse.io", subject="thanks", snippet="...",
            received_at=_now(), category=category, category_confidence=0.7,
            classified_by="heuristic",
        )
        session.add(msg)
        await session.commit()
        return msg.id


def test_unlinked_returns_non_noise_messages_without_link(test_app: TestClient):
    asyncio.run(_seed_msg("m1", category="ats_ack"))
    asyncio.run(_seed_msg("m-noise", category="noise"))
    resp = test_app.get("/api/correspondence/unlinked")
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {it["gmail_message_id"] for it in items}
    assert "m1" in ids
    assert "m-noise" not in ids


def test_link_creates_row_and_updates_application_timestamp(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("m2"))

    resp = test_app.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_id,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["confirmed_by_user"] is True
    assert body["link_method"] == "manual"

    # Verify last_correspondence_at was bumped
    async def _read():
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            return (await session.execute(
                select(Application).where(Application.id == app_id)
            )).scalar_one().last_correspondence_at
    assert asyncio.run(_read()) is not None


def test_list_for_application_returns_oldest_first(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    m1 = asyncio.run(_seed_msg("m-a"))
    m2 = asyncio.run(_seed_msg("m-b"))
    for mid in (m1, m2):
        r = test_app.post("/api/correspondence/link", json={
            "application_id": app_id, "gmail_message_id": mid,
        })
        assert r.status_code == 201

    resp = test_app.get(f"/api/correspondence/{app_id}")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["gmail_message_id"] for m in msgs] == ["m-a", "m-b"]


def test_unlink_removes_row(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("m-c"))
    link = test_app.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_id,
    })
    link_id = link.json()["id"]
    resp = test_app.delete(f"/api/correspondence/{link_id}")
    assert resp.status_code == 204

    listing = test_app.get(f"/api/correspondence/{app_id}")
    assert listing.json()["messages"] == []


def test_gmail_status_returns_not_connected_when_no_credential(test_app: TestClient):
    resp = test_app.get("/api/gmail/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["email_address"] is None
```

- [ ] **Step 2: Run test (expect 404s)**

```bash
.venv/bin/pytest tests/test_correspondence_api.py -v 2>&1 | tail -15
```

Expected: 404 / ModuleNotFoundError.

- [ ] **Step 3: Implement the correspondence router**

```python
# backend/api/correspondence.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, not_, select
from sqlalchemy.exc import IntegrityError

from backend.api.deps import DBSession
from backend.models.application import Application
from backend.models.gmail import ApplicationCorrespondence, GmailMessage

router = APIRouter(prefix="/api/correspondence", tags=["correspondence"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CorrespondenceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    subject: Optional[str]
    snippet: Optional[str]
    received_at: datetime
    category: Optional[str]
    category_confidence: Optional[float]


class UnlinkedItemOut(CorrespondenceItemOut):
    pass


class CorrespondenceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    application_id: int
    gmail_message_id: int
    gmail_thread_id: str
    direction: str
    link_confidence: float
    link_method: str
    confirmed_by_user: bool


class CorrespondenceThreadOut(BaseModel):
    application_id: int
    messages: list[CorrespondenceItemOut]


class UnlinkedListOut(BaseModel):
    items: list[UnlinkedItemOut]


class LinkBody(BaseModel):
    application_id: int
    gmail_message_id: int


@router.get("/unlinked", response_model=UnlinkedListOut)
async def list_unlinked(db: DBSession) -> UnlinkedListOut:
    linked_subq = select(ApplicationCorrespondence.message_id)
    stmt = (
        select(GmailMessage)
        .where(and_(
            not_(GmailMessage.id.in_(linked_subq)),
            GmailMessage.category != "noise",
        ))
        .order_by(GmailMessage.received_at.desc())
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return UnlinkedListOut(items=[UnlinkedItemOut.model_validate(r) for r in rows])


@router.get("/{application_id}", response_model=CorrespondenceThreadOut)
async def list_for_application(application_id: int, db: DBSession) -> CorrespondenceThreadOut:
    stmt = (
        select(GmailMessage)
        .join(ApplicationCorrespondence,
              ApplicationCorrespondence.message_id == GmailMessage.id)
        .where(ApplicationCorrespondence.application_id == application_id)
        .order_by(GmailMessage.received_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return CorrespondenceThreadOut(
        application_id=application_id,
        messages=[CorrespondenceItemOut.model_validate(r) for r in rows],
    )


@router.post("/link", response_model=CorrespondenceLinkOut, status_code=201)
async def link(body: LinkBody, db: DBSession) -> CorrespondenceLinkOut:
    app = (await db.execute(
        select(Application).where(Application.id == body.application_id)
    )).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "application not found")

    msg = (await db.execute(
        select(GmailMessage).where(GmailMessage.id == body.gmail_message_id)
    )).scalar_one_or_none()
    if msg is None:
        raise HTTPException(404, "gmail_message not found")

    link_row = ApplicationCorrespondence(
        application_id=body.application_id,
        message_id=body.gmail_message_id,
        gmail_thread_id=msg.gmail_thread_id,
        direction="inbound",
        link_confidence=1.0,
        link_method="manual",
        confirmed_by_user=True,
    )
    db.add(link_row)
    app.last_correspondence_at = _now()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "link already exists")
    await db.refresh(link_row)
    return CorrespondenceLinkOut.model_validate(link_row)


@router.delete("/{link_id}", status_code=204, response_class=Response)
async def unlink(link_id: int, db: DBSession) -> Response:
    row = (await db.execute(
        select(ApplicationCorrespondence).where(ApplicationCorrespondence.id == link_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "link not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Implement the Gmail status/sync router**

```python
# backend/api/gmail.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.models.gmail import GmailCredential, GmailMessage

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


class GmailStatusOut(BaseModel):
    connected: bool
    email_address: Optional[str]
    last_synced_at: Optional[str]
    history_id: Optional[str]
    message_count: int
    enabled: bool


@router.get("/status", response_model=GmailStatusOut)
async def status(db: DBSession) -> GmailStatusOut:
    cred = (await db.execute(select(GmailCredential).limit(1))).scalar_one_or_none()
    if cred is None:
        return GmailStatusOut(
            connected=False, email_address=None,
            last_synced_at=None, history_id=None,
            message_count=0, enabled=False,
        )
    count = (await db.execute(
        select(func.count(GmailMessage.id)).where(GmailMessage.account_email == cred.email_address)
    )).scalar_one()
    return GmailStatusOut(
        connected=True,
        email_address=cred.email_address,
        last_synced_at=cred.last_synced_at.isoformat() if cred.last_synced_at else None,
        history_id=cred.history_id,
        message_count=int(count),
        enabled=cred.enabled,
    )


class SyncOut(BaseModel):
    synced: int


@router.post("/sync", response_model=SyncOut)
async def sync_now(request: Request, db: DBSession) -> SyncOut:
    """Force a sync pass for the connected account. Power-user / debug."""
    cred = (await db.execute(select(GmailCredential).limit(1))).scalar_one_or_none()
    if cred is None:
        raise HTTPException(404, "no gmail account connected")
    token_mgr = getattr(request.app.state, "gmail_token_manager", None)
    if token_mgr is None:
        raise HTTPException(503, "gmail integration not initialised")
    from backend.gmail.sync import GmailSyncWorker
    worker = GmailSyncWorker(token_manager=token_mgr)
    n = await worker.sync_now(cred.email_address)
    return SyncOut(synced=n)
```

- [ ] **Step 5: Mount both routers**

In `backend/main.py`, inside the same `try:` block that mounts existing routers:

```python
    import backend.api.gmail as gmail  # type: ignore
    import backend.api.correspondence as correspondence  # type: ignore
    app.include_router(gmail.router)
    app.include_router(correspondence.router)
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_correspondence_api.py -v 2>&1 | tail -15
```

Expected: `5 passed`.

- [ ] **Step 7: Full sweep**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: baseline + 41 (356 total).

- [ ] **Step 8: Commit**

```bash
git add backend/api/correspondence.py backend/api/gmail.py backend/main.py tests/test_correspondence_api.py
git commit -m "$(cat <<'EOF'
gm-7: correspondence REST + gmail status/sync routes

GET /api/correspondence/{application_id}    list linked, oldest-first
GET /api/correspondence/unlinked            non-noise unlinked messages
POST /api/correspondence/link               manual link (sets confirmed_by_user)
DELETE /api/correspondence/{id}             unlink

GET /api/gmail/status     sync state for the FE Settings card
POST /api/gmail/sync      power-user force-sync
EOF
)"
```

---

## Task 8 — gm-8: WS events + broadcast helpers

**Files:**
- Modify: `backend/api/ws_models.py`
- Modify: `backend/api/ws.py`
- Modify: `backend/gmail/sync.py` (call broadcast helpers)
- Test: `tests/test_gmail_ws.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_ws.py
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import save_credential


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_sync_broadcasts_message_received_per_new_row():
    from backend.gmail.sync import GmailSyncWorker

    async with AsyncSessionLocal() as session:
        await save_credential(session, "u@e.com", "rt", ["gmail.readonly"])
        await session.commit()

    fake_msgs = {
        "m1": {
            "id": "m1", "threadId": "t1", "snippet": "hi",
            "payload": {"headers": [
                {"name": "From", "value": "no-reply@greenhouse.io"},
                {"name": "Subject", "value": "We received your application"},
                {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
            ]},
            "internalDate": "1748000000000",
        },
    }

    class _Fake:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def messages_list(self, **kw):
            return {"messages": [{"id": "m1"}], "historyId": "1"}
        async def history_list(self, *a, **kw): return {"history": []}
        async def messages_get(self, mid): return fake_msgs[mid]

    sent: list = []
    async def fake_broadcast(msg):
        sent.append(msg)

    with patch("backend.gmail.sync.GmailRestClient", return_value=_Fake()), \
         patch("backend.gmail.sync.broadcast_gmail_message_received", side_effect=fake_broadcast), \
         patch("backend.gmail.sync.broadcast_gmail_sync_status", side_effect=fake_broadcast):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        await worker.sync_now("u@e.com")

    # At least one "message_received" + at least one "sync_status"
    types = [getattr(s, "type", None) for s in sent]
    assert "gmail_message_received" in types
    assert "gmail_sync_status" in types


def test_ws_models_union_includes_gmail_variants():
    from backend.api.ws_models import GmailMessageReceived, GmailSyncStatus, WSMessage  # noqa: F401
    # If they import without error and have the right discriminator, we're good.
    inst = GmailSyncStatus(last_history_id="1", messages_synced=0, progress=0.0)
    assert inst.type == "gmail_sync_status"
    inst2 = GmailMessageReceived(
        gmail_message_id="m1", from_address="x@y.com",
        subject="s", category="ats_ack", category_confidence=0.7,
    )
    assert inst2.type == "gmail_message_received"
```

- [ ] **Step 2: Run test (expect import error)**

```bash
.venv/bin/pytest tests/test_gmail_ws.py -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'GmailSyncStatus'`.

- [ ] **Step 3: Add the two variants to `ws_models.py`**

In `backend/api/ws_models.py`, after `CaptchaResolved`:

```python
class GmailSyncStatus(BaseModel):
    type: Literal["gmail_sync_status"] = "gmail_sync_status"
    last_history_id: str | None = None
    messages_synced: int = 0
    progress: float = 0.0


class GmailMessageReceived(BaseModel):
    type: Literal["gmail_message_received"] = "gmail_message_received"
    gmail_message_id: str
    from_address: str
    subject: str | None = None
    category: str | None = None
    category_confidence: float | None = None
    linked_application_id: int | None = None
    link_confidence: float | None = None
```

Extend the `WSMessage` Union to include them (add inside `Union[...]`):

```python
        GmailSyncStatus,
        GmailMessageReceived,
```

And add to `__all__`:

```python
    "GmailSyncStatus",
    "GmailMessageReceived",
```

- [ ] **Step 4: Add broadcast helpers to `ws.py`**

At the bottom of `backend/api/ws.py`, before `__all__`:

```python
async def broadcast_gmail_sync_status(
    messages_synced: int, progress: float, last_history_id: Optional[str] = None
) -> None:
    from backend.api.ws_models import GmailSyncStatus  # local import for stub fallback
    await manager.broadcast(GmailSyncStatus(
        last_history_id=last_history_id,
        messages_synced=messages_synced,
        progress=progress,
    ))


async def broadcast_gmail_message_received(
    gmail_message_id: str, from_address: str, subject: str | None,
    category: str | None, category_confidence: float | None,
) -> None:
    from backend.api.ws_models import GmailMessageReceived
    await manager.broadcast(GmailMessageReceived(
        gmail_message_id=gmail_message_id, from_address=from_address,
        subject=subject, category=category, category_confidence=category_confidence,
    ))
```

Add the typing import if absent: `from typing import Optional`.

Extend `__all__`:

```python
__all__ = [
    "ConnectionManager", "manager", "router",
    "broadcast_status", "broadcast_job_assessment",
    "broadcast_gmail_sync_status", "broadcast_gmail_message_received",
]
```

- [ ] **Step 5: Call the broadcasters from the sync worker**

In `backend/gmail/sync.py`, add imports at the top:

```python
try:
    from backend.api.ws import (
        broadcast_gmail_message_received, broadcast_gmail_sync_status,
    )
except Exception:
    async def broadcast_gmail_sync_status(*a, **k) -> None: ...
    async def broadcast_gmail_message_received(*a, **k) -> None: ...
```

In `_persist_one`, when `inserted` is `True` (i.e., right before `return True`), broadcast:

```python
        await broadcast_gmail_message_received(
            gmail_message_id=row.gmail_message_id,
            from_address=row.from_address,
            subject=row.subject,
            category=row.category,
            category_confidence=row.category_confidence,
        )
```

And in `_sync_locked`, after the per-message gather loop, broadcast a status:

```python
        await broadcast_gmail_sync_status(
            messages_synced=inserted, progress=1.0, last_history_id=new_history_id,
        )
```

- [ ] **Step 6: Run WS tests**

```bash
.venv/bin/pytest tests/test_gmail_ws.py -v 2>&1 | tail -10
```

Expected: `2 passed`.

- [ ] **Step 7: Re-run the sync test (still green after broadcasters injected)**

```bash
.venv/bin/pytest tests/test_gmail_sync.py -v 2>&1 | tail -10
```

Expected: still `4 passed`.

- [ ] **Step 8: Full sweep**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
```

Expected: baseline + 43 (358 total).

- [ ] **Step 9: Commit**

```bash
git add backend/api/ws_models.py backend/api/ws.py backend/gmail/sync.py tests/test_gmail_ws.py
git commit -m "$(cat <<'EOF'
gm-8: gmail WS events — sync_status + message_received

Two new variants in the WSMessage discriminated union. Sync worker
broadcasts message_received per new row + sync_status at end of pass.
Import is wrapped in try/except so the worker still works in test/CLI
contexts where ws.py isn't fully importable.
EOF
)"
```

---

## Task 9 — gm-9: Frontend Settings Connect card

**Files:**
- Create: `frontend/src/lib/api/gmail.ts`
- Create: `frontend/src/lib/components/GmailConnectCard.svelte`
- Modify: `frontend/src/routes/settings/+page.svelte`

> **Note on frontend testing:** This repo has no Svelte unit-test runner; the gate is `svelte-check` + manual verification. Tasks 9–11 each finish with a `svelte-check` pass + a brief manual smoke list rather than an automated test.

- [ ] **Step 1: Add the typed API client**

```typescript
// frontend/src/lib/api/gmail.ts
export type GmailStatus = {
  connected: boolean;
  email_address: string | null;
  last_synced_at: string | null;
  history_id: string | null;
  message_count: number;
  enabled: boolean;
};

export async function fetchGmailStatus(): Promise<GmailStatus> {
  const r = await fetch("/api/gmail/status");
  if (!r.ok) throw new Error(`gmail status ${r.status}`);
  return r.json();
}

export async function forceSync(): Promise<{ synced: number }> {
  const r = await fetch("/api/gmail/sync", { method: "POST" });
  if (!r.ok) throw new Error(`gmail sync ${r.status}`);
  return r.json();
}

export async function disconnect(email: string): Promise<void> {
  const r = await fetch("/api/gmail/disconnect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!r.ok) throw new Error(`gmail disconnect ${r.status}`);
}

export type UnlinkedItem = {
  id: number;
  gmail_message_id: string;
  gmail_thread_id: string;
  from_address: string;
  subject: string | null;
  snippet: string | null;
  received_at: string;
  category: string | null;
  category_confidence: number | null;
};

export async function fetchUnlinked(): Promise<UnlinkedItem[]> {
  const r = await fetch("/api/correspondence/unlinked");
  if (!r.ok) throw new Error(`unlinked ${r.status}`);
  const body = await r.json();
  return body.items;
}

export async function linkMessage(application_id: number, gmail_message_id: number) {
  const r = await fetch("/api/correspondence/link", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ application_id, gmail_message_id }),
  });
  if (!r.ok) throw new Error(`link ${r.status}`);
  return r.json();
}

export async function fetchThread(application_id: number) {
  const r = await fetch(`/api/correspondence/${application_id}`);
  if (!r.ok) throw new Error(`thread ${r.status}`);
  return r.json();
}
```

- [ ] **Step 2: Build the Connect card component**

```svelte
<!-- frontend/src/lib/components/GmailConnectCard.svelte -->
<script lang="ts">
  import { onMount } from "svelte";
  import { disconnect, fetchGmailStatus, forceSync, type GmailStatus } from "$lib/api/gmail";

  let status: GmailStatus | null = $state(null);
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function refresh() {
    try {
      status = await fetchGmailStatus();
    } catch (e) {
      error = (e as Error).message;
    }
  }

  onMount(refresh);

  function connect() {
    window.location.href = "/api/gmail/oauth/start";
  }

  async function doDisconnect() {
    if (!status?.email_address) return;
    if (!confirm(`Disconnect ${status.email_address}?`)) return;
    busy = true;
    try {
      await disconnect(status.email_address);
      await refresh();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function doSync() {
    busy = true;
    try {
      const { synced } = await forceSync();
      alert(`Synced ${synced} new message(s)`);
      await refresh();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-4 space-y-3">
  <div class="flex items-center justify-between">
    <h3 class="text-base font-semibold text-slate-100">Gmail</h3>
    {#if status?.connected}
      <span class="text-xs px-2 py-1 rounded bg-emerald-700/40 text-emerald-200">
        Connected
      </span>
    {:else}
      <span class="text-xs px-2 py-1 rounded bg-slate-700/40 text-slate-300">
        Not connected
      </span>
    {/if}
  </div>

  {#if error}
    <p class="text-sm text-rose-300">{error}</p>
  {/if}

  {#if status?.connected}
    <p class="text-sm text-slate-300">
      <strong>{status.email_address}</strong> · {status.message_count} message(s) cached
      {#if status.last_synced_at}
        · last sync {new Date(status.last_synced_at).toLocaleString()}
      {/if}
    </p>
    <div class="flex gap-2">
      <button class="btn btn-secondary" disabled={busy} onclick={doSync}>Sync now</button>
      <button class="btn btn-danger" disabled={busy} onclick={doDisconnect}>Disconnect</button>
    </div>
  {:else}
    <p class="text-sm text-slate-400">
      Connect your Gmail to surface recruiter emails alongside your applications.
      Read-only access (gmail.readonly scope). Refresh tokens are encrypted at rest.
    </p>
    <button class="btn btn-primary" onclick={connect}>Connect Gmail</button>
  {/if}
</div>
```

- [ ] **Step 3: Mount the card on the Settings page**

In `frontend/src/routes/settings/+page.svelte`, find the existing tab strip (look for `let activeTab` or similar) and add an "Integrations" tab. In its body:

```svelte
{#if activeTab === "integrations"}
  <div class="space-y-4 max-w-2xl">
    <GmailConnectCard />
  </div>
{/if}
```

Add the import at the top:

```svelte
import GmailConnectCard from "$lib/components/GmailConnectCard.svelte";
```

Add the tab button alongside the existing tabs (style-match what's already there).

- [ ] **Step 4: Verify the build**

```bash
cd frontend && npm run check 2>&1 | tail -10 && cd ..
```

Expected: 0 errors (warnings allowed). If a Svelte 5 rune syntax error pops up, the component must use the existing project's idioms — read `frontend/src/lib/components/CVReviewPanel.svelte` to mirror.

- [ ] **Step 5: Manual smoke (record outcome in commit body)**

1. `cd frontend && npm run dev` and load `http://localhost:5173/settings`.
2. Click the new Integrations tab → see "Not connected" + "Connect Gmail" button.
3. Set fake `GMAIL_CLIENT_ID` in `.env`, restart backend → button should redirect to Google (will 400 because the client id is fake, but the redirect proves the route works).
4. Manually `INSERT` a `gmail_credentials` row (sqlite cli) → reload page → see "Connected" pill, email, message count.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api/gmail.ts frontend/src/lib/components/GmailConnectCard.svelte \
        frontend/src/routes/settings/+page.svelte
git commit -m "$(cat <<'EOF'
gm-9: Settings — Gmail Connect card (Integrations tab)

Status pill / Connect / Sync now / Disconnect. All in one card; no full
tab takeover. Typed API wrappers in $lib/api/gmail.ts are reused by the
Inbox page in gm-10.

Manual verified: status reflects DB state; disconnect revokes upstream
and clears the row; Sync now reports inserted count.
EOF
)"
```

---

## Task 10 — gm-10: Inbox page (manual link UI)

**Files:**
- Create: `frontend/src/lib/components/LinkApplicationModal.svelte`
- Create: `frontend/src/routes/inbox/+page.svelte`

- [ ] **Step 1: Build the Link-to-Application modal**

```svelte
<!-- frontend/src/lib/components/LinkApplicationModal.svelte -->
<script lang="ts">
  import { onMount } from "svelte";

  type Application = {
    id: number;
    method: string;
    status: string;
    applied_at: string | null;
    job_title?: string | null;
    company?: string | null;
  };

  let { open = $bindable(false), onLink }: {
    open: boolean;
    onLink: (applicationId: number) => Promise<void> | void;
  } = $props();

  let apps = $state<Application[]>([]);
  let filter = $state("");
  let loading = $state(false);

  async function load() {
    loading = true;
    try {
      const r = await fetch("/api/applications");
      if (r.ok) {
        const body = await r.json();
        // /api/applications returns either a list or {items: [...]}
        apps = Array.isArray(body) ? body : (body.items ?? body);
      }
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (open) load();
  });

  const filtered = $derived(
    apps.filter((a) => {
      if (!filter) return true;
      const blob = `${a.id} ${a.job_title ?? ""} ${a.company ?? ""} ${a.status}`.toLowerCase();
      return blob.includes(filter.toLowerCase());
    })
  );
</script>

{#if open}
  <div class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
       onclick={() => (open = false)}
       role="presentation">
    <div class="bg-slate-900 border border-slate-700 rounded-lg p-4 w-[480px] max-h-[70vh] flex flex-col"
         onclick={(e) => e.stopPropagation()}
         role="dialog">
      <h3 class="text-lg font-semibold text-slate-100 mb-2">Link to application</h3>
      <input class="input mb-2" placeholder="Filter…" bind:value={filter} />
      <div class="overflow-y-auto flex-1 space-y-1">
        {#if loading}
          <p class="text-sm text-slate-400">Loading…</p>
        {:else if filtered.length === 0}
          <p class="text-sm text-slate-400">No applications match.</p>
        {:else}
          {#each filtered as app (app.id)}
            <button
              class="block w-full text-left px-2 py-1.5 rounded hover:bg-slate-700/60 text-sm"
              onclick={async () => {
                await onLink(app.id);
                open = false;
              }}
            >
              <span class="text-slate-100">#{app.id}</span>
              <span class="text-slate-300">{app.job_title ?? "—"}</span>
              <span class="text-slate-500">· {app.company ?? "—"}</span>
              <span class="text-xs text-slate-500 ml-2">{app.status}</span>
            </button>
          {/each}
        {/if}
      </div>
      <button class="btn btn-secondary mt-2" onclick={() => (open = false)}>Cancel</button>
    </div>
  </div>
{/if}
```

- [ ] **Step 2: Build the Inbox page**

```svelte
<!-- frontend/src/routes/inbox/+page.svelte -->
<script lang="ts">
  import { onMount } from "svelte";
  import { fetchUnlinked, linkMessage, type UnlinkedItem } from "$lib/api/gmail";
  import LinkApplicationModal from "$lib/components/LinkApplicationModal.svelte";

  let items = $state<UnlinkedItem[]>([]);
  let loading = $state(true);
  let modalFor = $state<UnlinkedItem | null>(null);
  let modalOpen = $state(false);

  async function refresh() {
    loading = true;
    try {
      items = await fetchUnlinked();
    } finally {
      loading = false;
    }
  }

  onMount(refresh);

  function openLink(msg: UnlinkedItem) {
    modalFor = msg;
    modalOpen = true;
  }

  async function handleLink(applicationId: number) {
    if (!modalFor) return;
    await linkMessage(applicationId, modalFor.id);
    await refresh();
  }

  function categoryClass(cat: string | null) {
    switch (cat) {
      case "rejection": return "bg-rose-700/40 text-rose-200";
      case "interview_invite": return "bg-emerald-700/40 text-emerald-200";
      case "offer": return "bg-amber-700/40 text-amber-200";
      case "ats_ack": return "bg-slate-700/40 text-slate-300";
      default: return "bg-slate-800/40 text-slate-400";
    }
  }
</script>

<div class="p-6 max-w-4xl mx-auto">
  <header class="flex items-center justify-between mb-4">
    <h1 class="text-xl font-semibold text-slate-100">Inbox</h1>
    <button class="btn btn-secondary" onclick={refresh}>Refresh</button>
  </header>

  {#if loading}
    <p class="text-slate-400">Loading…</p>
  {:else if items.length === 0}
    <div class="rounded border border-slate-700 p-6 text-center text-slate-400">
      No unlinked job-related messages.
    </div>
  {:else}
    <ul class="space-y-2">
      {#each items as msg (msg.id)}
        <li class="rounded border border-slate-700 bg-slate-900/40 p-3">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 text-xs mb-1">
                {#if msg.category}
                  <span class="px-1.5 py-0.5 rounded {categoryClass(msg.category)}">
                    {msg.category}
                  </span>
                {/if}
                <span class="text-slate-500">{msg.from_address}</span>
                <span class="text-slate-600">·</span>
                <span class="text-slate-500">{new Date(msg.received_at).toLocaleString()}</span>
              </div>
              <p class="text-sm text-slate-100 truncate">{msg.subject ?? "(no subject)"}</p>
              {#if msg.snippet}
                <p class="text-xs text-slate-400 truncate">{msg.snippet}</p>
              {/if}
            </div>
            <button class="btn btn-primary text-xs" onclick={() => openLink(msg)}>
              Link to app…
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  <LinkApplicationModal bind:open={modalOpen} onLink={handleLink} />
</div>
```

- [ ] **Step 3: Add a sidebar link to Inbox**

In `frontend/src/routes/+layout.svelte`, find the nav block (look for existing `/tracker` link) and add:

```svelte
<a href="/inbox" class="nav-link">Inbox</a>
```

- [ ] **Step 4: svelte-check**

```bash
cd frontend && npm run check 2>&1 | tail -10 && cd ..
```

Expected: 0 errors.

- [ ] **Step 5: Manual smoke**

1. Visit `/inbox` → empty state or list of seeded messages.
2. Click "Link to app…" on a message → modal opens listing applications.
3. Type to filter; click an app → modal closes; message disappears from the list.
4. Reload — message stays gone; check the Application detail (next task) — message appears there.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/components/LinkApplicationModal.svelte \
        frontend/src/routes/inbox/+page.svelte \
        frontend/src/routes/+layout.svelte
git commit -m "$(cat <<'EOF'
gm-10: /inbox page + LinkApplicationModal

Lists unlinked, non-noise Gmail messages with category pill, sender,
date, subject, snippet. Per-row "Link to app…" opens a searchable
Application picker. POST /api/correspondence/link on selection; refresh
on success. No pagination in Phase 1 — server caps at 200.
EOF
)"
```

---

## Task 11 — gm-11: Linked Emails tab on application detail + WS toast

**Files:**
- Modify: `frontend/src/routes/jobs/[id]/+page.svelte` (or the existing application-detail route — see note)
- Modify: `frontend/src/lib/types/ws.ts` (extend WSMessage union)
- Modify: the central WS message-dispatch (search for `switch (msg.type)` in `$lib/`)

> **Note:** The application detail page may live under `/jobs/[id]` or its own route — confirm by reading the existing tracker/job routes before editing. The plan assumes `/jobs/[id]`; if it's elsewhere, the edits below apply to the correct file.

- [ ] **Step 1: Add "Linked Emails" tab to application detail**

In the application detail Svelte page, mount a `LinkedEmailsList` component (inline if the page is simple; otherwise create one in `$lib/components/`):

```svelte
<script lang="ts">
  import { fetchThread } from "$lib/api/gmail";
  // ... existing imports

  let linked = $state<any[]>([]);

  async function loadLinked() {
    const body = await fetchThread(applicationId);
    linked = body.messages ?? [];
  }

  onMount(() => { loadLinked(); });
</script>

<!-- inside the tab strip -->
{#if activeTab === "emails"}
  <section class="space-y-2">
    {#if linked.length === 0}
      <p class="text-slate-400 text-sm">No linked emails yet. Visit <a href="/inbox" class="link">Inbox</a> to link one.</p>
    {:else}
      {#each linked as m (m.id)}
        <article class="rounded border border-slate-700 p-3">
          <header class="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>{m.from_address}</span>
            <span>{new Date(m.received_at).toLocaleString()}</span>
          </header>
          <p class="text-sm text-slate-100">{m.subject ?? "(no subject)"}</p>
          {#if m.snippet}
            <p class="text-xs text-slate-400 mt-1">{m.snippet}</p>
          {/if}
        </article>
      {/each}
    {/if}
  </section>
{/if}
```

Add the tab button to the existing tab strip with label "Linked emails ({linked.length})".

- [ ] **Step 2: Extend the FE WSMessage union**

In `frontend/src/lib/types/ws.ts` (or wherever `WSMessage` is defined — `grep -r "WSMessage" frontend/src` to confirm), add:

```typescript
export type GmailSyncStatus = {
  type: "gmail_sync_status";
  last_history_id: string | null;
  messages_synced: number;
  progress: number;
};

export type GmailMessageReceived = {
  type: "gmail_message_received";
  gmail_message_id: string;
  from_address: string;
  subject: string | null;
  category: string | null;
  category_confidence: number | null;
  linked_application_id: number | null;
  link_confidence: number | null;
};
```

Add both to the `WSMessage` discriminated union.

- [ ] **Step 3: Dispatch — toast on inbound message**

Find the central WS message switch (`grep -rn "msg.type" frontend/src | grep -i "switch\|case"`). Add cases:

```typescript
case "gmail_message_received":
  // Reuse the existing toast helper (look for an existing usage; this repo
  // surfaces a simple Snackbar or window-level toast). If none exists,
  // a tiny inline alert is fine for Phase 1.
  showToast(`Gmail: ${msg.subject ?? msg.from_address}`, msg.category ?? "info");
  break;
case "gmail_sync_status":
  // Optional: surface in the sidebar widget added in qw-4 territory.
  // Phase 1 minimum: just log it.
  console.debug("gmail sync", msg);
  break;
```

- [ ] **Step 4: svelte-check**

```bash
cd frontend && npm run check 2>&1 | tail -10 && cd ..
```

Expected: 0 errors.

- [ ] **Step 5: Manual smoke**

1. Open `/jobs/<some-app-id>` (an application that has a linked email after gm-10) → click "Linked emails" tab → see the message.
2. Trigger a sync (Settings → Gmail → Sync now) with a fresh seeded message → see toast appear.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/jobs frontend/src/lib/types/ws.ts \
        $(git diff --name-only | grep -E '(frontend/src/lib.*dispatch|frontend/src/routes/\\+layout)' || true)
git commit -m "$(cat <<'EOF'
gm-11: Linked-emails tab on app detail + WS dispatch for gmail events

Wires the FE side of gm-8: GmailSyncStatus + GmailMessageReceived land
in the WSMessage union and trigger a toast for inbound recruiter mail.
Application detail page gains a "Linked emails" tab listing
/api/correspondence/{id}, oldest-first.
EOF
)"
```

---

## Task 12 — gm-12: End-to-end smoke test

**Files:**
- Test: `tests/test_gmail_smoke.py`

- [ ] **Step 1: Write the integration smoke test**

```python
# tests/test_gmail_smoke.py
"""End-to-end Phase-1 happy path: OAuth callback → sync → list unlinked
→ link → application detail. All external HTTP is mocked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal, init_db
from backend.models.application import Application


@pytest.fixture
def app_with_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "smoke-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "smoke-secret")
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_phase_1_happy_path(app_with_gmail: TestClient):
    asyncio.run(init_db())

    # ── 1. seed an application we'll link to later
    async def _seed_app():
        async with AsyncSessionLocal() as session:
            app = Application(
                method="manual", status="applied",
                applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(app)
            await session.commit()
            return app.id
    app_id = asyncio.run(_seed_app())

    # ── 2. OAuth callback (mocked Google)
    start = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    fake_token = httpx.Response(200, json={
        "access_token": "tok", "refresh_token": "rt",
        "expires_in": 3600, "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    })
    fake_profile = httpx.Response(200, json={"emailAddress": "smoke@example.com"})

    with patch("backend.api.gmail_auth.httpx.AsyncClient") as MockCB:
        inst = MockCB.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=fake_token)
        inst.get = AsyncMock(return_value=fake_profile)
        cb = app_with_gmail.get(
            f"/api/gmail/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
    assert cb.status_code in (302, 303)

    # ── 3. trigger a sync (manual: mock the Gmail REST client)
    fake_msg = {
        "id": "m-smoke", "threadId": "t-smoke", "snippet": "hi",
        "payload": {"headers": [
            {"name": "From", "value": "recruiter@acme.com"},
            {"name": "Subject", "value": "Interview invitation — next steps"},
            {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
        ]},
        "internalDate": "1748000000000",
    }

    class _Fake:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def messages_list(self, **kw):
            return {"messages": [{"id": "m-smoke"}], "historyId": "1"}
        async def history_list(self, *a, **kw): return {"history": []}
        async def messages_get(self, mid): return fake_msg

    with patch("backend.gmail.sync.GmailRestClient", return_value=_Fake()), \
         patch("backend.gmail.auth.httpx.AsyncClient") as MockTok:
        tk = MockTok.return_value.__aenter__.return_value
        tk.post = AsyncMock(return_value=fake_token)
        sync_resp = app_with_gmail.post("/api/gmail/sync")
    assert sync_resp.status_code == 200
    assert sync_resp.json()["synced"] == 1

    # ── 4. list unlinked — the new message should appear
    unlinked = app_with_gmail.get("/api/correspondence/unlinked").json()["items"]
    assert any(it["gmail_message_id"] == "m-smoke" for it in unlinked)
    msg_row_id = next(it["id"] for it in unlinked if it["gmail_message_id"] == "m-smoke")

    # ── 5. link it
    link = app_with_gmail.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_row_id,
    })
    assert link.status_code == 201

    # ── 6. application detail thread now contains the message
    thread = app_with_gmail.get(f"/api/correspondence/{app_id}").json()
    assert [m["gmail_message_id"] for m in thread["messages"]] == ["m-smoke"]

    # ── 7. status endpoint reports a connected, synced account
    status = app_with_gmail.get("/api/gmail/status").json()
    assert status["connected"] is True
    assert status["email_address"] == "smoke@example.com"
    assert status["message_count"] == 1
    assert status["history_id"] is not None
```

- [ ] **Step 2: Run the smoke**

```bash
.venv/bin/pytest tests/test_gmail_smoke.py -v 2>&1 | tail -10
```

Expected: `1 passed`.

- [ ] **Step 3: Full sweep + pyright + svelte-check**

```bash
.venv/bin/pytest -q --tb=no 2>&1 | tail -3
.venv/bin/pyright backend 2>&1 | tail -3
cd frontend && npm run check 2>&1 | tail -5 && cd ..
```

Expected: baseline + 44 (359 total); pyright no worse than 40/7; svelte-check 0/N warnings.

- [ ] **Step 4: Commit**

```bash
git add tests/test_gmail_smoke.py
git commit -m "$(cat <<'EOF'
gm-12: gmail phase-1 end-to-end smoke test

OAuth callback → force-sync → list unlinked → manual link → application
detail thread → status endpoint. All external HTTP mocked. Locks in the
Phase-1 happy path so future changes (Phase 2 LLM tier, Pub/Sub) can be
landed without regressing the contract a user actually touches.
EOF
)"
```

---

## Post-flight: Verification & changelog

- [ ] **Step 1: Confirm the exit criteria from the design doc**

Design §10 Phase 1 exit criteria — verify each:

1. **3 new tables + 1 column migration applied** — `sqlite3 data/jobpilot.db ".schema gmail_credentials gmail_messages application_correspondence"` and `"PRAGMA table_info(applications);" | grep last_correspondence_at`.
2. **Token refresh works for ≥ 14 days** — out of scope for the plan; a manual cron-on-real-account validation is the only honest test. Note this in the changelog as "verified locally for the first refresh; long-running soak deferred to operations."
3. **No DB writes outside the three new tables + `last_correspondence_at`** — `grep -r "Application\." backend/gmail backend/api/correspondence.py | grep -v last_correspondence_at` should be empty.

- [ ] **Step 2: Update INDEX.md to mark gm-PR-1 shipped**

In `docs/reports/2026-05-23-improvements/INDEX.md`, change line 34 (PG-INT-1) from:

```markdown
| 8 | **PG-INT-1** | Gmail integration Phase 1 (read-only sync) | PG-Int-1 | M | ...
```

to:

```markdown
| 8 | **PG-INT-1** | Gmail integration Phase 1 (read-only sync) *(Shipped 2026-05-23)* | PG-Int-1 | M | ...
```

- [ ] **Step 3: Append to CHANGELOG**

```markdown
## gm-sprint — 2026-05-23 — Gmail Phase 1

- Read-only Gmail sync (polling every 5 min, heuristic-only classifier).
- Three new tables + `applications.last_correspondence_at`.
- OAuth scaffold with `gmail.readonly` scope; refresh tokens Fernet-encrypted at rest.
- `/inbox` page for manual linking; "Linked emails" tab on application detail.
- Phase 2 (LLM tier, auto-link, status FSM) is **not** included — see design doc §10.
```

- [ ] **Step 4: Commit the documentation update**

```bash
git add docs/reports/2026-05-23-improvements/INDEX.md CHANGELOG.md
git commit -m "docs: gm-sprint — Gmail Phase 1 shipped (INDEX + CHANGELOG)"
```

---

## Self-Review

**Spec coverage (design §1–§9 vs. tasks):**

| Spec section | Covered by | Notes |
|---|---|---|
| §1 Auth (scopes, storage, OAuth, refresh) | gm-2, gm-3 | Phase 1 scope only (gmail.readonly); refresh via httpx |
| §2 Sync (push, polling, rate limits, delta) | gm-5, gm-6 | Polling-only per resolved Open Q2; push deferred to Phase 2 |
| §3 Schema (3 tables + column) | gm-1 | All three tables + `last_correspondence_at` |
| §4 Classification (heuristic / Flash-Lite / Pro) | gm-4 | Heuristic-only; LLM tiers deferred to Phase 2 per scope |
| §5 Application matching (algorithm) | — | Phase 2 — not in this plan (manual link only) |
| §6 Status state machine | — | Phase 2 — not in this plan |
| §7 Auto-adapt loop | — | Phase 3 — not in this plan |
| §8 UI (REST routes, WS events, Settings) | gm-7, gm-8, gm-9, gm-10, gm-11 | Routes the FE actually needs in Phase 1 |
| §9 Privacy & safety | gm-1, gm-5 | No body persisted (snippet only); no label writes; disconnect revokes upstream |

Gaps: matcher / FSM / auto-adapt are Phase-2/3 by design — correctly excluded.

**Placeholder scan:** No "TBD" / "implement later" / "add error handling" / "similar to Task N" markers. Each task carries the actual code.

**Type consistency:**
- `GmailCredential.history_id` is `Optional[str]` (Task 1) — used consistently as `str | None` in API (Task 7) and in `GmailSyncStatus` (Task 8).
- `GmailMessage.id` is the FK target in `ApplicationCorrespondence.message_id` (both `int`) — consistent across Task 1 and Task 7 (`LinkBody.gmail_message_id: int`, mapped to `message_id` on insert).
- `classify(...)` returns `(category: str, confidence: float, vendor: Optional[str])` (Task 4) — same signature used by `_persist_one` in Task 5.
- WS variant discriminators `"gmail_sync_status"` and `"gmail_message_received"` match exactly between `ws_models.py` (Task 8) and `frontend/src/lib/types/ws.ts` (Task 11).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-23-gmail-phase-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Good fit here because the tasks are linearly dependent and each ends in a commit, so a review checkpoint after each one mirrors the `qw-1`…`qw-7` cadence the repo already runs.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review. Faster end-to-end but harder to back out of mid-stream if Phase 1 needs scope changes after gm-3.

**Which approach?**
