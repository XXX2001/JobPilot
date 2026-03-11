"""Shared JSON extraction and job-parsing utilities for all scraper tiers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from backend.models.schemas import RawJob
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Any:
    """Robustly extract a JSON value (array or object) from arbitrary LLM output.

    Tries a series of extraction strategies before giving up.
    Moved from adaptive_scraper.py for reuse across scraper tiers.
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


def parse_jobs_from_json(
    parsed: Any,
    source_url: str = "",
    source_name: str = "browser",
) -> list[RawJob]:
    """Convert parsed JSON (list or dict with jobs key) into sanitized RawJob objects.

    Extracted from AdaptiveScraper._parse_agent_result() for reuse across tiers.
    Never raises — returns empty list on any malformed input.
    """
    if parsed is None:
        logger.warning("Could not parse JSON from result (url=%s)", source_url)
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
        logger.warning("Result is not a list (url=%s)", source_url)
        return []

    jobs: list[RawJob] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            raw_apply = str(item.get("apply_url") or "")
            raw_url = str(item.get("url") or "")
            # Resolve relative URLs (e.g. /viewjob?jk=abc) against source origin
            if raw_apply.startswith("/") and source_url:
                parsed_src = urlparse(source_url)
                raw_apply = urljoin(f"{parsed_src.scheme}://{parsed_src.netloc}", raw_apply)
            if raw_url.startswith("/") and source_url:
                parsed_src = urlparse(source_url)
                raw_url = urljoin(f"{parsed_src.scheme}://{parsed_src.netloc}", raw_url)
            # Prefer per-job URLs; only fall back to source_url for the
            # generic url field, never for apply_url (source_url is typically
            # a search page, not a specific job listing).
            clean_apply = sanitize_url(raw_apply)
            clean_url = sanitize_url(raw_url) or sanitize_url(raw_apply) or sanitize_url(source_url)
            job = RawJob(
                title=sanitize_for_prompt(
                    str(item.get("title") or "Unknown Title"), 300, "title"
                ),
                company=sanitize_for_prompt(
                    str(item.get("company") or "Unknown Company"), 200, "company"
                ),
                location=sanitize_for_prompt(
                    str(item.get("location") or ""), 200, "location"
                ),
                salary_text=sanitize_for_prompt(
                    str(item.get("salary") or ""), 100, "salary"
                ),
                description=sanitize_for_prompt(
                    str(
                        item.get("description") or item.get("description_preview") or ""
                    ),
                    5000,
                    "description",
                ),
                url=clean_url,
                apply_url=clean_apply or clean_url,
                apply_method=str(item.get("apply_method") or ""),
                source_name=source_name,
                raw_data=item,
            )
            jobs.append(job)
        except Exception as exc:
            logger.debug("Skipping malformed job item: %s — %s", item, exc)
            continue

    return jobs


__all__ = ["extract_json_from_text", "parse_jobs_from_json"]
