"""Scraping orchestrator — coordinates all job sources for a morning batch.

Phase 1: API sources (Adzuna) — fast, parallel.
Phase 2: Browser sources — sequential with 3-8s human-like delay.
Phase 3: Lab URL sources — parallel (no login needed).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from backend.models.schemas import JobDetails, RawJob
from backend.scraping.site_prompts import SITE_PROMPTS, format_prompt

if TYPE_CHECKING:
    from backend.matching.filters import JobFilters
    from backend.models.job import JobSource
    from backend.scraping.adaptive_scraper import AdaptiveScraper
    from backend.scraping.adzuna_client import AdzunaClient
    from backend.scraping.deduplicator import JobDeduplicator
    from backend.scraping.session_manager import BrowserSessionManager

logger = logging.getLogger(__name__)


def _flatten_results(results: list) -> list[RawJob]:
    """Flatten asyncio.gather results, ignoring exceptions."""
    flat: list[RawJob] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Source failed: %s", r)
        elif isinstance(r, list):
            flat.extend(r)
    return flat


def _raw_job_to_details(job: RawJob) -> JobDetails:
    """Convert a RawJob to JobDetails for matching."""
    return JobDetails(
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        posted_at=job.posted_at,
        posted_date=job.posted_at,
        url=job.url,
        apply_url=job.apply_url,
        apply_method=job.apply_method,
    )


class ScrapingOrchestrator:
    """Coordinates all job sources for a morning batch.

    Constructor receives all dependencies explicitly so they can be injected
    from the FastAPI app state (and mocked in tests).
    """

    def __init__(
        self,
        adzuna_client: "AdzunaClient | None" = None,
        adaptive_scraper: "AdaptiveScraper | None" = None,
        session_mgr: "BrowserSessionManager | None" = None,
        deduplicator: "JobDeduplicator | None" = None,
    ) -> None:
        self.adzuna = adzuna_client
        self.adaptive_scraper = adaptive_scraper
        self.session_mgr = session_mgr
        self.deduplicator = deduplicator

    async def run_morning_batch(
        self,
        keywords: list[str] | None = None,
        filters: "JobFilters | None" = None,
        sources: "list[JobSource] | None" = None,
    ) -> list[RawJob]:
        """Run the full morning scraping pipeline.

        Args:
            keywords: Search keywords. Loaded from DB settings if None.
            filters:  JobFilters instance. Loaded from DB settings if None.
            sources:  List of JobSource ORM records. Loaded from DB if None.

        Returns:
            Deduplicated list of RawJob records.
        """
        from backend.api.ws import broadcast_status

        if keywords is None:
            keywords = []
        if sources is None:
            sources = []

        all_jobs: list[RawJob] = []

        # ------------------------------------------------------------------
        # Phase 1 — API sources (fast, parallel)
        # ------------------------------------------------------------------
        api_sources = [s for s in sources if s.type == "api"]
        if api_sources and self.adzuna:
            await broadcast_status("Phase 1: Fetching from Adzuna API…", progress=0.1)
            api_tasks = []
            for src in api_sources:
                src_config = src.config or {}
                country = src_config.get("country", "gb")
                task = asyncio.create_task(
                    self.adzuna.search(keywords=keywords, filters=filters, country=country)
                    if filters
                    else self.adzuna.search(
                        keywords=keywords, filters=_empty_filters(), country=country
                    )
                )
                api_tasks.append(task)

            api_results = await asyncio.gather(*api_tasks, return_exceptions=True)
            phase1_jobs = _flatten_results(list(api_results))
            all_jobs.extend(phase1_jobs)
            logger.info("Phase 1 done: %d jobs from API sources", len(phase1_jobs))
            await broadcast_status(f"Phase 1 done: {len(phase1_jobs)} jobs from API", progress=0.3)
        elif not api_sources and self.adzuna and keywords:
            # Default: run Adzuna even if no explicit API source configured
            await broadcast_status("Phase 1: Fetching from Adzuna (default)…", progress=0.1)
            try:
                f = filters if filters is not None else _empty_filters()
                jobs = await self.adzuna.search(keywords=keywords, filters=f)
                all_jobs.extend(jobs)
                await broadcast_status(f"Phase 1 done: {len(jobs)} jobs from Adzuna", progress=0.3)
            except Exception as exc:
                logger.warning("Default Adzuna search failed: %s", exc)

        # ------------------------------------------------------------------
        # Phase 2 — Browser sources (sequential, human-like delay)
        # ------------------------------------------------------------------
        browser_sources = [s for s in sources if s.type == "browser"]
        if browser_sources and self.adaptive_scraper:
            await broadcast_status(
                f"Phase 2: Browser scraping {len(browser_sources)} sites…", progress=0.35
            )
            for source in browser_sources:
                try:
                    # Get or create persistent login session if available
                    if self.session_mgr:
                        try:
                            await self.session_mgr.get_or_create_session(source.name)
                        except Exception as exc:
                            logger.warning("Session init failed for %s: %s", source.name, exc)

                    # Choose prompt template
                    prompt_template = source.prompt_template or SITE_PROMPTS.get(
                        source.name, SITE_PROMPTS["generic"]
                    )
                    jobs = await self.adaptive_scraper.scrape_job_listings(
                        url=source.url or "",
                        keywords=keywords,
                        prompt_template=prompt_template,
                    )
                    all_jobs.extend(jobs)
                    logger.info("Phase 2: %d jobs from %s", len(jobs), source.name)
                    await broadcast_status(
                        f"Phase 2: {len(jobs)} jobs from {source.name}", progress=0.5
                    )
                except Exception as exc:
                    logger.warning("Phase 2: scraping %s failed (continuing): %s", source.name, exc)

                # Human-like delay between sites
                if browser_sources.index(source) < len(browser_sources) - 1:
                    delay = random.uniform(3, 8)
                    logger.debug("Sleeping %.1fs before next browser source", delay)
                    await asyncio.sleep(delay)

        # ------------------------------------------------------------------
        # Phase 3 — Lab URL sources (parallel, no login needed)
        # ------------------------------------------------------------------
        lab_sources = [s for s in sources if s.type == "lab_url"]
        if lab_sources and self.adaptive_scraper:
            await broadcast_status(f"Phase 3: Scraping {len(lab_sources)} lab sites…", progress=0.6)
            lab_tasks = [
                asyncio.create_task(
                    self.adaptive_scraper.scrape_job_listings(
                        url=s.url or "",
                        keywords=keywords,
                        prompt_template=s.prompt_template or SITE_PROMPTS["lab_website"],
                    )
                )
                for s in lab_sources
            ]
            lab_results = await asyncio.gather(*lab_tasks, return_exceptions=True)
            phase3_jobs = _flatten_results(list(lab_results))
            all_jobs.extend(phase3_jobs)
            logger.info("Phase 3 done: %d jobs from lab URLs", len(phase3_jobs))
            await broadcast_status(
                f"Phase 3 done: {len(phase3_jobs)} jobs from lab URLs", progress=0.75
            )

        # ------------------------------------------------------------------
        # Deduplicate
        # ------------------------------------------------------------------
        if self.deduplicator and all_jobs:
            deduped = self.deduplicator.deduplicate(all_jobs)
            logger.info("Deduplication: %d → %d unique jobs", len(all_jobs), len(deduped))
        else:
            deduped = all_jobs

        await broadcast_status(f"Scraping complete: {len(deduped)} unique jobs", progress=0.8)
        return deduped


def _empty_filters() -> "JobFilters":
    """Return a permissive default JobFilters (no restrictions)."""
    from backend.matching.filters import JobFilters

    return JobFilters(
        keywords=[],
        excluded_keywords=[],
        locations=[],
        salary_min=None,
        experience_range=None,
        remote_only=False,
        job_types=[],
        languages=[],
        excluded_companies=[],
    )


__all__ = ["ScrapingOrchestrator"]
