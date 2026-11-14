"""On-demand batch runner — orchestrates job scraping and CV pre-generation.

The batch is triggered manually via POST /api/queue/refresh. There is no
automatic scheduling; users decide when to search for new jobs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Callable, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier.daily_limit import DailyLimitGuard  # noqa: PLC0415
from backend.config import settings
from backend.latex.artifacts import diff_to_json, pdf_sha256, resolve_base_source
from backend.latex.regeneration import additional_context
from backend.defaults import CONCURRENCY_LLM, DAILY_LIMIT, MIN_JOB_SKILLS_FOR_FIT_ENGINE, MIN_MATCH_SCORE
from backend.matching.cv_parser import CVParser
from backend.matching.filters import JobFilters
from backend.matching.fit_engine import FitAssessment, FitEngine
from backend.matching.gazetteer import Gazetteer
from backend.matching.job_skills import extract_job_skills
from backend.matching.matcher import load_matcher
from backend.matching.scorer import PROVISIONAL_MIN_CHARS
from backend.models.document import TailoredDocument
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails
from backend.models.user import SearchSettings, UserProfile
from backend.monitor.store import raw_to_details

logger = logging.getLogger(__name__)

from backend.api.ws_models import Status


class StoredMatch(NamedTuple):
    """A persisted match and the job it belongs to (W03 / F01).

    Carried through assessment and generation so a match is never paired
    with another job's details by list position.
    """

    match_id: int
    job: JobDetails  # job.id is the persisted Job.id

try:
    from backend.api.ws import broadcast_job_assessment, broadcast_status  # type: ignore
except Exception:

    async def broadcast_status(message: str, progress: float = 0.0) -> None:  # type: ignore[misc]
        pass

    async def broadcast_job_assessment(match_id: int, ats_score: float, gap_severity: float,
                                        decision: str, covered: list[str], gaps: list[dict]) -> None:  # type: ignore[misc]
        pass


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
    """Base CV source for this batch: the profile path, else ``templates/*.tex``."""
    return resolve_base_source(getattr(profile_row, "base_cv_path", None), data_dir, "*.tex")


class BatchRunner:
    """Orchestrates job discovery and CV pre-generation pipeline.

    Steps:
      1. Scrape all configured sources.
      2. Match & rank raw jobs against user filters.
      3. Store new matches to the DB.
      4. Pre-generate tailored CVs for the top N matches (N = remaining daily limit).
      5. Broadcast "ready" status over WebSocket.

    Triggered manually via POST /api/queue/refresh.
    """

    def __init__(
        self,
        scraper: Any,  # ScrapingOrchestrator
        matcher: Any,  # JobMatcher; None: the owner's, loaded per batch (load_matcher)
        cv_pipeline: Any,  # CVPipeline
        db_factory: Callable[[], AsyncSession],
        fit_engine: FitEngine | None = None,
    ) -> None:
        self._scraper = scraper
        self._matcher = matcher
        self._cv_pipeline = cv_pipeline
        self._db_factory = db_factory
        self._fit_engine = fit_engine or FitEngine()
        self._cv_parser = CVParser()
        # Batch state tracking
        self.running: bool = False
        self.last_status: Status | None = None

    # ------------------------------------------------------------------ #
    #  Public batch runner                                                 #
    # ------------------------------------------------------------------ #

    async def _broadcast_and_track(self, message: str, progress: float) -> None:
        """Broadcast status and track it for reconnecting clients."""
        self.last_status = Status(message=message, progress=progress)
        await broadcast_status(message, progress=progress)

    async def run_batch(self, dry_run: bool = False) -> list[dict[str, Any]] | None:
        """Run the batch pipeline.

        ``dry_run=False`` (default) runs the full 5-step pipeline and returns
        ``None`` — exactly as before.

        ``dry_run=True`` runs ONLY Step 1 (scrape) and Step 2 (match/rank), then
        returns a lightweight preview ``list[dict]`` of what *would* be matched.
        It performs NO DB writes and makes NO LLM calls — used to preview
        today's batch without burning quota or committing rows.
        """
        if self.running:
            logger.warning("Batch already running — ignoring duplicate trigger")
            return None
        self.running = True
        self.last_status = None
        logger.info("Batch started%s", " (dry-run)" if dry_run else "")
        db: AsyncSession = self._db_factory()
        try:
            return await self._run_batch_inner(db, dry_run=dry_run)
        except Exception as exc:
            logger.error("Batch failed: %s", exc, exc_info=True)
            await self._broadcast_and_track(f"Batch failed: {exc}", progress=-1.0)
            return None
        finally:
            self.running = False
            await db.close()

    # ------------------------------------------------------------------ #
    #  Internal pipeline steps                                            #
    # ------------------------------------------------------------------ #

    async def _run_batch_inner(
        self, db: AsyncSession, dry_run: bool = False
    ) -> list[dict[str, Any]] | None:
        # ── Load settings ────────────────────────────────────────────────
        settings_row = await self._load_settings(db)
        profile_row = await self._load_profile(db)
        sources = await self._load_sources(db)

        keywords: list[str] = _extract_json_list(settings_row.keywords, "include")
        max_results_per_source: int = getattr(settings_row, "max_results_per_source", 20) or 20
        max_age_days: int | None = getattr(settings_row, "max_job_age_days", None)
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

        # ── Follow-up scan (lazy trigger) ────────────────────────────────
        # Skipped in dry-run: scan_overdue() persists application events, and a
        # preview must write NOTHING.
        if not dry_run:
            try:
                from backend.applier.follow_up import scan_overdue  # noqa: PLC0415

                _fu_count = await scan_overdue()
                logger.info("Batch follow-up scan: %d event(s) created", _fu_count)
            except Exception as _fu_exc:
                logger.warning(
                    "follow_up.scan_overdue failed at batch start (non-fatal): %s",
                    _fu_exc,
                    exc_info=True,
                )

        # ── Step 1: Scrape ───────────────────────────────────────────────
        await self._broadcast_and_track("Searching for jobs…", progress=0.05)
        location = filters.locations[0] if filters.locations else ""
        countries = _extract_json_list(settings_row.countries, "items") if settings_row.countries else []
        raw_jobs = await self._scraper.scrape_batch(
            keywords=keywords,
            filters=filters,
            sources=sources,
            location=location,
            countries=countries,
            max_results_per_source=max_results_per_source,
            max_age_days=max_age_days,
        )
        logger.info("Scraped %d raw jobs", len(raw_jobs))
        await self._broadcast_and_track(f"Found {len(raw_jobs)} raw jobs — ranking…", progress=0.35)

        # ── Step 2: Match & rank ─────────────────────────────────────────
        matcher = self._matcher or await load_matcher(db)
        job_details = [raw_to_details(j) for j in raw_jobs]
        ranked: list[tuple[JobDetails, float]] = [
            (jd, matcher.score(jd, filters)) for jd in job_details
        ]
        ranked = [(jd, s) for jd, s in ranked if s >= filters.min_score]
        ranked.sort(key=lambda x: x[1], reverse=True)
        logger.info("Ranked %d jobs above threshold %.1f", len(ranked), filters.min_score)

        # ── Dry-run cut point ────────────────────────────────────────────
        # Everything below this line writes to the DB and/or calls the LLM.
        # For a preview we stop here and return what WOULD be matched.
        if dry_run:
            preview = [
                {
                    "title": jd.title,
                    "company": jd.company,
                    "score": round(score),
                    "location": jd.location or "",
                }
                for jd, score in ranked
            ]
            await self._broadcast_and_track(
                f"Preview ready — {len(preview)} match(es) (nothing saved)", progress=1.0
            )
            logger.info("Dry-run preview built: %d match(es), no DB writes", len(preview))
            return preview

        # ── Step 3: Store new matches ────────────────────────────────────
        await self._broadcast_and_track("Storing new matches…", progress=0.55)
        stored = await self._store_matches(db, ranked, filters, matcher)
        logger.info("Stored %d new job matches", len(stored))

        # ── Step 3.5: Fit Assessment ─────────────────────────────────────
        await self._broadcast_and_track("Analyzing job fit…", progress=0.58)
        sensitivity = getattr(settings_row, "cv_modification_sensitivity", "balanced")

        assessments: dict[int, FitAssessment | None] = {}
        if cv_path and cv_path.exists():
            # The owner's keywords extend the gazetteer, as they do for the match score.
            gaz = Gazetteer.load(filters.keywords)
            cv_profile = self._cv_parser.build_profile(cv_path.read_text(encoding="utf-8"), gaz)
            for sm in stored:
                mid = sm.match_id
                try:
                    assessment = self._assess_one(sm.job, cv_profile, gaz, sensitivity)
                except Exception as exc:  # noqa: BLE001 - one bad posting must not end the batch
                    logger.warning("Fit assessment failed for match %d: %s", mid, exc)
                    assessment = None
                assessments[mid] = assessment
                if assessment is None:
                    continue

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

            await db.commit()

        # ── Step 4: Pre-generate CVs for top N ──────────────────────────
        cv_tailoring_enabled = getattr(settings_row, "cv_tailoring_enabled", True)
        guard = await DailyLimitGuard.from_settings(db)
        remaining = await guard.remaining_today()
        # A card with no description was scored on its title alone and can outrank real
        # postings: it takes no CV slot, and there is no text to tailor a CV to.
        described = [
            sm for sm in stored
            if len((sm.job.description or "").strip()) >= PROVISIONAL_MIN_CHARS
        ]
        if len(described) < len(stored):
            logger.info(
                "%d match(es) without a description: no CV pre-generated",
                len(stored) - len(described),
            )
        selected = described[:remaining]
        top_ids = [sm.match_id for sm in selected]

        if cv_tailoring_enabled:
            await self._broadcast_and_track(
                f"Generating tailored CVs for top {len(top_ids)} matches…", progress=0.65
            )
        else:
            await self._broadcast_and_track(
                f"Compiling base CV for top {len(top_ids)} matches (AI tailoring disabled)…", progress=0.65
            )

        done = failed = 0
        run_id = uuid.uuid4().hex[:12]
        if cv_path and cv_path.exists():
            _additional_context = additional_context(profile_row)

            pairs = [(sm.match_id, sm.job) for sm in selected]
            sem = asyncio.Semaphore(CONCURRENCY_LLM)

            async def _gen_one(mid: int, jd: Any) -> tuple[int, Any]:
                async with sem:
                    # Name output dir with job title slug for readability
                    import re as _re
                    slug = _re.sub(r"[^\w]+", "_", (jd.title or "job").lower()).strip("_")[:50]
                    dir_name = f"{mid}_{slug}"
                    # Review I6: one directory per run — a same-day re-run
                    # never overwrites a PDF an earlier row points at.
                    out_dir = Path(settings.jobpilot_data_dir) / "cvs" / dir_name / run_id
                    out_dir.mkdir(parents=True, exist_ok=True)

                    # Sub-step feedback: a single tailoring call on a local
                    # reasoning model can take 100s+, during which the only other
                    # signal is the prior "CV x/N generated" line. Emit a
                    # per-job "started" status so the UI isn't silent. Hold the
                    # progress bar at the CV-phase floor (it advances on
                    # completion in the gather loop below).
                    await broadcast_status(
                        f"Generating CV: {(jd.title or 'job')[:40]}…", progress=0.66
                    )

                    # When CV tailoring is disabled, always use the base CV (no AI calls)
                    if not cv_tailoring_enabled:
                        result = await self._cv_pipeline.generate_base_cv(
                            base_cv_path=cv_path,
                            job=jd,
                            output_dir=out_dir,
                        )
                        return mid, result

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
            for i, outcome in enumerate(raw_results):
                if isinstance(outcome, BaseException):
                    failed += 1
                    logger.error("CV generation failed for match_id=%d: %s", pairs[i][0], outcome)
                    continue
                mid, tailored = outcome
                await self._store_tailored_doc(db, mid, tailored, doc_type="cv")
                done += 1
                progress = 0.65 + 0.30 * ((done + failed) / max(len(top_ids), 1))
                await self._broadcast_and_track(f"CV {done}/{len(top_ids)} generated", progress=progress)
        else:
            logger.warning("No base CV path configured — skipping CV pre-generation")

        # ── Step 5: Notify dashboard ─────────────────────────────────────
        # Report persisted artifacts, not selected matches (W03).
        summary = f"{done} ready for review"
        if failed:
            summary += f", {failed} failed"
        await self._broadcast_and_track(summary, progress=1.0)
        logger.info("Batch complete — %d ready, %d failed of %d selected", done, failed, len(top_ids))

    def _assess_one(
        self, jd: Any, cv_profile: Any, gaz: Gazetteer, sensitivity: str
    ) -> FitAssessment | None:
        """Fit of ONE job, or ``None`` when its posting names too few skills to judge
        (the caller then tailors through the original pipeline)."""
        job_skills = extract_job_skills(jd.title or "", jd.description or "", gaz)
        if len(job_skills) < MIN_JOB_SKILLS_FOR_FIT_ENGINE:
            return None
        return self._fit_engine.assess(job_skills, cv_profile, gaz, sensitivity)

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
                keywords=[],
                daily_limit=DAILY_LIMIT,
                min_match_score=MIN_MATCH_SCORE,
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
        filters: JobFilters,
        matcher: Any = None,  # the one that ranked; default: the injected matcher
    ) -> list[StoredMatch]:
        """Persist new job matches; return each match with its own job.

        Already-actioned jobs are omitted and a job scraped twice in one batch
        yields one entry, so callers must use the returned job, never the
        position in ``ranked``.

        Jobs are deduplicated by a hash of (company, title, location) so
        repeated batch runs do not create duplicate Job rows.  If a job
        already exists the existing row is reused for the new JobMatch.
        """
        from backend.scraping.deduplicator import dedup_key

        matcher = matcher or self._matcher
        today = date.today()
        stored: list[StoredMatch] = []
        seen: set[int] = set()

        for jd, score in ranked:
            # Compute dedup hash — same logic as /api/jobs/search
            dedup_hash = dedup_key(jd.company, jd.title, jd.location)

            # Check for existing Job with same hash
            existing = (
                await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))
            ).scalar_one_or_none()

            if existing is not None:
                job_row = existing
                # Update fields that may have improved (e.g. description, apply_url)
                if jd.description and len(jd.description) > len(existing.description or ""):
                    existing.description = jd.description
                if jd.apply_url and not existing.apply_url:
                    existing.apply_url = jd.apply_url
            else:
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
                    dedup_hash=dedup_hash,
                )
                db.add(job_row)
                await db.flush()

            # Skip jobs that already have a non-new match (applied/skipped/rejected)
            any_actioned = (
                await db.execute(
                    select(JobMatch).where(
                        JobMatch.job_id == job_row.id,
                        # "applying" = prepared for the user or possibly
                        # submitted (W06): re-queueing it invites a duplicate.
                        # "rejected" would otherwise take a top-N CV slot every day.
                        JobMatch.status.in_(["applied", "skipped", "applying", "rejected"]),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

            if any_actioned is not None:
                logger.debug(
                    "Skipping job_id=%d — already %s", job_row.id, any_actioned.status
                )
                continue

            # One match per job, ever (spec D3): a later batch reuses the match
            # instead of queueing the job again. Newest first, because rows
            # written before D3 may hold one match per day.
            existing_match = (
                await db.execute(
                    select(JobMatch)
                    .where(JobMatch.job_id == job_row.id)
                    .order_by(JobMatch.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if existing_match is not None:
                # Update score if it improved
                if score > existing_match.score:
                    existing_match.score = score
                match_id = existing_match.id
            else:
                # Persist the matched include-keywords so the "Why this score"
                # panel can render the keyword breakdown. Stored as a dict of
                # keyword -> True: the column is ``Mapped[Optional[dict]]`` and
                # the frontend treats a dict of truthy values as the hit set.
                matched = matcher.matched_keywords(jd, filters)
                match_row = JobMatch(
                    job_id=job_row.id,
                    score=score,
                    keyword_hits={kw: True for kw in matched},
                    score_basis=getattr(matcher, "score_basis", None),
                    batch_date=today,
                    status="new",
                )
                db.add(match_row)
                await db.flush()
                match_id = match_row.id

            if match_id in seen:
                continue
            seen.add(match_id)
            stored.append(StoredMatch(match_id, jd.model_copy(update={"id": job_row.id})))

        await db.commit()
        return stored

    async def _store_tailored_doc(
        self,
        db: AsyncSession,
        match_id: int,
        tailored: Any,
        doc_type: str,
    ) -> None:
        diff_json = diff_to_json(tailored)
        pdf_path = getattr(tailored, "pdf_path", None)
        doc = TailoredDocument(
            job_match_id=match_id,
            doc_type=doc_type,
            tex_path=str(tailored.tex_path) if hasattr(tailored, "tex_path") else None,
            pdf_path=str(pdf_path) if pdf_path else None,
            pdf_sha256=pdf_sha256(Path(pdf_path)) if pdf_path else None,
            diff_json=diff_json,
        )
        db.add(doc)
        await db.commit()


__all__ = ["BatchRunner"]
