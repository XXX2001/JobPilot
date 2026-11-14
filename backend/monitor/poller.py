"""Monitor poller: one scheduler tick polls the sources that are due.

No LLM, no browser, no cookies (spec D1, D7). Poll state lives on the
``job_sources`` row; each source gets its own DB session and transaction, so a
failed poll rolls back as a whole and never touches stored jobs.
"""

from __future__ import annotations

import asyncio
import logging
import random
import weakref
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.matching.filters import JobFilters
from backend.matching.matcher import load_matcher
from backend.models.job import Job, JobMatch, JobSource
from backend.models.user import SearchSettings
from backend.monitor.linkedin_detail import read_jobs
from backend.monitor.notify import notify_new_match, notify_text
from backend.monitor.store import match_new, raw_to_details, rescore_all, upsert_discovered
from backend.scheduler.batch_runner import _extract_json_list
from backend.scraping.sources import ADAPTERS, SourceError
from backend.utils.time import naive_utc_now

logger = logging.getLogger(__name__)

# A stored job without a match is scored again after a CV or settings change if it
# was stored within this many days; an older posting is most likely filled.
RESCORE_UNMATCHED_DAYS = 30

Notify = Callable[[JobMatch, Job, str], Awaitable[None]]  # (match, job, source_name)
Alert = Callable[[str, str], Awaitable[None]]  # (title, body)

# Seconds between polls when ``poll_interval_s`` is NULL, by adapter.
DEFAULT_INTERVAL_S = {
    "greenhouse": 60,
    "smartrecruiters": 60,
    "ashby": 60,
    "indeed": 420,
    "linkedin_guest": 300,
}
FALLBACK_INTERVAL_S = 300
MAX_BACKOFF = timedelta(hours=6)
DEFAULT_WINDOW_HOURS = 2  # the indeed adapter's default when nothing is configured
MAX_ERROR_CHARS = 500
# Adapters that ask for a time window: their seed can miss postings that a later,
# differently cut window returns. A board adapter returns the whole board, and its
# posted_at may be old for a posting that was only just (re)published.
WINDOWED_ADAPTERS = frozenset({"indeed", "linkedin_guest"})

_rng = random.Random()


def next_due(now: datetime, interval_s: float, fail_count: int, rng: random.Random) -> datetime:
    """When to poll next: jittered interval after a success, capped doubling after failures."""
    if fail_count <= 0:
        return now + timedelta(seconds=interval_s * rng.uniform(0.8, 1.2))
    # exponent capped so a long outage cannot overflow; 2**20 intervals is far past 6 h
    backoff = timedelta(seconds=interval_s * 2 ** min(fail_count, 20))
    return now + min(backoff, MAX_BACKOFF)


# One per event loop: an asyncio.Lock binds to the loop it first waits on.
_write_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _write_lock() -> asyncio.Lock:
    """Serialises the polls' DB writes, so they do not wait on SQLite's write lock.

    ponytail: one lock for every source. SQLite has a single writer anyway; split
    it only if the store moves to a database with row locks.
    """
    return _write_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())


def _predates(posted_at: datetime | None, cutoff: datetime | None) -> bool:
    """Was the job posted before ``cutoff``? Unknown dates count as not."""
    if posted_at is None or cutoff is None:
        return False
    if posted_at.time() == time.min:  # a bare day (LinkedIn without "x minutes ago")
        return posted_at.date() < cutoff.date()
    return posted_at < cutoff


async def _load_filters(db: AsyncSession) -> JobFilters:
    """The owner's search settings as matcher filters (same mapping as the batch runner)."""
    row = (await db.execute(select(SearchSettings).limit(1))).scalar_one_or_none()
    if row is None:
        return JobFilters()
    return JobFilters(
        keywords=_extract_json_list(row.keywords, "include"),
        locations=_extract_json_list(row.locations, "items"),
        salary_min=row.salary_min,
        remote_only=bool(row.remote_only),
        excluded_keywords=_extract_json_list(row.excluded_keywords, "items"),
        excluded_companies=_extract_json_list(row.excluded_companies, "items"),
        min_score=float(row.min_match_score),
    )


async def rescore_stale(
    db: AsyncSession, *, force: bool = False, after_save: bool = True
) -> int:
    """Bring the stored matches up to the owner's current CV and search settings.

    Called at startup and after a CV or settings save. ``force`` for a save that may
    have changed the hard filters (see :func:`rescore_all`). Commits on ``db``.

    Recently stored jobs that have no match are scored too: one the old CV, keywords or
    filters left out may match now. Nobody is notified of those; they show in the queue.
    At startup (``after_save=False``) only when a stored score was stale: otherwise
    nothing changed while the server was down. Returns how many matches were rescored.
    """
    async with _write_lock():
        matcher = await load_matcher(db)
        filters = await _load_filters(db)
        count = await rescore_all(db, matcher, filters, force=force)
        created: list[JobMatch] = []
        if after_save or count:
            # ponytail: about 2 ms per job, on the event loop. Record the swept basis
            # and skip jobs already scored on it if a save gets slow.
            unmatched = (
                await db.execute(
                    select(Job)
                    .outerjoin(JobMatch, JobMatch.job_id == Job.id)
                    .where(
                        JobMatch.id.is_(None),
                        Job.scraped_at >= naive_utc_now() - timedelta(days=RESCORE_UNMATCHED_DAYS),
                    )
                )
            ).scalars()
            created = await match_new(db, list(unmatched), filters, filters.min_score, matcher)
        await db.commit()
    if count or created:
        logger.info("Rescored %d stored match(es), matched %d stored job(s)", count, len(created))
    return count


async def poll_source(
    db: AsyncSession,
    source: JobSource,
    client: httpx.AsyncClient,
    notify: Notify,
    alert: Alert,
) -> int:
    """Poll one source and record the outcome on its row. Returns the new matches.

    Commits on ``db``. Never raises for a failed poll: the failure is stored as
    ``fail_count`` / ``last_error`` and the next poll is pushed back.
    """
    now = naive_utc_now()
    name = source.name
    cfg = dict(source.config or {})
    adapter = str(cfg.get("adapter") or "")
    interval_s = source.poll_interval_s or DEFAULT_INTERVAL_S.get(adapter, FALLBACK_INTERVAL_S)
    if source.window_hours:
        cfg["window_hours"] = source.window_hours
    # First successful poll: every job in the window is unseen. Seed silently.
    seeding = source.last_scraped_at is None
    # ... and a windowed source keeps returning postings older than its seed
    # (LinkedIn pages, Indeed overflow): those are stored and matched, not pushed.
    seeded_at = source.first_polled_at if adapter in WINDOWED_ADAPTERS else None
    fail_count = source.fail_count or 0

    created: list[tuple[JobMatch, Job]] = []
    try:
        poll = ADAPTERS.get(adapter)
        if poll is None:
            raise SourceError("transient", f"unknown adapter {adapter!r}")
        result = await poll(client, cfg, source.etag, source.last_scraped_at)
        # The fetch above ran unlocked; the writes below hold the lock up to the commit.
        async with _write_lock():
            if not result.not_modified:
                jobs = await upsert_discovered(db, source, result.jobs)
                filters = await _load_filters(db)
                matches = await match_new(db, jobs, filters, filters.min_score)
                by_id = {job.id: job for job in jobs}
                created = [(match, by_id[match.job_id]) for match in matches]
                source.etag = result.etag
                if result.truncated and adapter == "indeed":
                    # The window overflowed the page: ask for half as much next time.
                    # ponytail: never widened back; the adapter already widens to cover
                    # downtime. Grow it on quiet polls if a halved window proves too small.
                    window = int(cfg.get("window_hours") or DEFAULT_WINDOW_HOURS)
                    source.window_hours = max(1, window // 2)
                elif result.truncated:
                    logger.warning("monitor: source %s: result truncated, jobs may be missed", name)
            source.fail_count = 0
            source.last_error = None
            source.last_scraped_at = now
            source.first_polled_at = source.first_polled_at or now
            source.next_poll_at = next_due(now, interval_s, 0, _rng)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - one source must never break the tick
        await db.rollback()
        await db.refresh(source)  # rollback expired it
        if isinstance(exc, SourceError):
            error, detail = str(exc), exc.detail
        else:
            error = detail = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, httpx.HTTPError):
                logger.exception("monitor: source %s failed unexpectedly", name)
        source.last_error = error[:MAX_ERROR_CHARS]
        disabled = isinstance(exc, SourceError) and exc.kind == "auth"
        if disabled:
            # e.g. a revoked app key: retrying cannot help, the owner has to act.
            source.enabled = False
        else:
            source.fail_count = fail_count + 1
            source.next_poll_at = next_due(now, interval_s, source.fail_count, _rng)
        await db.commit()
        logger.warning("monitor: source %s: %s", name, error)
        if disabled:
            await _safely(alert("Source disabled", f"{name}: {detail}"), "alert", name)
        return 0

    stored = len(created)
    if not seeding:
        created = [(m, j) for m, j in created if not _predates(j.posted_at, seeded_at)]
        if adapter == "linkedin_guest":
            created = await _read_linkedin_pages(db, created, name)
        for match, job in created:
            await _safely(notify(match, job, name), "notify", name)
    return stored


async def _read_linkedin_pages(
    db: AsyncSession, created: list[tuple[JobMatch, Job]], source_name: str
) -> list[tuple[JobMatch, Job]]:
    """Read the matched LinkedIn cards in the owner's logged-in Chrome and rescore them.

    Returns the matches still worth a notification: a posting that is closed, or that
    its description puts under the minimum score, is kept in the queue but not pushed.
    Without the Chrome (not running, logged out) the cards stay as they are.
    """
    cards = [(m, j) for m, j in created if not (j.description or "").strip()]
    try:
        details = await read_jobs([j.url for _, j in cards])
    except Exception:  # noqa: BLE001 - the poll is committed; the cards stay title-only
        logger.exception("monitor: reading LinkedIn pages failed for source %s", source_name)
        return created
    if not details:
        return created
    drop: set[int] = set()
    async with _write_lock():
        matcher = await load_matcher(db)
        filters = await _load_filters(db)
        for match, job in cards:
            detail = details.get(job.url)
            if detail is None:
                continue
            job.apply_method = detail.apply_method
            if detail.description:
                job.description = detail.description
                result = matcher.explain(raw_to_details(job), filters)
                match.score = result.score
                match.keyword_hits = dict.fromkeys(result.covered + result.partial, True)
                match.score_basis = matcher.score_basis
                match.provisional = result.provisional
                match.location_unverified = result.location_unverified
            if detail.apply_method == "closed" or match.score < filters.min_score:
                drop.add(match.id)
        await db.commit()
    return [(m, j) for m, j in created if m.id not in drop]


async def _safely(call: Awaitable[None], what: str, source_name: str) -> None:
    """A notification that fails must not undo or fail a poll that was committed."""
    try:
        await call
    except Exception:  # noqa: BLE001
        logger.exception("monitor: %s failed for source %s", what, source_name)


async def monitor_tick() -> None:
    """Poll every enabled adapter source that is due, a few at a time."""
    now = naive_utc_now()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(JobSource.id, JobSource.config).where(
                    JobSource.enabled.is_(True),
                    or_(JobSource.next_poll_at.is_(None), JobSource.next_poll_at <= now),
                )
            )
        ).all()
    # Sources of the old batch path carry no adapter: they are not the monitor's.
    due = [sid for sid, cfg in rows if isinstance(cfg, dict) and cfg.get("adapter")]
    if not due:
        return

    semaphore = asyncio.Semaphore(max(1, settings.MONITOR_MAX_CONCURRENCY))

    async def poll_one(source_id: int, client: httpx.AsyncClient) -> None:
        async with semaphore:
            try:
                async with AsyncSessionLocal() as db:
                    source = await db.get(JobSource, source_id)
                    if source is not None:
                        await poll_source(db, source, client, notify_new_match, notify_text)
            except Exception:  # noqa: BLE001 - e.g. the failure itself could not be stored
                logger.exception("monitor: tick failed for source %d", source_id)

    async with httpx.AsyncClient(timeout=20) as client:
        await asyncio.gather(*(poll_one(sid, client) for sid in due))
