"""Tier 1 scraper: Scrapling HTTP fetch + single Gemini call.

For known job boards (LinkedIn, Indeed, Google Jobs, WTTJ, Glassdoor) the page
structure is predictable — we fetch the HTML, clean it down to essential content,
then extract jobs with a single GeminiClient.generate_text() call instead of a
full browser-use Agent loop (~20 calls). Unknown/lab sites stay on Tier 2
(AdaptiveScraper).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from backend.config import settings
from backend.defaults import MAX_SCRAPLING_CONTENT_CHARS
from backend.models.schemas import RawJob
from backend.scraping.json_utils import extract_json_from_text, parse_jobs_from_json
from backend.scraping.site_prompts import EXTRACTION_PROMPTS, SITE_CONTENT_SELECTORS

if TYPE_CHECKING:
    from backend.llm.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

# Sites where we use StealthyFetcher (Patchright-based, handles anti-bot / auth cookies)
_STEALTHY_SITES = {"linkedin", "indeed", "glassdoor", "google_jobs"}

_MAX_CONTENT_CHARS = MAX_SCRAPLING_CONTENT_CHARS


class ScraplingFetcher:
    """Tier 1 scraper: HTTP fetch via Scrapling + single LLM extraction call.

    Reduces ~20 Gemini API calls per site/keyword to 1, and cuts wall-clock
    time from 60-180 s to ~10-30 s per site/keyword pair.
    """

    def __init__(self, gemini_client: "GeminiClient") -> None:
        self._gemini = gemini_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scrape_job_listings(
        self,
        url: str,
        keywords: list[str],
        max_jobs: int = 20,
        site: str | None = None,
        location: str = "",
        country_code: str = "",
        max_age_days: int | None = None,
    ) -> list[RawJob]:
        """Fetch a job listings page and extract jobs with a single Gemini call.

        Returns an empty list on any failure (caller should fall back to Tier 2).
        """
        site = site or ""
        fetcher_type = "StealthyFetcher" if site in _STEALTHY_SITES else "Fetcher"

        # Build a keyword-aware search URL instead of just fetching the base URL
        search_url = self._build_search_url(
            base_url=url, site=site, keywords=keywords,
            location=location, country_code=country_code,
            max_age_days=max_age_days,
        )
        logger.info(
            "[Tier 1] START scrape — site=%s search_url=%s keywords=%r fetcher=%s headless=%s",
            site, search_url, keywords, fetcher_type, settings.jobpilot_scraper_headless,
        )

        try:
            html = await self.fetch_page(search_url, site=site)
        except Exception as exc:
            logger.warning("[Tier 1] fetch_page FAILED — site=%s url=%s error=%s", site, search_url, exc)
            return []

        if not html:
            logger.warning("[Tier 1] empty HTML — site=%s url=%s", site, search_url)
            return []

        logger.info("[Tier 1] fetched %d bytes of HTML — site=%s", len(html), site)

        cleaned = self._clean_html(html, site=site)
        if not cleaned:
            logger.warning("[Tier 1] cleaned content is empty — site=%s url=%s", site, search_url)
            return []

        logger.info(
            "[Tier 1] cleaned HTML → %d chars (%.1f%% of raw) — site=%s",
            len(cleaned), 100.0 * len(cleaned) / max(len(html), 1), site,
        )

        try:
            logger.info("[Tier 1] calling Gemini for extraction — site=%s", site)
            raw_text = await self._extract_jobs(cleaned, site=site)
        except Exception as exc:
            logger.warning("[Tier 1] Gemini extraction FAILED — site=%s error=%s", site, exc)
            return []

        logger.debug("[Tier 1] Gemini raw response (%d chars):\n%s", len(raw_text), raw_text[:500])

        parsed = extract_json_from_text(raw_text)
        jobs = parse_jobs_from_json(parsed, source_url=search_url, source_name="scrapling")
        logger.info(
            "[Tier 1] DONE — site=%s extracted %d jobs (url=%s)",
            site, len(jobs), search_url,
        )
        return jobs

    async def fetch_page(
        self,
        url: str,
        site: str = "",
        storage_state: str | None = None,
    ) -> str:
        """Fetch a page's HTML using Scrapling (async wrapper around sync API).

        Uses StealthyFetcher for anti-bot sites, plain Fetcher otherwise.
        Reuses existing Playwright storage state when available.
        Respects JOBPILOT_SCRAPER_HEADLESS for browser visibility.
        """
        # Resolve storage state path if not explicitly provided
        if storage_state is None and site:
            state_path = Path(settings.jobpilot_data_dir) / "browser_profiles" / site / "state.json"
            if state_path.exists():
                storage_state = str(state_path)
                logger.info("[Tier 1] using storage state: %s", storage_state)
            else:
                logger.debug("[Tier 1] no storage state found for site=%s", site)

        use_stealthy = site in _STEALTHY_SITES
        headless = settings.jobpilot_scraper_headless

        logger.info(
            "[Tier 1] fetch_page — url=%s fetcher=%s headless=%s storage_state=%s",
            url, "StealthyFetcher" if use_stealthy else "Fetcher",
            headless, storage_state or "none",
        )

        # Load cookies from Playwright storage_state JSON if available
        cookies: list | None = None
        if storage_state:
            try:
                import json as _json
                with open(storage_state) as f:
                    state = _json.load(f)
                raw_cookies = state.get("cookies") or []
                # Scrapling/Patchright rejects partitionKey as an object (newer
                # Playwright versions store it as {hasCrossSiteAncestor, topLevelSite}).
                # Remove the key entirely when it's not a plain string.
                cookies = []
                for c in raw_cookies:
                    c = dict(c)
                    pk = c.get("partitionKey")
                    if pk is not None and not isinstance(pk, str):
                        del c["partitionKey"]
                    cookies.append(c)
                logger.info("[Tier 1] loaded %d cookies from storage state", len(cookies))
            except Exception as exc:
                logger.warning("[Tier 1] could not load storage state cookies: %s", exc)

        def _fetch_sync() -> str:
            try:
                if use_stealthy:
                    from scrapling.fetchers import StealthyFetcher  # type: ignore
                    kwargs: dict = {"headless": headless}
                    if cookies:
                        kwargs["cookies"] = cookies
                    page = StealthyFetcher.fetch(url, **kwargs)
                else:
                    from scrapling.fetchers import Fetcher  # type: ignore
                    page = Fetcher.get(url)
                html = page.html_content or ""
                logger.info("[Tier 1] _fetch_sync OK — %d bytes from %s", len(html), url)
                return html
            except Exception as exc:
                logger.warning("[Tier 1] _fetch_sync error — url=%s: %s", url, exc)
                raise

        return await asyncio.get_running_loop().run_in_executor(None, _fetch_sync)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_search_url(
        self,
        base_url: str,
        site: str,
        keywords: list[str],
        location: str = "",
        country_code: str = "",
        max_age_days: int | None = None,
    ) -> str:
        """Build a keyword-aware search URL for the given site.

        Falls back to base_url if no template is defined for the site.
        country_code is a 2-letter ISO code (e.g. 'fr', 'gb').
        max_age_days limits results to jobs posted within the last N days.
        """
        kw = quote_plus(" ".join(keywords)) if keywords else ""
        loc = quote_plus(location) if location else ""
        cc = (country_code or "fr").lower()

        # Indeed domain per country
        _INDEED_DOMAINS: dict[str, str] = {
            "fr": "fr.indeed.com", "gb": "uk.indeed.com", "de": "de.indeed.com",
            "us": "www.indeed.com", "ca": "ca.indeed.com", "au": "au.indeed.com",
            "nl": "indeed.nl", "es": "es.indeed.com", "it": "it.indeed.com",
        }
        # Google domain per country
        _GOOGLE_DOMAINS: dict[str, str] = {
            "fr": "www.google.fr", "gb": "www.google.co.uk", "de": "www.google.de",
            "us": "www.google.com", "nl": "www.google.nl", "es": "www.google.es",
        }

        if site == "linkedin":
            # LinkedIn f_TPR: r86400=24h, r604800=week, r2592000=month
            params = f"keywords={kw}"
            if loc:
                params += f"&location={loc}"
            if max_age_days is not None:
                seconds = max_age_days * 86400
                params += f"&f_TPR=r{seconds}&sortBy=DD"
            return f"https://www.linkedin.com/jobs/search/?{params}"

        if site == "indeed":
            domain = _INDEED_DOMAINS.get(cc, f"{cc}.indeed.com")
            url = f"https://{domain}/jobs?q={kw}&l={loc}"
            if max_age_days is not None:
                url += f"&fromage={max_age_days}"
            return url

        if site == "google_jobs":
            domain = _GOOGLE_DOMAINS.get(cc, "www.google.com")
            query = quote_plus(f"{' '.join(keywords)} emplois {location}") if keywords else "jobs"
            url = f"https://{domain}/search?q={query}&udm=8"
            if max_age_days is not None:
                # Google Jobs: chips=date_posted:today/3days/week/month
                if max_age_days <= 1:
                    url += "&chips=date_posted:today"
                elif max_age_days <= 3:
                    url += "&chips=date_posted:3days"
                elif max_age_days <= 7:
                    url += "&chips=date_posted:week"
                else:
                    url += "&chips=date_posted:month"
            return url

        if site == "welcome_to_the_jungle":
            url = (
                f"https://www.welcometothejungle.com/en/jobs?query={kw}"
                f"&refinementList[offices.country_reference_code][0]={cc.upper()}"
            )
            return url

        if site == "glassdoor":
            # Glassdoor web UI does not reliably support date URL params.
            # Recency filtering is handled by the post-scrape date filter instead.
            return (
                f"https://www.glassdoor.fr/Emploi/emplois.htm"
                f"?suggestChosen=false&clickSource=searchBtn&typedKeyword={kw}&locT=N&locId=0&jobType=all"
            )

        # Unknown site — return base_url unchanged
        logger.debug("[Tier 1] no search URL template for site=%s — using base_url", site)
        return base_url

    def _clean_html(self, html: str, site: str = "") -> str:
        """Strip raw HTML to LLM-friendly markdown (~5-15 KB from ~500 KB).

        Steps:
        1. Parse with lxml
        2. Optionally scope to site-specific content container
        3. Remove noise tags (script, style, nav, footer, etc.)
        4. Strip most attributes (keep href, class, data-job-id, data-entity-urn)
        5. Convert to markdown via markdownify
        6. Collapse whitespace
        7. Truncate to _MAX_CONTENT_CHARS
        """
        try:
            from lxml import etree  # type: ignore
            from lxml.html import fromstring  # type: ignore
            from markdownify import markdownify  # type: ignore
        except ImportError as exc:
            logger.error("[Tier 1] requires lxml and markdownify: %s", exc)
            return html[:_MAX_CONTENT_CHARS]

        try:
            root = fromstring(html)
        except Exception as exc:
            logger.warning("[Tier 1] lxml parse error: %s — falling back to raw truncation", exc)
            return html[:_MAX_CONTENT_CHARS]

        # Step 2: scope to content container if selector defined
        content_selector = SITE_CONTENT_SELECTORS.get(site)
        if content_selector:
            matched_sel = None
            for sel in (s.strip() for s in content_selector.split(",")):
                try:
                    from cssselect import GenericTranslator  # type: ignore
                    xpath = GenericTranslator().css_to_xpath(sel)
                    nodes = root.xpath(xpath)
                    if nodes:
                        root = nodes[0]
                        matched_sel = sel
                        break
                except Exception:
                    continue
            if matched_sel:
                logger.info("[Tier 1] scoped to selector %r — site=%s", matched_sel, site)
            else:
                logger.debug("[Tier 1] no content selector matched for site=%s (using full page)", site)

        # Step 3: remove noise tags
        _NOISE_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"}
        removed = 0
        for tag in _NOISE_TAGS:
            for elem in root.findall(f".//{tag}"):
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)
                    removed += 1
        logger.debug("[Tier 1] removed %d noise elements — site=%s", removed, site)

        # Step 4a: promote data-share-url to href for Google Jobs
        # markdownify only preserves href; data-* attributes are lost in markdown.
        if site == "google_jobs":
            for elem in root.iter():
                share_url = elem.get("data-share-url")
                if share_url and not elem.get("href"):
                    elem.tag = "a"
                    elem.set("href", share_url)

        # Step 4b: strip non-essential attributes
        _KEEP_ATTRS = {"href", "class", "data-job-id", "data-entity-urn"}
        for elem in root.iter():
            attribs = dict(elem.attrib)
            for attr in attribs:
                if attr not in _KEEP_ATTRS:
                    del elem.attrib[attr]

        # Step 5: convert to markdown
        try:
            html_str = etree.tostring(root, encoding="unicode", method="html")
            md = markdownify(html_str, heading_style="ATX", strip=["img"])
        except Exception as exc:
            logger.warning("[Tier 1] markdownify failed: %s — using raw text", exc)
            md = root.text_content() if hasattr(root, "text_content") else ""

        # Step 6: collapse whitespace
        md = re.sub(r"\n{3,}", "\n\n", md)
        md = re.sub(r"[ \t]+", " ", md)

        # Step 7: truncate
        result = md[:_MAX_CONTENT_CHARS]
        if len(md) > _MAX_CONTENT_CHARS:
            logger.info(
                "[Tier 1] truncated content from %d to %d chars — site=%s",
                len(md), _MAX_CONTENT_CHARS, site,
            )
        return result

    async def _extract_jobs(self, cleaned_content: str, site: str = "") -> str:
        """Call Gemini with an extraction-only prompt and return raw text response."""
        prompt_template = EXTRACTION_PROMPTS.get(site) or EXTRACTION_PROMPTS["default"]
        prompt = prompt_template.format(cleaned_content=cleaned_content)
        logger.debug("[Tier 1] extraction prompt length: %d chars — site=%s", len(prompt), site)
        return await self._gemini.generate_text(prompt)

    def _parse_and_sanitize(self, raw_text: str, source_url: str = "") -> list[RawJob]:
        """Parse Gemini output into RawJob list. Kept as public method for testability."""
        parsed = extract_json_from_text(raw_text)
        return parse_jobs_from_json(parsed, source_url=source_url, source_name="scrapling")


__all__ = ["ScraplingFetcher"]
