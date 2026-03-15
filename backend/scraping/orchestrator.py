"""Scraping orchestrator — coordinates all job sources for a morning batch.

Phase 1: API sources (Adzuna) — fast, parallel.
Phase 2: Browser sources — sequential with 3-8s human-like delay.
Phase 3: Lab URL sources — parallel (no login needed).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from backend.models.schemas import RawJob
from backend.scraping.site_prompts import SITE_CONFIGS, SITE_PROMPTS

if TYPE_CHECKING:
    from backend.matching.filters import JobFilters
    from backend.models.job import JobSource
    from backend.scraping.adaptive_scraper import AdaptiveScraper
    from backend.scraping.adzuna_client import AdzunaClient
    from backend.scraping.deduplicator import JobDeduplicator
    from backend.scraping.scrapling_fetcher import ScraplingFetcher
    from backend.scraping.session_manager import BrowserSessionManager

logger = logging.getLogger(__name__)

# Common location names → Adzuna 2-letter country codes
LOCATION_TO_COUNTRY: dict[str, str] = {
    "france": "fr", "paris": "fr", "lyon": "fr", "marseille": "fr",
    "toulouse": "fr", "bordeaux": "fr", "strasbourg": "fr", "nantes": "fr",
    "united kingdom": "gb", "uk": "gb", "london": "gb", "manchester": "gb",
    "germany": "de", "berlin": "de", "munich": "de", "frankfurt": "de",
    "deutschland": "de", "hamburg": "de",
    "united states": "us", "usa": "us", "new york": "us",
    "netherlands": "nl", "amsterdam": "nl",
    "spain": "es", "madrid": "es", "barcelona": "es",
    "italy": "it", "milan": "it", "rome": "it",
    "belgium": "be", "brussels": "be", "bruxelles": "be",
    "switzerland": "ch", "zurich": "ch", "geneva": "ch", "gen\u00e8ve": "ch",
    "canada": "ca", "toronto": "ca", "montreal": "ca",
    "australia": "au", "sydney": "au", "melbourne": "au",
    "austria": "at", "vienna": "at", "wien": "at",
    "poland": "pl", "warsaw": "pl",
    "india": "in", "singapore": "sg", "brazil": "br",
}


def _normalize_country(raw: str) -> str:
    """Normalise a country/location string to a 2-letter Adzuna code."""
    key = raw.strip().lower()
    # Already a valid 2-letter code?
    if len(key) == 2 and key.isalpha():
        return key
    if key in LOCATION_TO_COUNTRY:
        return LOCATION_TO_COUNTRY[key]
    logger.warning("Unknown location %r — no country code mapping found", raw)
    return ""


def _flatten_results(results: list) -> list[RawJob]:
    """Flatten asyncio.gather results, ignoring exceptions."""
    flat: list[RawJob] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Source failed: %s", r)
        elif isinstance(r, list):
            flat.extend(r)
    return flat



class ScrapingOrchestrator:
    """Coordinates all job sources for a morning batch.

    Constructor receives all dependencies explicitly so they can be injected
    from the FastAPI app state (and mocked in tests).
    """

    # Tier 1 sites handled by ScraplingFetcher (HTTP + single Gemini call)
    TIER1_SITES: frozenset[str] = frozenset(
        {"linkedin", "indeed", "google_jobs", "welcome_to_the_jungle", "glassdoor"}
    )

    def __init__(
        self,
        adzuna_client: "AdzunaClient | None" = None,
        adaptive_scraper: "AdaptiveScraper | None" = None,
        session_mgr: "BrowserSessionManager | None" = None,
        deduplicator: "JobDeduplicator | None" = None,
        scrapling_fetcher: "ScraplingFetcher | None" = None,
    ) -> None:
        self.adzuna = adzuna_client
        self.adaptive_scraper = adaptive_scraper
        self.session_mgr = session_mgr
        self.deduplicator = deduplicator
        self.scrapling_fetcher = scrapling_fetcher

    async def run_morning_batch(
        self,
        keywords: list[str] | None = None,
        filters: "JobFilters | None" = None,
        sources: "list[JobSource] | None" = None,
        location: str = "",
        countries: list[str] | None = None,
        max_results_per_source: int = 20,
        max_age_days: int | None = None,
    ) -> list[RawJob]:
        """Run the full morning scraping pipeline.

        Args:
            keywords: Search keywords. Loaded from DB settings if None.
            filters:  JobFilters instance. Loaded from DB settings if None.
            sources:  List of JobSource ORM records. Loaded from DB if None.
            location: User's target location string (e.g. "France", "Paris").
            countries: List of 2-letter country codes (e.g. ["fr", "gb"]).
            max_results_per_source: Max jobs to fetch per source (default 20).
            max_age_days: Only fetch jobs posted within the last N days (None=no filter).

        Returns:
            Deduplicated list of RawJob records.
        """
        from backend.api.ws import broadcast_status

        if keywords is None:
            keywords = []
        if sources is None:
            sources = []

        all_jobs: list[RawJob] = []

        logger.info(
            "run_morning_batch: %d sources, %d keywords, location=%r, countries=%r",
            len(sources), len(keywords), location, countries,
        )

        # ------------------------------------------------------------------
        # Phase 1 — API sources (fast, parallel)
        # ------------------------------------------------------------------
        api_sources = [s for s in sources if s.type == "api"]
        if api_sources and self.adzuna:
            await broadcast_status("Phase 1: Fetching from Adzuna API…", progress=0.1)
            api_tasks = []
            for src in api_sources:
                src_config = src.config or {}
                country = _normalize_country(src_config.get("country", "fr"))
                f = filters if filters else _empty_filters()
                task = asyncio.create_task(
                    self.adzuna.search(
                        keywords=keywords, filters=f, country=country,
                        results_per_page=max_results_per_source,
                        max_days_old=max_age_days,
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
                country = _normalize_country(countries[0]) if countries else _normalize_country(location) if location else "fr"
                jobs = await self.adzuna.search(
                    keywords=keywords, filters=f, country=country,
                    results_per_page=max_results_per_source,
                    max_days_old=max_age_days,
                )
                all_jobs.extend(jobs)
                logger.info("Phase 1 (default Adzuna): %d jobs, country=%s, keywords=%s", len(jobs), country, keywords)
            except Exception as exc:
                logger.warning("Default Adzuna search failed: %s", exc, exc_info=True)

        # ------------------------------------------------------------------
        # Phase 2 — Browser sources (sequential, human-like delay)
        # ------------------------------------------------------------------
        browser_sources = [s for s in sources if s.type == "browser"]
        if browser_sources and (self.adaptive_scraper or self.scrapling_fetcher):
            await broadcast_status(
                f"Phase 2: Browser scraping {len(browser_sources)} sites…", progress=0.35
            )
            for source in browser_sources:
                try:
                    # Get or create persistent login session if available
                    # Only request login for sites that actually need it
                    site_cfg = SITE_CONFIGS.get(source.name, {})
                    if self.session_mgr and site_cfg.get("requires_login", False):
                        try:
                            await self.session_mgr.get_or_create_session(source.name)
                        except Exception as exc:
                            logger.warning("Session init failed for %s: %s", source.name, exc)

                    # Choose prompt template
                    prompt_template = source.prompt_template or SITE_PROMPTS.get(
                        source.name, SITE_PROMPTS["generic"]
                    )
                    src_country_code = _normalize_country(
                        (source.config or {}).get("country", "")
                        or site_cfg.get("country_codes", [""])[0]
                        or location
                    )

                    # Search one keyword at a time to avoid overly-specific
                    # combined queries that return 0 results. Distribute
                    # max_jobs across keywords so each search stays small.
                    source_jobs: list[RawJob] = []
                    search_keywords = keywords if keywords else [""]
                    per_kw_max = max(5, max_results_per_source // len(search_keywords)) if search_keywords else max_results_per_source

                    for kw in search_keywords:
                        kw_list = [kw] if kw else []
                        jobs: list[RawJob] = []

                        # Tier 1: ScraplingFetcher (fast HTTP + single Gemini call)
                        tier1_attempted = False
                        if self.scrapling_fetcher and source.name in self.TIER1_SITES:
                            tier1_attempted = True
                            try:
                                jobs = await self.scrapling_fetcher.scrape_job_listings(
                                    url=source.url or "",
                                    keywords=kw_list,
                                    max_jobs=per_kw_max,
                                    site=source.name,
                                    location=location,
                                    country_code=src_country_code,
                                    max_age_days=max_age_days,
                                )
                                if jobs:
                                    logger.info(
                                        "Phase 2 [Tier 1]: %d jobs from %s for keyword %r",
                                        len(jobs), source.name, kw,
                                    )
                                else:
                                    logger.info(
                                        "Phase 2 [Tier 1]: 0 jobs from %s kw=%r — falling back to Tier 2",
                                        source.name, kw,
                                    )
                            except Exception as exc:
                                logger.warning(
                                    "Phase 2 [Tier 1] failed for %s kw=%r: %s — falling back to Tier 2",
                                    source.name, kw, exc,
                                )
                                jobs = []

                        # Tier 2: AdaptiveScraper (full browser-use agent) — used as fallback
                        # or when Tier 1 is not applicable
                        if not jobs and self.adaptive_scraper:
                            try:
                                jobs = await self.adaptive_scraper.scrape_job_listings(
                                    url=source.url or "",
                                    keywords=kw_list,
                                    max_jobs=per_kw_max,
                                    prompt_template=prompt_template,
                                    site=source.name,
                                    location=location,
                                    country_code=src_country_code,
                                )
                                tier_label = "Tier 2 fallback" if tier1_attempted else "Tier 2"
                                logger.info(
                                    "Phase 2 [%s]: %d jobs from %s for keyword %r",
                                    tier_label, len(jobs), source.name, kw,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Phase 2: %s keyword %r failed (continuing): %s",
                                    source.name, kw, exc,
                                )

                        try:
                            for job in jobs:
                                if not job.country:
                                    job.country = src_country_code
                            source_jobs.extend(jobs)
                        except Exception as exc:
                            logger.warning(
                                "Phase 2: %s keyword %r failed (continuing): %s",
                                source.name, kw, exc,
                            )
                        # Brief delay between keyword searches on the same site
                        if kw != search_keywords[-1]:
                            await asyncio.sleep(random.uniform(1, 2))

                    all_jobs.extend(source_jobs)
                    logger.info("Phase 2: %d total jobs from %s", len(source_jobs), source.name)
                    await broadcast_status(
                        f"Phase 2: {len(source_jobs)} jobs from {source.name}", progress=0.5
                    )
                except Exception as exc:
                    logger.warning("Phase 2: scraping %s failed (continuing): %s", source.name, exc)

                # Brief delay between sites to avoid hammering servers
                if browser_sources.index(source) < len(browser_sources) - 1:
                    delay = random.uniform(1, 3)
                    logger.debug("Sleeping %.1fs before next browser source", delay)
                    await asyncio.sleep(delay)

        # ------------------------------------------------------------------
        # Phase 3 — Custom URL sources (parallel, no login needed)
        # ------------------------------------------------------------------
        lab_sources = [s for s in sources if s.type == "lab_url"]
        if lab_sources and self.adaptive_scraper:
            await broadcast_status(f"Phase 3: Scraping {len(lab_sources)} custom sites…", progress=0.6)
            lab_tasks = [
                asyncio.create_task(
                    self.adaptive_scraper.scrape_job_listings(
                        url=s.url or "",
                        keywords=keywords,
                        prompt_template=s.prompt_template or SITE_PROMPTS["lab_website"],
                        location=location,
                    )
                )
                for s in lab_sources
            ]
            lab_results = await asyncio.gather(*lab_tasks, return_exceptions=True)
            phase3_jobs = _flatten_results(list(lab_results))
            for job in phase3_jobs:
                if not job.country:
                    job.country = _normalize_country(location)
            all_jobs.extend(phase3_jobs)
            logger.info("Phase 3 done: %d jobs from custom sites", len(phase3_jobs))
            await broadcast_status(
                f"Phase 3 done: {len(phase3_jobs)} jobs from custom sites", progress=0.75
            )

        # ------------------------------------------------------------------
        # Post-scrape date filter (safety net for sources without URL-level filtering)
        # ------------------------------------------------------------------
        if max_age_days is not None and all_jobs:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            before = len(all_jobs)
            # Keep jobs with no posted_at (can't be filtered) or within the cutoff
            all_jobs = [j for j in all_jobs if j.posted_at is None or j.posted_at >= cutoff]
            filtered_out = before - len(all_jobs)
            if filtered_out:
                logger.info("Post-scrape date filter: removed %d old jobs (cutoff=%s)", filtered_out, cutoff.date())

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
