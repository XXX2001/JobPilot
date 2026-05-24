"""Tests for ScraplingFetcher URL construction and pagination support.

These tests don't hit the network — they exercise the pure URL-builder
path on a real ``ScraplingFetcher`` instance with a stub Gemini client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.scraping.scrapling_fetcher import ScraplingFetcher
from backend.scraping.site_prompts import (
    GOOGLE_DOMAINS,
    INDEED_DOMAINS,
    google_domain,
    indeed_domain,
)


def _fetcher() -> ScraplingFetcher:
    return ScraplingFetcher(gemini_client=MagicMock())


# ── Domain map source-of-truth ───────────────────────────────────────────────


def test_indeed_domains_cover_all_13_countries():
    expected = {"fr", "gb", "de", "es", "it", "nl", "be", "ca", "au", "us", "in", "br", "sg"}
    assert set(INDEED_DOMAINS) == expected


def test_google_domains_cover_all_13_countries():
    expected = {"fr", "gb", "de", "es", "it", "nl", "be", "ca", "au", "us", "in", "br", "sg"}
    assert set(GOOGLE_DOMAINS) == expected


def test_indeed_domain_helper_falls_back_to_pattern():
    """Unknown country falls back to ``{cc}.indeed.com``."""
    assert indeed_domain("xx") == "xx.indeed.com"


def test_google_domain_helper_falls_back_to_com():
    assert google_domain("xx") == "www.google.com"


# ── URL construction ─────────────────────────────────────────────────────────


def test_linkedin_basic_url():
    url = _fetcher()._build_search_url(
        base_url="", site="linkedin", keywords=["python"], location="Paris",
    )
    assert "linkedin.com/jobs/search" in url
    assert "keywords=python" in url
    assert "location=Paris" in url
    assert "start=" not in url


def test_linkedin_pagination_uses_start_25():
    url = _fetcher()._build_search_url(
        base_url="", site="linkedin", keywords=["python"], page=3,
    )
    # page=3 → start=(3-1)*25 = 50
    assert "start=50" in url


def test_indeed_uses_country_specific_domain():
    url = _fetcher()._build_search_url(
        base_url="", site="indeed", keywords=["python"], location="London",
        country_code="gb",
    )
    assert "uk.indeed.com" in url
    assert "q=python" in url
    assert "l=London" in url


def test_indeed_belgium_picks_up_be_domain():
    """Regression: ``be`` used to be missing from scrapling_fetcher's map."""
    url = _fetcher()._build_search_url(
        base_url="", site="indeed", keywords=["python"], country_code="be",
    )
    assert "be.indeed.com" in url


def test_indeed_pagination_uses_start_10():
    url = _fetcher()._build_search_url(
        base_url="", site="indeed", keywords=["python"], country_code="fr", page=2,
    )
    assert "start=10" in url


def test_google_jobs_belgium_picks_up_be_domain():
    """Regression: ``be`` used to be missing from scrapling_fetcher's map."""
    url = _fetcher()._build_search_url(
        base_url="", site="google_jobs", keywords=["data"], country_code="be",
    )
    assert "www.google.be" in url
    assert "udm=8" in url


def test_google_jobs_ignores_pagination():
    """Google SERP udm=8 has no clean pagination; page param is a no-op."""
    page1 = _fetcher()._build_search_url(
        base_url="", site="google_jobs", keywords=["data"], country_code="fr", page=1,
    )
    page5 = _fetcher()._build_search_url(
        base_url="", site="google_jobs", keywords=["data"], country_code="fr", page=5,
    )
    assert page1 == page5


def test_wttj_pagination_uses_page_param():
    url1 = _fetcher()._build_search_url(
        base_url="", site="welcome_to_the_jungle", keywords=["python"], country_code="fr",
    )
    assert "page=" not in url1
    url2 = _fetcher()._build_search_url(
        base_url="", site="welcome_to_the_jungle", keywords=["python"], country_code="fr", page=2,
    )
    assert "page=2" in url2


def test_glassdoor_pagination_uses_ip_suffix():
    url1 = _fetcher()._build_search_url(
        base_url="", site="glassdoor", keywords=["python"], country_code="fr",
    )
    assert "emplois.htm" in url1
    assert "_IP" not in url1
    url2 = _fetcher()._build_search_url(
        base_url="", site="glassdoor", keywords=["python"], country_code="fr", page=2,
    )
    assert "_IP2.htm" in url2


def test_unknown_site_returns_base_url():
    base = "https://example.com/jobs"
    url = _fetcher()._build_search_url(
        base_url=base, site="unknown_site_xyz", keywords=["python"],
    )
    assert url == base


def test_page_zero_or_negative_treated_as_one():
    url0 = _fetcher()._build_search_url(
        base_url="", site="linkedin", keywords=["python"], page=0,
    )
    url1 = _fetcher()._build_search_url(
        base_url="", site="linkedin", keywords=["python"], page=1,
    )
    assert url0 == url1
