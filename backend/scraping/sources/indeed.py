"""Indeed mobile GraphQL API: one POST returns the jobs published in a time window.

There is no ETag: each poll asks for the last N hours and the store diffs by ID.

HEADERS, LANGUAGES and the job-search query fields are vendored from
python-jobspy 1.4.1 (jobspy/indeed/constant.py),
Copyright (c) 2023 Cullen Watson, MIT License — https://github.com/speedyapply/JobSpy
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from markdownify import markdownify

from backend.models.schemas import RawJob
from backend.scraping.sources.base import (
    ADAPTERS,
    ITEM_ERRORS,
    PollResult,
    SourceError,
    classify_response,
    send_logged_out,
    skip_item,
)
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url
from backend.utils.time import naive_utc_now

URL = "https://apis.indeed.com/graphql"
LIMIT = 100  # the API's page cap; a full page means the window overflowed
MAX_WINDOW_HOURS = 72
MAX_DESCRIPTION = 20000  # RawJob.description limit

# --- vendored from python-jobspy 1.4.1 (MIT) ---------------------------------
# The api key is the public one shipped in Indeed's iOS app, not a secret of ours.
HEADERS = {
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Mobile/15E148 Indeed App 193.1"
    ),
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
}

# Country code -> language of the indeed-locale header (default "en").
LANGUAGES = {
    "AR": "es", "AT": "de", "BE": "nl", "BR": "pt", "CH": "de", "CL": "es", "CN": "zh",
    "CO": "es", "CR": "es", "CZ": "cs", "DE": "de", "DK": "da", "EC": "es", "ES": "es",
    "FI": "fi", "FR": "fr", "GR": "el", "HU": "hu", "ID": "id", "IL": "he", "IT": "it",
    "JP": "ja", "KR": "ko", "LU": "fr", "MA": "fr", "MX": "es", "NL": "nl", "NO": "nb",
    "PA": "es", "PE": "es", "PL": "pl", "PT": "pt", "RO": "ro", "SE": "sv", "TH": "th",
    "TR": "tr", "TW": "zh", "UA": "uk", "UY": "es", "VE": "es", "VN": "vi",
}  # fmt: skip
SUBDOMAINS = {"MY": "malaysia", "GB": "uk", "US": "www"}  # others use the country code

# jobspy's query cut down to the fields stored, plus indeedApply (apply method);
# sorted by date and filtered on dateOnIndeed instead of jobspy's RELEVANCE.
QUERY = """
query GetJobData {{
    jobSearch(
        what: {what}
        location: {{where: {where}, radius: {radius}, radiusUnit: KILOMETERS}}
        limit: {limit}
        sort: DATE
        filters: [{{ date: {{ field: "dateOnIndeed", start: "{hours}h" }} }}]
    ) {{
        pageInfo {{ nextCursor }}
        results {{
            job {{
                key
                title
                sourceEmployerName
                dateOnIndeed
                description {{ html }}
                location {{ countryCode admin1Code city }}
                employer {{ name }}
                indeedApply {{ scopes }}
                recruit {{ viewJobUrl }}
            }}
        }}
    }}
}}
"""
# --- end vendored -------------------------------------------------------------


def _window_hours(cfg: dict, last_scraped_at: datetime | None) -> int:
    """Hours to ask for: the configured window, widened to cover any downtime."""
    hours = int(cfg.get("window_hours") or 2)
    if last_scraped_at is not None:
        gap = (naive_utc_now() - last_scraped_at).total_seconds() / 3600
        hours = max(hours, math.ceil(gap))
    return max(1, min(hours, MAX_WINDOW_HOURS))


def _is_external(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host != "indeed.com" and not host.endswith(".indeed.com")


def _from_epoch_ms(ms: object) -> datetime | None:
    """Epoch ms -> naive UTC; integer timedelta keeps the milliseconds exact."""
    if not isinstance(ms, int):
        return None
    try:
        return datetime(1970, 1, 1) + timedelta(milliseconds=ms)
    except OverflowError:  # a timestamp outside years 1..9999
        return None


async def poll(
    client: httpx.AsyncClient,
    cfg: dict,
    etag: str | None,
    last_scraped_at: datetime | None,
) -> PollResult:
    country = str(cfg.get("country") or "FR").upper()
    headers = HEADERS | {
        "indeed-co": country,
        "indeed-locale": f"{LANGUAGES.get(country, 'en')}-{country}",
    }
    # json.dumps quotes and escapes: a search term cannot break out of its string.
    query = QUERY.format(
        what=json.dumps(str(cfg["what"]), ensure_ascii=False),
        where=json.dumps(str(cfg["where"]), ensure_ascii=False),
        radius=int(cfg.get("radius_km") or 30),
        limit=LIMIT,
        hours=_window_hours(cfg, last_scraped_at),
    )
    r = await send_logged_out(client, "POST", URL, headers=headers, json={"query": query})

    classify_response(r)  # 401 (app key revoked) -> auth
    try:
        body = r.json()
        if body.get("errors"):
            raise SourceError("transient", f"GraphQL errors: {str(body['errors'])[:200]}")
        results = body["data"]["jobSearch"]["results"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError("transient", f"unexpected body: {type(exc).__name__}") from exc
    if not isinstance(results, list):
        raise SourceError("transient", "unexpected body: results is not a list")

    base = f"https://{SUBDOMAINS.get(country, country.lower())}.indeed.com"
    jobs = []
    for result in results:
        item = result.get("job") if isinstance(result, dict) else None
        if not isinstance(item, dict) or not item.get("key") or not item.get("title"):
            continue
        try:
            url = f"{base}/viewjob?jk={item['key']}"
            external = sanitize_url(str((item.get("recruit") or {}).get("viewJobUrl") or ""))
            if not _is_external(external):
                external = ""
            scopes = (item.get("indeedApply") or {}).get("scopes")
            loc = item.get("location") or {}
            description = markdownify(
                str((item.get("description") or {}).get("html") or ""),
                heading_style="ATX",
                strip=["img"],
            ).strip()
            jobs.append(
                RawJob(
                    external_id=f"in-{item['key']}",
                    title=sanitize_for_prompt(str(item["title"]).strip(), 300, "title"),
                    company=sanitize_for_prompt(
                        str(
                            (item.get("employer") or {}).get("name")
                            or item.get("sourceEmployerName")
                            or ""
                        ),
                        200,
                        "company",
                    ),
                    location=sanitize_for_prompt(
                        ", ".join(
                            str(loc[k]) for k in ("city", "admin1Code", "countryCode") if loc.get(k)
                        ),
                        200,
                        "location",
                    ),
                    description=sanitize_for_prompt(description, MAX_DESCRIPTION, "description"),
                    url=url,
                    apply_url=external or url,
                    apply_method="indeed_apply" if scopes and not external else "external",
                    posted_at=_from_epoch_ms(item.get("dateOnIndeed")),
                    source_name="indeed",
                    country=country,
                )
            )
        except ITEM_ERRORS as exc:
            skip_item("indeed", exc)
    return PollResult(jobs=jobs, truncated=len(results) >= LIMIT)


ADAPTERS["indeed"] = poll
