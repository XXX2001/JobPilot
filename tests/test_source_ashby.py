"""Ashby adapter: IDs, dates, conditional GET, blocked responses. No network."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import pytest

from backend.scraping.sources import ADAPTERS
from backend.scraping.sources.ashby import poll
from backend.scraping.sources.base import PollResult, SourceError

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "ashby_alan.json"
CFG = {"adapter": "ashby", "slug": "alan"}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ashby_parses_ids_and_dates():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            content=FIXTURE.read_bytes(),
            headers={"content-type": "application/json", "etag": 'W/"fresh"'},
        )

    async with client_for(handler) as client:
        res = await poll(client, CFG, None, None)

    assert isinstance(res, PollResult)
    assert not res.not_modified and not res.truncated
    assert res.etag == 'W/"fresh"'
    assert [j.external_id for j in res.jobs] == [
        "40f9c790-bdae-4329-b292-6151862c4b4c",
        "d457e0f1-2418-4759-b4ed-e41fdff50bf0",
        "a5973d82-d4f2-48f8-8c96-70de49f14c9b",
    ]
    # publishedAt 2026-09-09T13:17:05.420+00:00 -> naive UTC
    assert res.jobs[0].posted_at == datetime(2026, 9, 9, 13, 17, 5, 420000)
    assert res.jobs[1].posted_at == datetime(2026, 8, 3, 8, 33, 52, 99000)

    job = res.jobs[1]
    assert job.title == "CTO Founder Associate - internship"
    assert job.company == "alan"  # the board API carries no company name
    assert job.location == "Paris, France"
    assert job.url == "https://jobs.ashbyhq.com/alan/d457e0f1-2418-4759-b4ed-e41fdff50bf0"
    assert job.apply_url == job.url + "/application"
    assert job.source_name == "ashby"
    assert len(job.description) > 1000 and "<p" not in job.description

    assert str(seen[0].url) == "https://api.ashbyhq.com/posting-api/job-board/alan"
    assert "if-none-match" not in seen[0].headers


async def test_ashby_sends_weak_etag_verbatim_and_handles_304():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(304)

    async with client_for(handler) as client:
        res = await poll(client, CFG, 'W/"abc"', None)

    assert res.not_modified and res.jobs == []
    assert res.etag == 'W/"abc"'
    assert seen[0].headers["if-none-match"] == 'W/"abc"'


async def test_ashby_html_403_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            403, text="<html>Just a moment...</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_ashby_html_200_challenge_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            200, text="<html>Verify you are human</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_ashby_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_ashby_unexpected_body_is_transient_not_empty_success():
    async with client_for(lambda request: httpx.Response(200, json={"content": []})) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_ashby_skips_items_without_id_or_title():
    body = {"jobs": [{"title": "No id"}, {"id": "7"}, {"id": "8", "title": "Kept"}, "junk"]}

    async with client_for(lambda request: httpx.Response(200, json=body)) as client:
        res = await poll(client, CFG, None, None)
    assert [j.external_id for j in res.jobs] == ["8"]
    assert res.jobs[0].posted_at is None and res.jobs[0].description == ""


def test_ashby_is_registered():
    assert ADAPTERS["ashby"] is poll
