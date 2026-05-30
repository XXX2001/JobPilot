from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.gmail.auth import GmailTokenManager
from backend.gmail.classifier_heuristics import classify
from backend.gmail.client import GmailRestClient
from backend.gmail.credentials import load_credential
from backend.models.gmail import GmailMessage

try:
    from backend.api.ws import (
        broadcast_gmail_message_received,
        broadcast_gmail_sync_status,
    )
except Exception:
    async def broadcast_gmail_sync_status(*a, **k) -> None: ...
    async def broadcast_gmail_message_received(*a, **k) -> None: ...

logger = logging.getLogger(__name__)

_CONCURRENCY = 10


def _is_gmail_dedup_violation(exc: IntegrityError) -> bool:
    """True only for the gmail_messages dedup UNIQUE violation.

    A FK violation (post-T2a) must NOT be classified as dedup — that would
    silently swallow a real referential-integrity bug.
    """
    text = str(getattr(exc, "orig", exc)).lower()
    return "unique constraint failed" in text and "gmail_message" in text


def _now() -> datetime:
    # Naive UTC, matching the legacy `datetime.utcnow()` behaviour so existing
    # DB rows (stored naive in SQLite) remain comparable.
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
                await broadcast_gmail_sync_status(
                    messages_synced=0, progress=1.0, last_history_id=new_history_id,
                )
                return 0

            payloads = await asyncio.gather(*(self._safe_get(client, mid) for mid in msg_ids))

        inserted = 0
        for payload in payloads:
            if payload is None:
                continue
            if await self._persist_one(email, payload):
                inserted += 1

        await self._update_cursor(email, new_history_id)
        await broadcast_gmail_sync_status(
            messages_synced=inserted, progress=1.0, last_history_id=new_history_id,
        )
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
            except IntegrityError as exc:
                await session.rollback()
                if _is_gmail_dedup_violation(exc):
                    return False
                raise
        await broadcast_gmail_message_received(
            gmail_message_id=row.gmail_message_id,
            from_address=row.from_address,
            subject=row.subject,
            category=row.category,
            category_confidence=row.category_confidence,
        )
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
