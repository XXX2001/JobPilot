"""Monitor notifications: a ``new_match`` WebSocket frame plus one ntfy POST (spec D16)."""

from __future__ import annotations

import asyncio
import logging

import httpx

from backend.api.ws import broadcast_new_match
from backend.api.ws_models import NewMatch
from backend.config import settings
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

NTFY_TIMEOUT_S = 5  # per HTTP phase, and for one push as a whole

# Pushes still going out. The loop holds tasks weakly: this keeps them alive.
_pending: set[asyncio.Task[None]] = set()


def _post_ntfy(body: str, headers: dict[str, str]) -> None:
    """Send one message to ``NTFY_URL`` in the background. A no-op without it.

    Never awaited by the caller: a slow or unreachable ntfy must not hold up the
    poll (and with it the tick) that found the match.
    """
    url = settings.NTFY_URL
    if not url:
        return
    # ponytail: one task per push, unordered and unthrottled. A queue with a
    # single sender if ntfy ever rate-limits a burst of matches.
    task = asyncio.create_task(_send_ntfy(url, body, headers))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _send_ntfy(url: str, body: str, headers: dict[str, str]) -> None:
    """POST one message; give up after ``NTFY_TIMEOUT_S`` overall. Never raises."""
    try:
        async with asyncio.timeout(NTFY_TIMEOUT_S):
            async with httpx.AsyncClient(timeout=NTFY_TIMEOUT_S) as client:
                response = await client.post(url, content=body.encode(), headers=headers)
                response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - a lost push must not fail anything
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
            provisional=bool(match.provisional),
            location_unverified=bool(match.location_unverified),
        )
    )
    where = f" ({job.location})" if job.location else ""
    notes = [
        note
        for flag, note in (
            (match.provisional, "title only"),
            (match.location_unverified, "location unverified"),
        )
        if flag
    ]
    caveat = f" ({', '.join(notes)})" if notes else ""
    _post_ntfy(
        f"{job.title} — {job.company}{where}",
        {"Title": f"New match {match.score:.0f}{caveat}", "Click": job.url},
    )


async def notify_text(title: str, body: str) -> None:
    """A plain ntfy alert (e.g. a source was disabled)."""
    _post_ntfy(body, {"Title": title})
