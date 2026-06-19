"""Tests that ScrapingOrchestrator records per-source health correctly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.matching.filters import JobFilters
from backend.models.schemas import RawJob
from backend.scraping.orchestrator import ScrapingOrchestrator
from backend.scraping.source_health import SourceHealthTracker


def _raw_job(title="SWE", url="https://example.com/1"):
    return RawJob(title=title, company="ACME", url=url)


def _filters():
    return JobFilters(keywords=["python"], locations=[], remote_only=False)


def _mock_adzuna_source(name="adzuna", country="fr"):
    src = MagicMock()
    src.name = name
    src.type = "api"
    src.url = None
    src.config = {"country": country}
    return src


def _mock_browser_source(name="linkedin"):
    src = MagicMock()
    src.name = name
    src.type = "browser"
    src.url = f"https://{name}.com/jobs"
    src.config = {}
    src.prompt_template = None
    return src


def _mock_lab_source(name="careers", url="https://careers.example.com"):
    src = MagicMock()
    src.name = name
    src.type = "lab_url"
    src.url = url
    src.config = {}
    src.prompt_template = None
    return src


class _DummyAdzuna:
    def __init__(self, jobs=None, raise_exc=None):
        self._jobs = jobs or []
        self._raise = raise_exc

    async def search(self, **kwargs):  # noqa: ARG002
        if self._raise:
            raise self._raise
        return list(self._jobs)


class _DummyAdaptive:
    def __init__(self, jobs=None, raise_exc=None):
        self._jobs = jobs or []
        self._raise = raise_exc

    async def scrape_job_listings(self, **kwargs):  # noqa: ARG002
        if self._raise:
            raise self._raise
        return list(self._jobs)


class _DummyScrapling:
    def __init__(self, jobs=None, raise_exc=None):
        self._jobs = jobs or []
        self._raise = raise_exc
        self.calls: list[int] = []  # records page numbers

    async def scrape_job_listings(self, **kwargs):
        self.calls.append(int(kwargs.get("page", 1)))
        if self._raise:
            raise self._raise
        return list(self._jobs)


class _DummyDedup:
    def deduplicate(self, jobs):
        seen = set()
        out = []
        for j in jobs:
            if j.url not in seen:
                seen.add(j.url)
                out.append(j)
        return out


@pytest.mark.asyncio
async def test_adzuna_ok_records_healthy():
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adzuna_client=_DummyAdzuna(jobs=[_raw_job()]),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_adzuna_source()],
    )
    snap = {s["source"]: s for s in tracker.snapshot()}
    assert snap["adzuna"]["status"] == "healthy"
    assert snap["adzuna"]["last_outcome"] == "ok"
    assert snap["adzuna"]["last_job_count"] == 1


@pytest.mark.asyncio
async def test_adzuna_empty_records_degraded():
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adzuna_client=_DummyAdzuna(jobs=[]),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_adzuna_source()],
    )
    rec = tracker.get("adzuna")
    assert rec is not None
    assert rec.status() == "degraded"
    assert rec.last_outcome == "empty"


@pytest.mark.asyncio
async def test_adzuna_error_records_error():
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adzuna_client=_DummyAdzuna(raise_exc=RuntimeError("Adzuna 500")),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_adzuna_source()],
    )
    rec = tracker.get("adzuna")
    assert rec is not None
    assert rec.last_outcome == "error"
    assert rec.last_error and "Adzuna 500" in rec.last_error


@pytest.mark.asyncio
async def test_default_adzuna_path_records_health():
    """When no API source is configured but adzuna+keywords are set, the
    fallback default-Adzuna call still records health."""
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adzuna_client=_DummyAdzuna(jobs=[_raw_job()]),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[],
    )
    rec = tracker.get("adzuna")
    assert rec is not None
    assert rec.last_outcome == "ok"


@pytest.mark.asyncio
async def test_browser_source_tier1_ok_records_healthy():
    tracker = SourceHealthTracker()
    scrapling = _DummyScrapling(jobs=[_raw_job(url="https://l.example.com/1")])
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[]),
        deduplicator=_DummyDedup(),
        scrapling_fetcher=scrapling,
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_browser_source("linkedin")],
    )
    rec = tracker.get("linkedin")
    assert rec is not None
    assert rec.last_outcome == "ok"
    assert rec.last_job_count == 1


@pytest.mark.asyncio
async def test_browser_source_all_empty_records_empty():
    """Both tier1 and tier2 return zero jobs but no exception → 'empty'."""
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[]),
        deduplicator=_DummyDedup(),
        scrapling_fetcher=_DummyScrapling(jobs=[]),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_browser_source("linkedin")],
    )
    rec = tracker.get("linkedin")
    assert rec is not None
    assert rec.last_outcome == "empty"
    assert rec.status() == "degraded"


@pytest.mark.asyncio
async def test_browser_source_tier1_error_then_tier2_empty_records_error():
    """Tier 1 raised — even if Tier 2 silently returns []."""
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[]),
        deduplicator=_DummyDedup(),
        scrapling_fetcher=_DummyScrapling(raise_exc=RuntimeError("403 blocked")),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_browser_source("linkedin")],
    )
    rec = tracker.get("linkedin")
    assert rec is not None
    assert rec.last_outcome == "error"
    assert rec.last_error and "403 blocked" in rec.last_error


@pytest.mark.asyncio
async def test_lab_source_records_health():
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[_raw_job(url="https://lab.example.com/1")]),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[_mock_lab_source()],
    )
    rec = tracker.get("careers")
    assert rec is not None
    assert rec.last_outcome == "ok"


@pytest.mark.asyncio
async def test_tracker_persists_across_runs():
    """Multiple consecutive scrapes accumulate history on the same tracker."""
    tracker = SourceHealthTracker()
    orch = ScrapingOrchestrator(
        adzuna_client=_DummyAdzuna(jobs=[]),
        deduplicator=_DummyDedup(),
        source_health=tracker,
    )
    for _ in range(3):
        await orch.scrape_batch(
            keywords=["python"],
            filters=_filters(),
            sources=[_mock_adzuna_source()],
        )
    rec = tracker.get("adzuna")
    assert rec is not None
    assert rec.total_attempts == 3
    assert rec.consecutive_failures == 3
    assert rec.status() == "down"


@pytest.mark.asyncio
async def test_browser_pagination_loops_when_max_pages_set():
    """When source.config.max_pages > 1 and Tier 1 returns a full page, the
    orchestrator drives multiple page fetches.

    ``per_kw_max`` in the orchestrator is ``max(5, max_results_per_source // n_keywords)``.
    With one keyword and ``max_results_per_source=5``, per_kw_max=5 — so the
    stub must return 5 jobs per page to count as a "full page" and keep
    looping until ``max_pages``.
    """
    tracker = SourceHealthTracker()
    scrapling = _DummyScrapling(
        jobs=[_raw_job(url=f"https://l/{i}") for i in range(5)],
    )
    src = _mock_browser_source("linkedin")
    src.config = {"max_pages": 3}
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[]),
        deduplicator=_DummyDedup(),
        scrapling_fetcher=scrapling,
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[src],
        max_results_per_source=5,
    )
    # Should have hit page 1, 2, 3 — full pages each time.
    assert scrapling.calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_browser_pagination_stops_early_on_short_page():
    """A short page (fewer jobs than per_kw_max) terminates pagination."""
    tracker = SourceHealthTracker()
    # per_kw_max=5 (floor); only return 1 job → short page → stop after page 1.
    scrapling = _DummyScrapling(jobs=[_raw_job(url="https://l/1")])
    src = _mock_browser_source("linkedin")
    src.config = {"max_pages": 3}
    orch = ScrapingOrchestrator(
        adaptive_scraper=_DummyAdaptive(jobs=[]),
        deduplicator=_DummyDedup(),
        scrapling_fetcher=scrapling,
        source_health=tracker,
    )
    await orch.scrape_batch(
        keywords=["python"],
        filters=_filters(),
        sources=[src],
        max_results_per_source=5,
    )
    assert scrapling.calls == [1]  # bailed after the first short page
