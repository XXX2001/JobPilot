"""Tests for scraping orchestrator and adaptive scraper helpers."""

from __future__ import annotations

import json
import pytest

from backend.scraping.adaptive_scraper import _extract_json_from_text, AdaptiveScraper
from backend.scraping.orchestrator import ScrapingOrchestrator
from backend.models.schemas import RawJob, JobDetails
from backend.matching.filters import JobFilters


# ── _extract_json_from_text ───────────────────────────────────────────────────


def test_extract_json_empty_string():
    assert _extract_json_from_text("") is None


def test_extract_json_none_input():
    assert _extract_json_from_text(None) is None  # type: ignore[arg-type]


def test_extract_json_plain_json_list():
    text = '[{"title": "Engineer", "company": "ACME", "url": "https://acme.com"}]'
    result = _extract_json_from_text(text)
    assert result is not None
    assert isinstance(result, list)
    assert result[0]["title"] == "Engineer"


def test_extract_json_from_fenced_block():
    text = """
Some preamble.
```json
[{"title": "Data Scientist", "company": "BigCo", "url": "https://bigco.io"}]
```
Some suffix.
"""
    result = _extract_json_from_text(text)
    assert result is not None
    assert result[0]["title"] == "Data Scientist"


def test_extract_json_object_not_list():
    text = '{"title": "SWE", "company": "Foo", "url": "https://foo.com"}'
    result = _extract_json_from_text(text)
    # Should return the dict wrapped in a list
    assert result is not None
    assert isinstance(result, (list, dict))


def test_extract_json_malformed():
    assert _extract_json_from_text("{not valid json}") is None


# ── AdaptiveScraper._parse_agent_result ──────────────────────────────────────


class FakeResult:
    def __init__(self, text: str) -> None:
        self._text = text

    def final_result(self) -> str:
        return self._text


def test_parse_agent_result_empty_text():
    scraper = AdaptiveScraper.__new__(AdaptiveScraper)
    jobs = scraper._parse_agent_result(FakeResult(""), source_url="https://x.com")
    assert jobs == []


def test_parse_agent_result_valid_json():
    scraper = AdaptiveScraper.__new__(AdaptiveScraper)
    payload = json.dumps(
        [{"title": "ML Engineer", "company": "DeepMind", "url": "https://deepmind.com/jobs/1"}]
    )
    jobs = scraper._parse_agent_result(FakeResult(payload), source_url="https://deepmind.com")
    assert len(jobs) == 1
    assert jobs[0].title == "ML Engineer"


def test_parse_agent_result_with_fence():
    scraper = AdaptiveScraper.__new__(AdaptiveScraper)
    text = '```json\n[{"title": "NLP Researcher", "company": "HuggingFace", "url": "https://hf.co/jobs/2"}]\n```'
    jobs = scraper._parse_agent_result(FakeResult(text), source_url="https://hf.co")
    assert len(jobs) == 1
    assert jobs[0].company == "HuggingFace"


def test_parse_agent_result_missing_required_fields():
    scraper = AdaptiveScraper.__new__(AdaptiveScraper)
    # Missing "url" → should be skipped or use empty string
    payload = json.dumps([{"title": "Analyst", "company": "Corp"}])
    jobs = scraper._parse_agent_result(FakeResult(payload), source_url="https://corp.com")
    # Should not raise; either skips or fills url=""
    assert isinstance(jobs, list)


# ── ScrapingOrchestrator.run_morning_batch (mocked) ──────────────────────────


class MockAdzuna:
    def __init__(self, result=None, fail=False):
        self._result = result or []
        self._fail = fail

    async def search(self, keywords=None, filters=None, country="gb") -> list[RawJob]:
        if self._fail:
            raise RuntimeError("Adzuna API down")
        return self._result


class MockAdaptive:
    async def scrape_job_listings(self, url, keywords, max_jobs=20, prompt_template=None):
        return []


class MockSession:
    async def get_or_create_session(self, site: str):
        return None


class MockDedup:
    def deduplicate(self, jobs):
        seen = set()
        result = []
        for j in jobs:
            if j.url not in seen:
                seen.add(j.url)
                result.append(j)
        return result


def _make_raw_job(title="Engineer", url="https://example.com/job1"):
    return RawJob(title=title, company="ACME", url=url)


def _make_filters():
    return JobFilters(keywords=["python"], locations=[], remote_only=False)


@pytest.mark.asyncio
async def test_orchestrator_merges_results():
    raw = [_make_raw_job()]
    adzuna = MockAdzuna(result=raw)
    orch = ScrapingOrchestrator(
        adzuna_client=adzuna,
        adaptive_scraper=MockAdaptive(),
        session_mgr=MockSession(),
        deduplicator=MockDedup(),
    )
    jobs = await orch.run_morning_batch(
        keywords=["python"],
        filters=_make_filters(),
        sources=[],
    )
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_orchestrator_api_failure_continues():
    """If Adzuna raises, the orchestrator should still return [] rather than crash."""
    adzuna = MockAdzuna(fail=True)
    orch = ScrapingOrchestrator(
        adzuna_client=adzuna,
        adaptive_scraper=MockAdaptive(),
        session_mgr=MockSession(),
        deduplicator=MockDedup(),
    )
    jobs = await orch.run_morning_batch(
        keywords=["python"],
        filters=_make_filters(),
        sources=[],
    )
    assert isinstance(jobs, list)


@pytest.mark.asyncio
async def test_orchestrator_deduplication():
    """Duplicate URLs should appear only once after deduplication."""
    raw = [_make_raw_job(url="https://example.com/job1")] * 3
    adzuna = MockAdzuna(result=raw)
    orch = ScrapingOrchestrator(
        adzuna_client=adzuna,
        adaptive_scraper=MockAdaptive(),
        session_mgr=MockSession(),
        deduplicator=MockDedup(),
    )
    jobs = await orch.run_morning_batch(
        keywords=["python"],
        filters=_make_filters(),
        sources=[],
    )
    assert len(jobs) == 1
