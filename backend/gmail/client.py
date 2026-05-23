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
