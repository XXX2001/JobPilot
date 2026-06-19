from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

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
