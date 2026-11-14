"""Monitor store: one row per job, one match per job (spec D2, D3).

Neither function commits — the caller (the poller, one session per source)
owns the transaction so a failed poll rolls back as a whole.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher, load_matcher
from backend.matching.scorer import ScoreResult
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails, RawJob
from backend.scraping.deduplicator import dedup_key
from backend.utils.time import naive_utc_now

logger = logging.getLogger(__name__)


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
    but the same company/title/city — a repost, or two identical openings.
    The second is not stored; it is logged. Telling them apart needs a
    source-qualified hash column.
    """
    # Two sources can insert the same new hash in one tick: the lookup of both
    # misses, so the insert itself skips the conflict (ON CONFLICT DO NOTHING) and
    # the loser treats the winner's row as existing. Not a SAVEPOINT: pysqlite
    # opens no transaction before one, so its RELEASE would commit the job ahead
    # of its match.
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

        if existing is None:
            job = await db.scalar(
                insert(Job)
                .values(
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
                .on_conflict_do_nothing(index_elements=["dedup_hash"])
                .returning(Job)
            )
            if job is not None:
                inserted.append(job)
                continue
            # Lost the race: another session committed this hash since the lookup.
            existing = (
                await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))
            ).scalar_one()

        existing.last_seen_at = now
        if len(raw.description) > len(existing.description or ""):
            existing.description = raw.description
        if raw.apply_url and not existing.apply_url:
            existing.apply_url = raw.apply_url
    return inserted


async def match_new(
    db: AsyncSession,
    jobs: list[Job],
    filters: JobFilters,
    min_score: float,
    matcher: JobMatcher | None = None,
) -> list[JobMatch]:
    """Create one ``new`` match per job scoring ``>= min_score`` that has no match at all.

    A job a hard filter rejects is never matched, whatever the threshold. ``matcher``
    defaults to the owner's (:func:`load_matcher`).
    """
    if not jobs:
        return []
    matcher = matcher or await load_matcher(db)
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
        result = matcher.explain(raw_to_details(job), filters)
        if not result.passed_filters or result.score < min_score:
            continue
        matched_ids.add(job.id)
        match = JobMatch(
            job_id=job.id,
            score=result.score,
            keyword_hits=_hits(result),
            score_basis=matcher.score_basis,
            provisional=result.provisional,
            location_unverified=result.location_unverified,
            batch_date=today,
            status="new",
        )
        db.add(match)
        created.append(match)
    await db.flush()
    return created


def _hits(result: ScoreResult) -> dict[str, bool]:
    """``JobMatch.keyword_hits``: the posting's skills the CV covers, fully or by family."""
    return dict.fromkeys(result.covered + result.partial, True)


async def rescore_all(
    db: AsyncSession, matcher: JobMatcher, filters: JobFilters, *, force: bool = False
) -> int:
    """Recompute the ``new`` matches scored on another CV, keywords or gazetteer.

    ``score_basis`` does not cover the hard filters (locations, exclusions, remote
    only): ``force`` rescores every ``new`` match, for the caller that just changed them.

    Returns how many were rescored. A match the filters now reject stays, at 0.
    """
    query = select(JobMatch, Job).join(Job, Job.id == JobMatch.job_id).where(
        JobMatch.status == "new"
    )
    if not force:
        query = query.where(JobMatch.score_basis.is_distinct_from(matcher.score_basis))
    rows = (await db.execute(query)).all()
    for match, job in rows:
        result = matcher.explain(raw_to_details(job), filters)
        match.score = result.score
        match.keyword_hits = _hits(result)
        match.score_basis = matcher.score_basis
        match.provisional = result.provisional
        match.location_unverified = result.location_unverified
    await db.flush()
    return len(rows)
