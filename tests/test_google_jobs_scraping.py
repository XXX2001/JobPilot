"""Tests for Google Jobs scraping — URL building, selectors, prompt, and HTML cleaning."""

from __future__ import annotations

from backend.scraping.scrapling_fetcher import ScraplingFetcher, _STEALTHY_SITES
from backend.scraping.site_prompts import (
    SITE_CONTENT_SELECTORS,
    SITE_PROMPTS,
    EXTRACTION_PROMPTS,
    format_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fetcher() -> ScraplingFetcher:
    """Create a ScraplingFetcher without a real GeminiClient (bypass __init__)."""
    fetcher = ScraplingFetcher.__new__(ScraplingFetcher)
    fetcher._gemini = None  # type: ignore[assignment]
    return fetcher


# ---------------------------------------------------------------------------
# _build_search_url — Google Jobs
# ---------------------------------------------------------------------------


class TestBuildSearchUrlGoogleJobs:
    """Verify _build_search_url produces correct udm=8 URLs for Google Jobs."""

    def test_basic_french_url(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.fr/search",
            site="google_jobs",
            keywords=["food safety"],
            location="France",
            country_code="fr",
        )
        assert "www.google.fr" in url
        assert "&udm=8" in url
        assert "ibp=htl" not in url
        assert "emplois" in url

    def test_uses_udm8_not_ibp(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.com/search",
            site="google_jobs",
            keywords=["python developer"],
            location="Paris",
            country_code="fr",
        )
        assert "udm=8" in url
        assert "ibp" not in url

    def test_country_code_gb(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.co.uk/search",
            site="google_jobs",
            keywords=["data engineer"],
            location="London",
            country_code="gb",
        )
        assert "www.google.co.uk" in url
        assert "&udm=8" in url

    def test_country_code_us(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.com/search",
            site="google_jobs",
            keywords=["backend"],
            location="New York",
            country_code="us",
        )
        assert "www.google.com" in url
        assert "&udm=8" in url

    def test_country_code_de(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.de/search",
            site="google_jobs",
            keywords=["devops"],
            location="Berlin",
            country_code="de",
        )
        assert "www.google.de" in url
        assert "&udm=8" in url

    def test_unknown_country_falls_back_to_google_com(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.com/search",
            site="google_jobs",
            keywords=["analyst"],
            location="Tokyo",
            country_code="jp",
        )
        assert "www.google.com" in url
        assert "&udm=8" in url

    def test_empty_keywords_returns_jobs_query(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.fr/search",
            site="google_jobs",
            keywords=[],
            location="France",
            country_code="fr",
        )
        assert "q=jobs" in url
        assert "&udm=8" in url

    def test_multi_word_keywords_are_url_encoded(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.fr/search",
            site="google_jobs",
            keywords=["machine learning", "engineer"],
            location="Paris",
            country_code="fr",
        )
        # spaces should be encoded as +
        assert "+" in url
        assert "&udm=8" in url

    def test_default_country_code_is_fr(self):
        fetcher = _make_fetcher()
        url = fetcher._build_search_url(
            base_url="https://www.google.fr/search",
            site="google_jobs",
            keywords=["python"],
            location="Lyon",
            country_code="",
        )
        assert "www.google.fr" in url


# ---------------------------------------------------------------------------
# SITE_CONTENT_SELECTORS — Google Jobs
# ---------------------------------------------------------------------------


class TestGoogleJobsContentSelector:
    """Verify CSS selectors target the udm=8 page structure."""

    def test_selector_exists(self):
        assert "google_jobs" in SITE_CONTENT_SELECTORS

    def test_selector_does_not_use_old_horizon_class(self):
        sel = SITE_CONTENT_SELECTORS["google_jobs"]
        assert "gws-plugins-horizon-jobs" not in sel

    def test_selector_targets_search_containers(self):
        sel = SITE_CONTENT_SELECTORS["google_jobs"]
        # Should contain at least one of the known udm=8 containers
        assert any(s in sel for s in ["#search", "#rso", ".MjjYud"])


# ---------------------------------------------------------------------------
# SITE_PROMPTS — Google Jobs prompt template
# ---------------------------------------------------------------------------


class TestGoogleJobsPromptTemplate:
    """Verify the Tier 2 browser-use prompt references udm=8."""

    def test_prompt_contains_udm8(self):
        prompt = SITE_PROMPTS["google_jobs"]
        assert "udm=8" in prompt

    def test_prompt_does_not_contain_ibp(self):
        prompt = SITE_PROMPTS["google_jobs"]
        assert "ibp=htl;jobs" not in prompt

    def test_prompt_mentions_job_extraction(self):
        prompt = SITE_PROMPTS["google_jobs"]
        assert "extract" in prompt.lower() or "Extract" in prompt


# ---------------------------------------------------------------------------
# format_prompt — Google Jobs
# ---------------------------------------------------------------------------


class TestFormatPromptGoogleJobs:
    """Verify format_prompt substitutes variables correctly for google_jobs."""

    def test_substitutes_google_domain(self):
        result = format_prompt("google_jobs", country_code="fr", keywords="python", location="Paris")
        assert "www.google.fr" in result

    def test_substitutes_keywords(self):
        result = format_prompt("google_jobs", keywords="data science", location="Lyon")
        assert "data science" in result

    def test_substitutes_location(self):
        result = format_prompt("google_jobs", keywords="devops", location="Marseille")
        assert "Marseille" in result

    def test_contains_udm8(self):
        result = format_prompt("google_jobs", keywords="ml", location="Paris", country_code="fr")
        assert "udm=8" in result

    def test_gb_domain(self):
        result = format_prompt("google_jobs", country_code="gb", keywords="swe", location="London")
        assert "www.google.co.uk" in result


# ---------------------------------------------------------------------------
# EXTRACTION_PROMPTS — google_jobs or default
# ---------------------------------------------------------------------------


class TestExtractionPrompt:
    """Verify an extraction prompt exists and asks for JSON output."""

    def test_google_jobs_specific_prompt_exists(self):
        assert "google_jobs" in EXTRACTION_PROMPTS

    def test_extraction_prompt_requests_json(self):
        prompt = EXTRACTION_PROMPTS["google_jobs"]
        assert "json" in prompt.lower() or "JSON" in prompt

    def test_extraction_prompt_mentions_data_share_url(self):
        prompt = EXTRACTION_PROMPTS["google_jobs"]
        assert "data-share-url" in prompt


# ---------------------------------------------------------------------------
# _clean_html — scoping with new selectors
# ---------------------------------------------------------------------------


class TestCleanHtmlGoogleJobs:
    """Verify _clean_html scopes to the correct container for google_jobs."""

    def test_scopes_to_search_div(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <div id="search">
                <div class="job-card"><h3>Software Engineer</h3><span>ACME Corp</span></div>
                <div class="job-card"><h3>Data Analyst</h3><span>BigCo</span></div>
            </div>
            <footer>Unrelated footer content that should be removed</footer>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        assert "Software Engineer" in result
        assert "Data Analyst" in result

    def test_scopes_to_rso_div(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <nav>Navigation bar</nav>
            <div id="rso">
                <div><h3>DevOps Engineer</h3><span>CloudCo</span></div>
            </div>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        assert "DevOps Engineer" in result

    def test_removes_script_and_style_tags(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <div id="search">
                <script>var tracking = true;</script>
                <style>.hidden { display: none; }</style>
                <div><h3>ML Engineer</h3></div>
            </div>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        assert "ML Engineer" in result
        assert "tracking" not in result
        assert "display: none" not in result

    def test_fallback_when_no_selector_matches(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <div class="unexpected-structure">
                <h3>Backend Dev</h3>
            </div>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        # Should still return content (full page fallback)
        assert "Backend Dev" in result

    def test_empty_html_returns_empty(self):
        fetcher = _make_fetcher()
        result = fetcher._clean_html("", site="google_jobs")
        assert result == ""

    def test_truncates_large_content(self):
        fetcher = _make_fetcher()
        # Create HTML that will produce content larger than _MAX_CONTENT_CHARS
        big_content = "<div id='search'>" + "<p>Job listing data. </p>" * 5000 + "</div>"
        html = f"<html><body>{big_content}</body></html>"
        result = fetcher._clean_html(html, site="google_jobs")
        # Should be truncated (default max is 30000 chars)
        assert len(result) <= 35000  # some overhead from markdown conversion

    def test_promotes_data_share_url_to_href(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <div id="rso">
                <div data-share-url="https://www.google.fr/search?ibp=htl;jobs&amp;q=devops&amp;htidocid=abc123">
                    <h3>DevOps Engineer</h3><span>CloudCo</span>
                </div>
            </div>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        assert "DevOps Engineer" in result
        # data-share-url should be promoted to href and survive markdown conversion
        assert "htidocid=abc123" in result

    def test_data_share_url_does_not_overwrite_existing_href(self):
        fetcher = _make_fetcher()
        html = """
        <html><body>
            <div id="rso">
                <a href="https://example.com/job/1"
                   data-share-url="https://www.google.fr/search?ibp=htl;jobs&amp;htidocid=xyz">
                    <h3>Data Analyst</h3>
                </a>
            </div>
        </body></html>
        """
        result = fetcher._clean_html(html, site="google_jobs")
        # Existing href should be kept, not overwritten by data-share-url
        assert "example.com/job/1" in result


# ---------------------------------------------------------------------------
# StealthyFetcher — Google Jobs requires it
# ---------------------------------------------------------------------------


class TestGoogleJobsUsesStealthyFetcher:
    """Google blocks plain Fetcher; google_jobs must be in _STEALTHY_SITES."""

    def test_google_jobs_in_stealthy_sites(self):
        assert "google_jobs" in _STEALTHY_SITES

    def test_other_stealthy_sites_still_present(self):
        for site in ("linkedin", "indeed", "glassdoor"):
            assert site in _STEALTHY_SITES
