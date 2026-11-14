"""Source contract: every adapter is one deterministic ``poll()`` — no LLM, no browser."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import httpx

from backend.models.schemas import RawJob

ErrorKind = Literal["blocked", "auth", "transient"]


@dataclass
class PollResult:
    jobs: list[RawJob] = field(default_factory=list)
    not_modified: bool = False
    etag: str | None = None
    truncated: bool = False  # result hit the source's page/limit cap


class SourceError(Exception):
    """A poll failed. ``kind`` tells the poller how to back off."""

    def __init__(self, kind: ErrorKind, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


#                  client             cfg   etag        last_scraped_at
PollFn = Callable[[httpx.AsyncClient, dict, str | None, datetime | None], Awaitable[PollResult]]

# Keyed by JobSource.config["adapter"]; each adapter module registers itself.
ADAPTERS: dict[str, PollFn] = {}


def classify_response(r: httpx.Response) -> None:
    """Raise SourceError unless ``r`` is a usable JSON/XML answer.

    A challenge page often comes back as 200 text/html, so the content type is
    checked too: nothing from such a page may be parsed as jobs.
    """
    status = r.status_code
    if status == 401:
        raise SourceError("auth", "HTTP 401")
    if status in (403, 429, 999):
        raise SourceError("blocked", f"HTTP {status}")
    if status >= 500:
        raise SourceError("transient", f"HTTP {status}")
    if status >= 400:
        # e.g. 404 for a wrong slug: not a block, the poller just backs off.
        raise SourceError("transient", f"HTTP {status}")
    if "text/html" in r.headers.get("content-type", "").lower():
        raise SourceError("blocked", f"HTML where JSON/XML expected (HTTP {status})")
