"""Morning batch scheduler — orchestrates daily job scraping and CV pre-generation."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier.daily_limit import DailyLimitGuard  # noqa: PLC0415
from backend.config import settings
from backend.defaults import MIN_JOB_SKILLS_FOR_FIT_ENGINE
from backend.matching.cv_parser import CVParser
from backend.matching.embedder import Embedder
from backend.matching.filters import JobFilters
from backend.matching.fit_engine import FitEngine
from backend.matching.job_skill_extractor import JobSkillExtractor
from backend.models.document import TailoredDocument
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails
from backend.models.user import SearchSettings, UserProfile

logger = logging.getLogger(__name__)

try:
    from backend.api.ws import broadcast_job_assessment, broadcast_status  # type: ignore
except Exception:

    async def broadcast_status(_message: str, _progress: float = 0.0) -> None:  # type: ignore[misc]
        pass

    async def broadcast_job_assessment(_match_id: int, _ats_score: float, _gap_severity: float,
                                        _decision: str, _covered: list, _gaps: list) -> None:  # type: ignore[misc]
        pass


try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore

    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    AsyncIOScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore

def _extract_json_list(value: Any, key: str) -> list[str]:
    """Extract a list from a JSON column that may be a list or a dict.

    The frontend stores these as ``{"include": [...]}`` or ``{"items": [...]}``.
    This helper handles both shapes safely.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.get(key, []))
    return []


def _resolve_cv_path(profile_row: Any, data_dir: Path) -> Path | None:
    """Return the CV path to use for this batch run.

    Resolution order:
    1. ``profile_row.base_cv_path`` — if set *and* the file exists, use it.
    2. Auto-detect: scan ``<data_dir>/templates/`` for ``*.tex`` files and pick
       the alphabetically first one (deterministic across runs).
    3. Return ``None`` if no CV can be found.

    A warning is logged whenever we fall back to auto-detection.
    """
    if profile_row and profile_row.base_cv_path:
        candidate = Path(profile_row.base_cv_path)
        if candidate.exists():
            return candidate

    # Fallback: scan the templates directory
    templates_dir = data_dir / "templates"
    candidates = sorted(templates_dir.glob("*.tex")) if templates_dir.is_dir() else []
    if candidates:
        cv_path = candidates[0]
        logger.warning("No base_cv_path in profile — using auto-detected CV: %s", cv_path)
        return cv_path

    return None


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
        fit_engine: FitEngine | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._scraper = scraper
        self._matcher = matcher
        self._cv_pipeline = cv_pipeline
        self._db_factory = db_factory
        self._scheduler = AsyncIOScheduler() if _APSCHEDULER_AVAILABLE else None
        self._fit_engine = fit_engine or FitEngine()
        self._embedder = embedder
        self._cv_parser = CVParser()
        self._job_extractor = JobSkillExtractor()

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

        keywords: list[str] = _extract_json_list(settings_row.keywords, "include")
        daily_limit: int = settings_row.daily_limit or 10
        cv_path = _resolve_cv_path(
            profile_row, data_dir=Path(settings.jobpilot_data_dir)
        )

        filters = JobFilters(
            keywords=keywords,
            locations=_extract_json_list(settings_row.locations, "items"),
            salary_min=settings_row.salary_min,
            remote_only=bool(settings_row.remote_only),
            excluded_keywords=_extract_json_list(settings_row.excluded_keywords, "items"),
            excluded_companies=_extract_json_list(settings_row.excluded_companies, "items"),
            min_score=float(settings_row.min_match_score),
        )

        # ── Step 1: Scrape ───────────────────────────────────────────────
        await broadcast_status("Searching for jobs…", progress=0.05)
        location = filters.locations[0] if filters.locations else ""
        countries = _extract_json_list(settings_row.countries, "items") if settings_row.countries else []
        raw_jobs = await self._scraper.run_morning_batch(
            keywords=keywords,
            filters=filters,
            sources=sources,
            location=location,
            countries=countries,
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

        # ── Step 3.5: Fit Assessment ─────────────────────────────────────
        await broadcast_status("Analyzing job fit…", progress=0.58)
        sensitivity = getattr(settings_row, "cv_modification_sensitivity", "balanced")

        # Parse and embed CV profile (cached by hash)
        cv_profile = None
        if cv_path and cv_path.exists() and self._embedder:
            cv_tex = cv_path.read_text(encoding="utf-8")
            cv_profile = self._cv_parser.build_profile(cv_tex)
            cv_profile = await self._embedder.embed_cv_profile(cv_profile)

        # Build match_id -> JobDetails mapping
        match_to_jd: dict[int, Any] = {}
        _ranked_iter = iter(ranked)
        for mid in new_match_ids:
            jd_pair = next(_ranked_iter, None)
            if jd_pair:
                match_to_jd[mid] = jd_pair[0]

        assessments: dict[int, Any] = {}  # match_id -> FitAssessment or None
        if cv_profile and self._embedder:
            for mid in new_match_ids:
                jd = match_to_jd.get(mid)
                if jd is None:
                    continue
                try:
                    job_profile = self._job_extractor.extract(jd.description or "")
                    if len(job_profile.skills) < MIN_JOB_SKILLS_FOR_FIT_ENGINE:
                        assessments[mid] = None  # fallback
                        continue
                    job_profile = await self._embedder.embed_job_profile(job_profile)
                    assessment = self._fit_engine.assess(job_profile, cv_profile, sensitivity)
                    assessments[mid] = assessment

                    # Store assessment on JobMatch
                    match_row = (await db.execute(
                        select(JobMatch).where(JobMatch.id == mid)
                    )).scalar_one_or_none()
                    if match_row:
                        match_row.gap_severity = assessment.severity
                        match_row.ats_score = assessment.simulated_ats_score
                        match_row.fit_assessment_json = assessment.to_dict()

                    await broadcast_job_assessment(
                        match_id=mid,
                        ats_score=assessment.simulated_ats_score,
                        gap_severity=assessment.severity,
                        decision="modify" if assessment.should_modify else "base_cv",
                        covered=assessment.covered_skills[:10],
                        gaps=[{"skill": g.skill, "criticality": g.criticality}
                              for g in assessment.critical_gaps[:5]],
                    )
                except Exception as exc:
                    logger.warning("Fit assessment failed for match %d: %s", mid, exc)
                    assessments[mid] = None

            await db.commit()

        # ── Step 4: Pre-generate CVs for top N ──────────────────────────
        guard = DailyLimitGuard(db=db, limit=daily_limit)
        remaining = await guard.remaining_today()
        top_ids = new_match_ids[:remaining]
        await broadcast_status(
            f"Generating tailored CVs for top {len(top_ids)} matches…", progress=0.65
        )

        if cv_path and cv_path.exists():
            pairs = list(zip(top_ids, [jd for jd, _ in ranked[: len(top_ids)]]))
            sem = asyncio.Semaphore(3)  # cap concurrent Gemini calls

            async def _gen_one(mid: int, jd: Any) -> tuple[int, Any]:
                async with sem:
                    out_dir = Path(settings.jobpilot_data_dir) / "cvs" / str(mid)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    assessment = assessments.get(mid)

                    if assessment is not None and not assessment.should_modify:
                        # Base CV path — no LLM calls
                        result = await self._cv_pipeline.generate_base_cv(
                            base_cv_path=cv_path,
                            job=jd,
                            output_dir=out_dir,
                        )
                    elif assessment is not None and assessment.should_modify:
                        # Targeted modification using FitAssessment
                        result = await self._cv_pipeline.generate_tailored_cv(
                            base_cv_path=cv_path,
                            job=jd,
                            output_dir=out_dir,
                            additional_context=_additional_context,
                            fit_assessment=assessment,
                        )
                    else:
                        # Fallback — use original pipeline (JobAnalyzer + CVModifier)
                        result = await self._cv_pipeline.generate_tailored_cv(
                            base_cv_path=cv_path,
                            job=jd,
                            output_dir=out_dir,
                        )
                    return mid, result

            raw_results = await asyncio.gather(
                *[_gen_one(mid, jd) for mid, jd in pairs],
                return_exceptions=True,
            )
            done = 0
            for i, outcome in enumerate(raw_results):
                if isinstance(outcome, BaseException):
                    logger.error("CV generation failed for match_id=%d: %s", pairs[i][0], outcome)
                    continue
                mid, tailored = outcome
                await self._store_tailored_doc(db, mid, tailored, doc_type="cv")
                done += 1
                progress = 0.65 + 0.30 * (done / max(len(top_ids), 1))
                await broadcast_status(f"CV {done}/{len(top_ids)} generated", progress=progress)
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
                country=jd.country or "",
                description=jd.description or "",
                salary_min=jd.salary_min,
                salary_max=jd.salary_max,
                url=jd.url or "",
                apply_url=jd.apply_url or jd.url or "",
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
            country=getattr(raw, "country", ""),
            description=getattr(raw, "description", ""),
            salary_min=getattr(raw, "salary_min", None),
            salary_max=getattr(raw, "salary_max", None),
            url=getattr(raw, "url", ""),
            apply_url=getattr(raw, "apply_url", ""),
            apply_method=getattr(raw, "apply_method", ""),
            posted_at=getattr(raw, "posted_at", None),
        )


__all__ = ["MorningBatchScheduler"]
