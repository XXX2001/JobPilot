"""Adaptive browser-use scraper that works on any website.

Uses browser-use + Gemini to extract job listings without hardcoded selectors.
Falls back gracefully on malformed agent output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from backend.config import settings
from backend.models.schemas import JobDetails, RawJob
from backend.scraping.site_prompts import SITE_PROMPTS, format_prompt

logger = logging.getLogger(__name__)


def _extract_json_from_text(text: str) -> Any:
    """Robustly extract a JSON value (array or object) from arbitrary agent output.

    The LLM often wraps JSON in markdown code fences or prefixes it with prose.
    This tries a series of extraction strategies before giving up.
    """
    if not text:
        return None

    # Strategy 1: direct parse
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract from ```json ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: find first [ ... ] block
    array_match = re.search(r"(\[[\s\S]*\])", stripped)
    if array_match:
        try:
            return json.loads(array_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 4: find first { ... } block
    obj_match = re.search(r"(\{[\s\S]*\})", stripped)
    if obj_match:
        try:
            return json.loads(obj_match.group(1))
        except json.JSONDecodeError:
            pass

    return None


class AdaptiveScraper:
    """LLM-powered scraper that works on ANY website.

    Uses browser-use + Gemini to understand page structure and extract job data
    without hardcoded selectors.  All browser-use Agent calls are capped with
    max_steps to respect the Gemini 15 RPM free tier.
    """

    def __init__(self, gemini_api_key: str | None = None) -> None:
        self._api_key = gemini_api_key or settings.GOOGLE_API_KEY

    def _make_llm(self):  # type: ignore[return]
        """Create a ChatGoogle LLM instance for browser-use."""
        try:
            from browser_use import ChatGoogle  # type: ignore

            model = settings.GOOGLE_MODEL or "gemini-2.0-flash"
            return ChatGoogle(model=model, api_key=self._api_key)
        except ImportError:
            logger.warning("browser_use not installed; AdaptiveScraper will be a no-op")
            return None

    async def scrape_job_listings(
        self,
        url: str,
        keywords: list[str],
        max_jobs: int = 20,
        prompt_template: str | None = None,
        site: str | None = None,
        location: str = "",
        country_code: str = "",
    ) -> list[RawJob]:
        """Navigate to a job listing page and extract all jobs.

        Args:
            url: The job listings URL to scrape.
            keywords: Search keywords to filter/search on the page.
            max_jobs: Maximum number of jobs to extract.
            prompt_template: Optional override prompt. Uses 'generic' template if None.
            location: User's target location (e.g. "France", "Paris").
        Returns:
            List of RawJob objects parsed from the agent output.
            Returns an empty list on any error (graceful degradation).
        """
        try:
            from browser_use import Agent, Browser  # type: ignore
        except ImportError:
            logger.warning("browser_use not installed; returning empty list")
            return []

        llm = self._make_llm()
        if llm is None:
            return []

        # Build prompt: use format_prompt(site, ...) for safe default substitution
        # (handles {country_code} and other template vars automatically)
        fmt_kwargs = {
            "keywords": ", ".join(keywords),
            "location": location,
            "max_jobs": str(max_jobs),
            "url": url,
            "country_code": country_code,
        }

        if prompt_template == SITE_PROMPTS["generic"] or prompt_template is None:
            prompt = format_prompt("generic", **fmt_kwargs)
        elif site and SITE_PROMPTS.get(site) == prompt_template:
            # Site-specific template from SITE_PROMPTS — use format_prompt for safe defaults
            prompt = format_prompt(site, **fmt_kwargs)
        else:
            # Custom user-supplied template — best-effort .format()
            try:
                prompt = prompt_template.format(**fmt_kwargs)
            except (KeyError, IndexError):
                logger.warning("Custom prompt template formatting failed for site=%s; using raw template", site)
                prompt = prompt_template
        last_exc: Exception | None = None
        for attempt in range(2):
            # If a site is provided and a saved storage state exists, pass it to Browser
            storage_state = None
            try:
                from pathlib import Path

                if site:
                    storage_path = (
                        Path(settings.jobpilot_data_dir) / "browser_profiles" / site / "state.json"
                    )
                    if storage_path.exists():
                        storage_state = str(storage_path)
            except OSError:
                storage_state = None

            browser = (
                Browser(headless=settings.jobpilot_scraper_headless, storage_state=storage_state)
                if storage_state
                else Browser(headless=settings.jobpilot_scraper_headless)
            )
            try:
                agent = Agent(
                    task=prompt,
                    llm=llm,
                    browser=browser,
                    max_steps=20,
                )
                result = await asyncio.wait_for(agent.run(), timeout=180)
                return self._parse_agent_result(result, source_url=url)
            except Exception as exc:
                last_exc = exc
                backoff = 2**attempt * 2  # 2s, 4s, 8s
                logger.warning(
                    "browser-use agent failed for %s (attempt %d/2): %s — retrying in %ds",
                    url,
                    attempt + 1,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
            finally:
                try:
                    await browser.stop()
                except Exception:
                    pass

        logger.error("browser-use agent exhausted retries for %s: %s", url, last_exc)
        return []

    async def scrape_job_details(self, job_url: str, site: str | None = None) -> JobDetails | None:
        """Navigate to a single job posting and extract full details.

        Args:
            job_url: The direct URL of the job posting.

        Returns:
            JobDetails if extraction succeeds, None on failure.
        """
        try:
            from browser_use import Agent, Browser  # type: ignore
        except ImportError:
            logger.warning("browser_use not installed; returning None")
            return None

        llm = self._make_llm()
        if llm is None:
            return None

        prompt = f"""
Navigate to: {job_url}

This is a job posting page. Extract the FULL details and return as JSON:
{{
  "title": "full job title",
  "company": "company name",
  "location": "full location (city, country, remote status)",
  "salary": "salary range if shown (null if not)",
  "description": "FULL job description text",
  "requirements": ["list of requirements/qualifications"],
  "benefits": ["list of benefits if shown"],
  "apply_url": "the direct apply button/link URL",
  "apply_method": "easy_apply OR redirect OR email OR form",
  "posted_date": "when posted (null if not shown)"
}}

Do NOT click the apply button. Just extract and return JSON.
"""

        # Try to load saved storage_state for the given site if present
        storage_state = None
        try:
            from pathlib import Path

            if site:
                storage_path = (
                    Path(settings.jobpilot_data_dir) / "browser_profiles" / site / "state.json"
                )
                if storage_path.exists():
                    storage_state = str(storage_path)
        except Exception:
            storage_state = None

        browser = (
            Browser(headless=True, storage_state=storage_state)
            if storage_state
            else Browser(headless=True)
        )
        try:
            agent = Agent(
                task=prompt,
                llm=llm,
                browser=browser,
                max_steps=8,
            )
            result = await asyncio.wait_for(agent.run(), timeout=90)
        except Exception as exc:
            logger.warning("browser-use detail agent failed for %s: %s", job_url, exc)
            return None
        finally:
            try:
                    await browser.stop()
            except Exception:
                pass

        return self._parse_job_details(result, job_url=job_url)

    def _parse_agent_result(
        self,
        result: Any,
        source_url: str = "",
    ) -> list[RawJob]:
        """Parse a browser-use agent result into a list of RawJob objects.

        Handles cases where the result is a string, an object with extracted_content,
        or any other format. Returns an empty list on malformed input — never raises.
        """
        raw_text: str = ""

        try:
            # browser-use AgentHistoryList has a final_result() method
            if hasattr(result, "final_result"):
                raw_text = result.final_result() or ""
            elif hasattr(result, "extracted_content"):
                raw_text = str(result.extracted_content or "")
            elif isinstance(result, str):
                raw_text = result
            else:
                raw_text = str(result)
        except Exception as exc:
            logger.warning("Could not extract text from agent result: %s", exc)
            return []

        parsed = _extract_json_from_text(raw_text)
        if parsed is None:
            logger.warning("Could not parse JSON from agent result (url=%s)", source_url)
            return []

        # Normalise: may be a dict with a 'jobs' key, or a bare list
        if isinstance(parsed, dict):
            for key in ("jobs", "results", "listings", "data"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                # Single job dict?
                parsed = [parsed]

        if not isinstance(parsed, list):
            logger.warning("Agent result is not a list (url=%s)", source_url)
            return []

        jobs: list[RawJob] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                job = RawJob(
                    title=str(item.get("title") or "Unknown Title"),
                    company=str(item.get("company") or "Unknown Company"),
                    location=str(item.get("location") or ""),
                    salary_text=str(item.get("salary") or ""),
                    description=str(
                        item.get("description_preview") or item.get("description") or ""
                    ),
                    url=str(item.get("apply_url") or item.get("url") or source_url),
                    apply_url=str(item.get("apply_url") or item.get("url") or source_url),
                    apply_method=str(item.get("apply_method") or ""),
                    source_name="browser",
                    raw_data=item,
                )
                jobs.append(job)
            except Exception as exc:
                logger.debug("Skipping malformed job item: %s — %s", item, exc)
                continue

        return jobs

    def _parse_job_details(self, result: Any, job_url: str = "") -> JobDetails | None:
        """Parse a browser-use agent result for a single job's full details."""
        raw_text: str = ""
        try:
            if hasattr(result, "final_result"):
                raw_text = result.final_result() or ""
            elif hasattr(result, "extracted_content"):
                raw_text = str(result.extracted_content or "")
            elif isinstance(result, str):
                raw_text = result
            else:
                raw_text = str(result)
        except Exception:
            return None

        parsed = _extract_json_from_text(raw_text)
        if not isinstance(parsed, dict):
            return None

        try:
            return JobDetails(
                title=str(parsed.get("title") or "Unknown Title"),
                company=str(parsed.get("company") or "Unknown Company"),
                location=str(parsed.get("location") or ""),
                description=str(parsed.get("description") or ""),
                url=job_url,
                apply_url=str(parsed.get("apply_url") or job_url),
                apply_method=str(parsed.get("apply_method") or ""),
            )
        except Exception as exc:
            logger.warning("Failed to build JobDetails from agent result: %s", exc)
            return None


__all__ = ["AdaptiveScraper"]
