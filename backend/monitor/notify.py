"""Monitor notifications: a ``new_match`` WebSocket frame plus one ntfy POST (spec D16)."""

from __future__ import annotations

import logging

import httpx

from backend.api.ws import broadcast_new_match
from backend.api.ws_models import NewMatch
from backend.config import settings
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

NTFY_TIMEOUT_S = 5


async def _post_ntfy(body: str, headers: dict[str, str]) -> None:
    """POST one message to ``NTFY_URL``. A no-op without it; never raises."""
    url = settings.NTFY_URL
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=NTFY_TIMEOUT_S) as client:
            response = await client.post(url, content=body.encode(), headers=headers)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - a lost push must not fail the poll
        logger.warning("monitor: ntfy post failed: %s: %s", type(exc).__name__, exc)


async def notify_new_match(match: JobMatch, job: Job, source_name: str) -> None:
    """Tell the open pages and the owner's phone about one new match."""
    await broadcast_new_match(
        NewMatch(
            match_id=match.id,
            job_id=job.id,
            title=job.title,
            company=job.company,
            location=job.location or "",
            score=match.score,
            source=source_name,
            posted_at=job.posted_at,
            url=job.url,
        )
    )
    where = f" ({job.location})" if job.location else ""
    await _post_ntfy(
        f"{job.title} — {job.company}{where}",
        {"Title": f"New match {match.score:.0f}", "Click": job.url},
    )


async def notify_text(title: str, body: str) -> None:
    """A plain ntfy alert (e.g. a source was disabled)."""
    await _post_ntfy(body, {"Title": title})
