"""Monitor store: one row per job, one match per job (spec D2, D3).

Neither function commits — the caller (the poller, one session per source)
owns the transaction so a failed poll rolls back as a whole.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails, RawJob
from backend.scraping.deduplicator import dedup_key
from backend.utils.time import naive_utc_now

logger = logging.getLogger(__name__)

_matcher = JobMatcher()


def raw_to_details(raw: Any) -> JobDetails:
    """Convert a ``RawJob`` — or a stored ``Job`` row — to ``JobDetails`` for matching."""
    return JobDetails(
        title=raw.title,
        company=raw.company,
        location=getattr(raw, "location", "") or "",
        country=getattr(raw, "country", "") or "",
        description=getattr(raw, "description", "") or "",
        salary_min=getattr(raw, "salary_min", None),
        salary_max=getattr(raw, "salary_max", None),
        url=getattr(raw, "url", "") or "",
        apply_url=getattr(raw, "apply_url", "") or "",
        apply_method=getattr(raw, "apply_method", "") or "",
        posted_at=getattr(raw, "posted_at", None),
    )


async def upsert_discovered(db: AsyncSession, source: JobSource, jobs: list[RawJob]) -> list[Job]:
    """Store unseen jobs; return only the rows inserted by this call.

    Identity is ``(source.id, external_id)`` first, then ``dedup_hash`` — so the
    same posting seen on a second source refreshes the first row instead of
    creating another. A posting found by its ID that was retitled gets the new
    title and hash, so the second source still lands on it.

    Known miss (accepted): ``jobs.dedup_hash`` is globally unique, so the hash
    fallback also merges two postings of *one* source that carry different IDs
    but the same company/title/location — a repost, or two identical openings.
    The second is not stored; it is logged. Telling them apart needs a
    source-qualified hash column.
    """
    # ponytail: lookup-then-insert, no savepoint. Two sources inserting the same
    # new hash in the same tick -> IntegrityError on one of them; that poll fails
    # and the next one finds the row. Add begin_nested + re-lookup if it shows up.
    now = naive_utc_now()
    inserted: list[Job] = []
    for raw in jobs:
        existing = None
        if raw.external_id:
            existing = (
                await db.execute(
                    select(Job).where(
                        Job.source_id == source.id, Job.external_id == raw.external_id
                    )
                )
            ).scalar_one_or_none()
        dedup_hash = dedup_key(raw.company, raw.title, raw.location)
        by_hash = None
        if existing is None or existing.dedup_hash != dedup_hash:
            by_hash = (
                await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))
            ).scalar_one_or_none()
        if existing is None:
            existing = by_hash
            if (
                existing is not None
                and raw.external_id
                and existing.source_id == source.id
                and existing.external_id
            ):
                logger.info(
                    "source %s: posting %r merged into job %d (%r): same company/title/location",
                    source.name, raw.external_id, existing.id, existing.external_id,
                )
        elif existing.dedup_hash != dedup_hash and by_hash is None:
            # Retitled posting: follow it, unless another row already owns the new hash.
            existing.title, existing.company = raw.title, raw.company
            existing.location, existing.dedup_hash = raw.location, dedup_hash

        if existing is not None:
            existing.last_seen_at = now
            if len(raw.description) > len(existing.description or ""):
                existing.description = raw.description
            if raw.apply_url and not existing.apply_url:
                existing.apply_url = raw.apply_url
            continue

        job = Job(
            source_id=source.id,
            external_id=raw.external_id or None,
            title=raw.title,
            company=raw.company,
            location=raw.location,
            country=raw.country,
            salary_text=raw.salary_text or None,
            salary_min=raw.salary_min,
            salary_max=raw.salary_max,
            description=raw.description,
            url=raw.url,
            apply_url=raw.apply_url or raw.url,
            apply_method=raw.apply_method,
            posted_at=raw.posted_at,
            dedup_hash=dedup_hash,
            raw_data=raw.raw_data,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(job)
        await db.flush()  # the next RawJob in this list must see it
        inserted.append(job)
    return inserted


async def match_new(
    db: AsyncSession, jobs: list[Job], filters: JobFilters, min_score: float
) -> list[JobMatch]:
    """Create one ``new`` match per job scoring ``>= min_score`` that has no match at all."""
    if not jobs:
        return []
    matched_ids = set(
        (
            await db.execute(
                select(JobMatch.job_id).where(JobMatch.job_id.in_({j.id for j in jobs}))
            )
        ).scalars()
    )
    today = naive_utc_now().date()
    created: list[JobMatch] = []
    for job in jobs:
        if job.id in matched_ids:
            continue
        details = raw_to_details(job)
        score = _matcher.score(details, filters)
        if score < min_score:
            continue
        matched_ids.add(job.id)
        match = JobMatch(
            job_id=job.id,
            score=score,
            keyword_hits={kw: True for kw in _matcher.matched_keywords(details, filters)},
            batch_date=today,
            status="new",
        )
        db.add(match)
        created.append(match)
    await db.flush()
    return created
