"""LinkedIn guest job search: a logged-out GET returns an HTML fragment of ten job cards.

No account, no cookies (spec D7). There is no ETag: each poll asks for the jobs
posted in a time window, newest first, page by page (at most ``MAX_PAGES``),
and the store diffs by ID. Cards carry no
description. Sustained polling of this endpoint is not proven: anything that is
not a card list counts as blocked so the poller backs off.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from lxml import html

from backend.models.schemas import RawJob
from backend.scraping.json_utils import _parse_posted_date
from backend.scraping.sources.base import (
    ADAPTERS,
    ITEM_ERRORS,
    PollResult,
    SourceError,
    send_logged_out,
    skip_item,
)
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url
from backend.utils.time import naive_utc_now

URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
URN_PREFIX = "urn:li:jobPosting:"
MIN_WINDOW_S = 1800
MAX_WINDOW_S = 86400
PAGE_SIZE = 10  # cards per request; the endpoint takes no page-size parameter
MAX_PAGES = 5


def _window_seconds(last_scraped_at: datetime | None) -> int:
    """Twice the gap since the last poll, so a late or failed poll loses nothing."""
    if last_scraped_at is None:
        return MAX_WINDOW_S  # never polled: seed with the widest window
    gap = (naive_utc_now() - last_scraped_at).total_seconds()
    return int(max(MIN_WINDOW_S, min(2 * gap, MAX_WINDOW_S)))


def _text(card, xpath: str) -> str:
    return " ".join(card.xpath(f"string({xpath})").split())


def _posted_at(card) -> datetime | None:
    """The ``datetime`` attribute is a bare day; '15 minutes ago' adds the time."""
    day = _parse_posted_date(next(iter(card.xpath(".//time/@datetime")), None))
    relative = _parse_posted_date(_text(card, ".//time"))
    if relative and (day is None or abs(relative - day) < timedelta(days=2)):
        return relative
    return day  # no relative text, or one that contradicts the attribute


_NO_MARKUP = re.compile(r"<!DOCTYPE[^>]*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)


async def _cards(client: httpx.AsyncClient, cfg: dict, window_s: int, start: int) -> list:
    """One page of job cards; ``[]`` when the window holds nothing (more)."""
    # Redirects are not followed: the only one this endpoint sends is the authwall.
    r = await send_logged_out(
        client,
        "GET",
        URL,
        params={
            "keywords": str(cfg["keywords"]),
            "location": str(cfg["location"]),
            "f_TPR": f"r{window_s}",
            "sortBy": "DD",
            "start": start,
        },
        headers={"User-Agent": USER_AGENT},
    )

    status = r.status_code
    if status in (403, 429, 999):
        raise SourceError("blocked", f"HTTP {status}")
    if 300 <= status < 400:
        raise SourceError("blocked", f"HTTP {status} -> {r.headers.get('location', '')[:100]}")
    if status != 200:
        raise SourceError("transient", f"HTTP {status}")

    # An empty window comes back as "" or as a bare ``<!DOCTYPE html><!---->``.
    body = _NO_MARKUP.sub("", r.text).strip()
    if not body:
        return []  # nothing (more) posted in the window
    try:
        tree = html.fromstring(body)
    except (ValueError, html.etree.ParserError) as exc:
        raise SourceError("blocked", f"unparseable body: {type(exc).__name__}") from exc
    cards = tree.xpath(f"//*[starts-with(@data-entity-urn, '{URN_PREFIX}')]")
    if not cards:
        # A login or challenge page: nothing in it may be read as jobs.
        raise SourceError("blocked", "HTTP 200 without job cards")
    return cards


async def poll(
    client: httpx.AsyncClient,
    cfg: dict,
    etag: str | None,
    last_scraped_at: datetime | None,
) -> PollResult:
    # A page that fails fails the whole poll: nothing is stored, the poller backs
    # off, and the next window (twice the gap) covers what this one would have.
    window_s = _window_seconds(last_scraped_at)
    cards: list = []
    truncated = True  # until a short page says the window was read to its end
    for page in range(MAX_PAGES):
        batch = await _cards(client, cfg, window_s, page * PAGE_SIZE)
        cards += batch
        if len(batch) < PAGE_SIZE:
            truncated = False
            break

    jobs = []
    for card in cards:
        job_id = card.get("data-entity-urn")[len(URN_PREFIX) :].strip()
        title = _text(card, ".//h3")
        if not job_id.isdigit() or not title:
            continue
        href = next(iter(card.xpath(".//a[contains(@class, 'base-card__full-link')]/@href")), "")
        try:
            # The query string is only tracking (position, refId, trackingId).
            url = sanitize_url(urlsplit(href)._replace(query="", fragment="").geturl())
        except ValueError:  # e.g. an unclosed IPv6 bracket: the ID still gives the URL
            url = ""
        url = url or f"https://www.linkedin.com/jobs/view/{job_id}"
        try:
            jobs.append(
                RawJob(
                    external_id=f"li-{job_id}",
                    title=sanitize_for_prompt(title, 300, "title"),
                    company=sanitize_for_prompt(_text(card, ".//h4"), 200, "company"),
                    location=sanitize_for_prompt(
                        _text(card, ".//*[contains(@class, 'job-search-card__location')]"),
                        200,
                        "location",
                    ),
                    url=url,
                    apply_url=url,
                    posted_at=_posted_at(card),
                    source_name="linkedin_guest",
                )
            )
        except ITEM_ERRORS as exc:
            skip_item("linkedin_guest", exc)
    return PollResult(jobs=jobs, truncated=truncated)


ADAPTERS["linkedin_guest"] = poll
