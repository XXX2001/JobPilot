"""Morning batch scheduler — orchestrates daily job scraping and CV pre-generation."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import Job, JobMatch, JobSource
from backend.models.user import SearchSettings, UserProfile
from backend.models.document import TailoredDocument
from backend.models.schemas import JobDetails
from backend.matching.filters import JobFilters

from backend.applier.daily_limit import DailyLimitGuard, DailyLimitExceeded  # noqa: PLC0415
logger = logging.getLogger(__name__)

try:
    from backend.api.ws import broadcast_status  # type: ignore
except Exception:
    async def broadcast_status(message: str, progress: float = 0.0) -> None:  # type: ignore[misc]
        pass

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore

    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    AsyncIOScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore


class MorningBatchScheduler:
    """Orchestrates the daily morning job discovery and CV pre-generation pipeline.

    Steps:
      1. Scrape all configured sources.
      2. Match & rank raw jobs against user filters.
      3. Store new matches to the DB.
      4. Pre-generate tailored CVs for the top N matches (N = remaining daily limit).
      5. Broadcast "ready" status over WebSocket.
    """

    def __init__(
        self,
        scraper: Any,  # ScrapingOrchestrator
        matcher: Any,  # JobMatcher
        cv_pipeline: Any,  # CVPipeline
        db_factory: Callable[[], AsyncSession],
    ) -> None:
        self._scraper = scraper
        self._matcher = matcher
        self._cv_pipeline = cv_pipeline
        self._db_factory = db_factory
        self._scheduler = AsyncIOScheduler() if _APSCHEDULER_AVAILABLE else None

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self, batch_time: str = "08:00") -> None:
        """Schedule the morning batch as a cron job at *batch_time* (HH:MM)."""
        if self._scheduler is None:
            logger.warning("APScheduler not installed — morning batch scheduling disabled")
            return
        hour, minute = batch_time.split(":")
        self._scheduler.add_job(
            self._run_batch_task,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="morning_batch",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("Morning batch scheduler started — will run daily at %s", batch_time)

    def stop(self) -> None:
        """Shutdown the APScheduler gracefully."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Morning batch scheduler stopped")

    # ------------------------------------------------------------------ #
    #  APScheduler entry point                                             #
    # ------------------------------------------------------------------ #

    def _run_batch_task(self) -> None:
        """APScheduler calls this synchronously — we create an asyncio task."""
        asyncio.ensure_future(self.run_batch())

    # ------------------------------------------------------------------ #
    #  Public batch runner (also callable manually for testing/debugging) #
    # ------------------------------------------------------------------ #

    async def run_batch(self) -> None:
        """Full 5-step morning pipeline."""
        logger.info("Morning batch started")
        db: AsyncSession = self._db_factory()
        try:
            await self._run_batch_inner(db)
        except Exception as exc:
            logger.error("Morning batch failed: %s", exc, exc_info=True)
        finally:
            await db.close()

    # ------------------------------------------------------------------ #
    #  Internal pipeline steps                                            #
    # ------------------------------------------------------------------ #


    async def _run_batch_inner(self, db: AsyncSession) -> None:
        # ── Load settings ────────────────────────────────────────────────
        settings_row = await self._load_settings(db)
        profile_row = await self._load_profile(db)
        sources = await self._load_sources(db)

        keywords: list[str] = (
            settings_row.keywords
            if isinstance(settings_row.keywords, list)
            else list(settings_row.keywords or [])
        )
        daily_limit: int = settings_row.daily_limit or 10
        cv_path = (
            Path(profile_row.base_cv_path) if (profile_row and profile_row.base_cv_path) else None
        )

        filters = JobFilters(
            keywords=keywords,
            locations=list(settings_row.locations or []),
            salary_min=settings_row.salary_min,
            remote_only=bool(settings_row.remote_only),
            excluded_keywords=list(settings_row.excluded_keywords or []),
            excluded_companies=list(settings_row.excluded_companies or []),
            min_score=float(settings_row.min_match_score),
        )

        # ── Step 1: Scrape ───────────────────────────────────────────────
        await broadcast_status("Searching for jobs…", progress=0.05)
        raw_jobs = await self._scraper.run_morning_batch(
            keywords=keywords,
            filters=filters,
            sources=sources,
        )
        logger.info("Scraped %d raw jobs", len(raw_jobs))
        await broadcast_status(f"Found {len(raw_jobs)} raw jobs — ranking…", progress=0.35)

        # ── Step 2: Match & rank ─────────────────────────────────────────
        job_details = [self._raw_to_details(j) for j in raw_jobs]
        ranked: list[tuple[JobDetails, float]] = [
            (jd, self._matcher.score(jd, filters)) for jd in job_details
        ]
        ranked = [(jd, s) for jd, s in ranked if s >= filters.min_score]
        ranked.sort(key=lambda x: x[1], reverse=True)
        logger.info("Ranked %d jobs above threshold %.1f", len(ranked), filters.min_score)

        # ── Step 3: Store new matches ────────────────────────────────────
        await broadcast_status("Storing new matches…", progress=0.55)
        new_match_ids = await self._store_matches(db, ranked)
        logger.info("Stored %d new job matches", len(new_match_ids))

        # ── Step 4: Pre-generate CVs for top N ──────────────────────────
        guard = DailyLimitGuard(db=db, limit=daily_limit)
        remaining = await guard.remaining_today()
        top_ids = new_match_ids[:remaining]
        await broadcast_status(
            f"Generating tailored CVs for top {len(top_ids)} matches…", progress=0.65
        )

        if cv_path and cv_path.exists():
            for i, (match_id, jd) in enumerate(
                (mid, jd) for mid, (jd, _) in zip(top_ids, ranked[: len(top_ids)])
            ):
                try:
                    output_dir = Path(f"data/cvs/{match_id}")
                    output_dir.mkdir(parents=True, exist_ok=True)
                    tailored = await self._cv_pipeline.generate_tailored_cv(
                        base_cv_path=cv_path,
                        job=jd,
                        output_dir=output_dir,
                    )
                    await self._store_tailored_doc(db, match_id, tailored, doc_type="cv")
                    progress = 0.65 + 0.30 * ((i + 1) / max(len(top_ids), 1))
                    await broadcast_status(
                        f"CV {i + 1}/{len(top_ids)} generated", progress=progress
                    )
                except Exception as exc:
                    logger.error("CV generation failed for match_id=%d: %s", match_id, exc)
        else:
            logger.warning("No base CV path configured — skipping CV pre-generation")

        # ── Step 5: Notify dashboard ─────────────────────────────────────
        await broadcast_status(f"{len(top_ids)} applications ready for review", progress=1.0)
        logger.info("Morning batch complete — %d jobs ready", len(top_ids))

    # ------------------------------------------------------------------ #
    #  DB helpers                                                          #
    # ------------------------------------------------------------------ #

    async def _load_settings(self, db: AsyncSession) -> SearchSettings:
        result = await db.execute(select(SearchSettings).limit(1))
        row = result.scalar_one_or_none()
        if row is None:
            # Return sensible defaults without persisting
            row = SearchSettings(
                id=1,
                keywords=["python", "machine learning"],
                daily_limit=10,
                batch_time="08:00",
                min_match_score=30.0,
                remote_only=False,
            )
        return row

    async def _load_profile(self, db: AsyncSession) -> UserProfile | None:
        result = await db.execute(select(UserProfile).limit(1))
        return result.scalar_one_or_none()

    async def _load_sources(self, db: AsyncSession) -> list[JobSource]:
        result = await db.execute(
            select(JobSource).where(JobSource.enabled == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def _store_matches(
        self,
        db: AsyncSession,
        ranked: list[tuple[JobDetails, float]],
    ) -> list[int]:
        """Persist new job matches and return their DB IDs."""
        today = date.today()
        match_ids: list[int] = []

        for jd, score in ranked:
            # Upsert the Job row
            job_row = Job(
                title=jd.title,
                company=jd.company,
                location=jd.location or "",
                description=jd.description or "",
                salary_min=jd.salary_min,
                salary_max=jd.salary_max,
                url=jd.url or "",
                apply_url=jd.apply_url or "",
                apply_method=jd.apply_method or "",
                posted_at=jd.posted_at,
            )
            db.add(job_row)
            await db.flush()

            match_row = JobMatch(
                job_id=job_row.id,
                score=score,
                batch_date=today,
                status="new",
            )
            db.add(match_row)
            await db.flush()
            match_ids.append(match_row.id)

        await db.commit()
        return match_ids

    async def _store_tailored_doc(
        self,
        db: AsyncSession,
        match_id: int,
        tailored: Any,
        doc_type: str,
    ) -> None:
        diff_json = None
        if hasattr(tailored, "diff") and tailored.diff:
            try:
                diff_json = [
                    d.__dict__ if hasattr(d, "__dict__") else str(d) for d in tailored.diff
                ]
            except Exception:
                pass

        doc = TailoredDocument(
            job_match_id=match_id,
            doc_type=doc_type,
            tex_path=str(tailored.tex_path) if hasattr(tailored, "tex_path") else None,
            pdf_path=str(tailored.pdf_path) if hasattr(tailored, "pdf_path") else None,
            diff_json=diff_json,
        )
        db.add(doc)
        await db.commit()

    @staticmethod
    def _raw_to_details(raw: Any) -> JobDetails:
        """Convert a RawJob to a JobDetails for matching."""
        return JobDetails(
            title=raw.title,
            company=raw.company,
            location=getattr(raw, "location", ""),
            description=getattr(raw, "description", ""),
            salary_min=getattr(raw, "salary_min", None),
            salary_max=getattr(raw, "salary_max", None),
            url=getattr(raw, "url", ""),
            apply_url=getattr(raw, "apply_url", ""),
            apply_method=getattr(raw, "apply_method", ""),
            posted_at=getattr(raw, "posted_at", None),
        )


__all__ = ["MorningBatchScheduler"]
