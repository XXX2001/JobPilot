"""SmartRecruiters postings API: page 1 of the list, newest first, no descriptions."""

from __future__ import annotations

from datetime import datetime

import httpx

from backend.models.schemas import RawJob
from backend.scraping.json_utils import _parse_posted_date
from backend.scraping.sources.base import ADAPTERS, PollResult, SourceError, classify_response
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url

PAGE_SIZE = 100
URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={limit}"
JOB_URL = "https://jobs.smartrecruiters.com/{slug}/{id}"


async def poll(
    client: httpx.AsyncClient,
    cfg: dict,
    etag: str | None,
    last_scraped_at: datetime | None,
) -> PollResult:
    slug = cfg["slug"]
    headers = {"If-None-Match": etag} if etag else {}
    try:
        r = await client.get(URL.format(slug=slug, limit=PAGE_SIZE), headers=headers)
    except httpx.HTTPError as exc:
        raise SourceError("transient", f"{type(exc).__name__}: {exc}") from exc

    if r.status_code == 304:
        return PollResult(not_modified=True, etag=etag)
    classify_response(r)
    try:
        body = r.json()
        items = body["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("transient", f"unexpected body: {type(exc).__name__}") from exc
    if not isinstance(items, list):
        raise SourceError("transient", "unexpected body: content is not a list")

    jobs = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None or not item.get("name"):
            continue
        url = sanitize_url(JOB_URL.format(slug=slug, id=item["id"]))
        jobs.append(
            RawJob(
                external_id=str(item["id"]),
                title=sanitize_for_prompt(str(item["name"]).strip(), 300, "title"),
                company=sanitize_for_prompt(
                    str((item.get("company") or {}).get("name") or slug), 200, "company"
                ),
                location=sanitize_for_prompt(
                    str((item.get("location") or {}).get("fullLocation") or ""), 200, "location"
                ),
                # The list carries no description: scoring uses the title until the
                # detail is fetched on selection.
                description="",
                url=url,
                apply_url=url,
                posted_at=_parse_posted_date(item.get("releasedDate")),
                source_name="smartrecruiters",
            )
        )
    total = body.get("totalFound")
    truncated = isinstance(total, int) and total > PAGE_SIZE
    return PollResult(jobs=jobs, etag=r.headers.get("etag"), truncated=truncated)


ADAPTERS["smartrecruiters"] = poll
