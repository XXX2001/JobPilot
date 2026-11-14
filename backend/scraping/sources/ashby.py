"""Ashby posting API: one conditional GET returns the whole job board."""

from __future__ import annotations

from datetime import datetime

import httpx

from backend.models.schemas import RawJob
from backend.scraping.json_utils import _parse_posted_date
from backend.scraping.sources.base import ADAPTERS, PollResult, SourceError, classify_response
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url

URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
MAX_DESCRIPTION = 20000  # RawJob.description limit


async def poll(
    client: httpx.AsyncClient,
    cfg: dict,
    etag: str | None,
    last_scraped_at: datetime | None,
) -> PollResult:
    slug = cfg["slug"]
    headers = {"If-None-Match": etag} if etag else {}
    try:
        r = await client.get(URL.format(slug=slug), headers=headers)
    except httpx.HTTPError as exc:
        raise SourceError("transient", f"{type(exc).__name__}: {exc}") from exc

    if r.status_code == 304:
        return PollResult(not_modified=True, etag=etag)
    classify_response(r)
    try:
        items = r.json()["jobs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("transient", f"unexpected body: {type(exc).__name__}") from exc
    if not isinstance(items, list):
        raise SourceError("transient", "unexpected body: jobs is not a list")

    jobs = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None or not item.get("title"):
            continue
        url = sanitize_url(str(item.get("jobUrl") or ""))
        jobs.append(
            RawJob(
                external_id=str(item["id"]),
                title=sanitize_for_prompt(str(item["title"]).strip(), 300, "title"),
                # The board API carries no company name.
                company=sanitize_for_prompt(str(slug), 200, "company"),
                location=sanitize_for_prompt(str(item.get("location") or ""), 200, "location"),
                description=sanitize_for_prompt(
                    str(item.get("descriptionPlain") or "").strip(), MAX_DESCRIPTION, "description"
                ),
                url=url,
                apply_url=sanitize_url(str(item.get("applyUrl") or "")) or url,
                posted_at=_parse_posted_date(item.get("publishedAt")),
                source_name="ashby",
            )
        )
    return PollResult(jobs=jobs, etag=r.headers.get("etag"))


ADAPTERS["ashby"] = poll
