"""Greenhouse adapter: IDs, dates, conditional GET, blocked responses. No network."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import pytest

from backend.scraping.sources import ADAPTERS
from backend.scraping.sources.base import PollResult, SourceError, classify_response
from backend.scraping.sources.greenhouse import poll

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "greenhouse_mirakl.json"
CFG = {"adapter": "greenhouse", "slug": "mirakl"}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def fixture_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/json", "etag": 'W/"fresh"'},
    )


async def test_greenhouse_parses_ids_and_dates():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return fixture_response(request)

    async with client_for(handler) as client:
        res = await poll(client, CFG, None, None)

    assert isinstance(res, PollResult)
    assert not res.not_modified and not res.truncated
    assert res.etag == 'W/"fresh"'
    assert [j.external_id for j in res.jobs] == ["5589254004", "6233307004", "6212914004"]
    # first_published 2025-07-15T10:20:22-04:00 -> naive UTC, to the second
    assert res.jobs[0].posted_at == datetime(2025, 7, 15, 14, 20, 22)
    assert res.jobs[1].posted_at == datetime(2026, 10, 23, 15, 22, 52)
    assert res.jobs[2].posted_at == datetime(2026, 10, 1, 14, 10, 42)

    job = res.jobs[2]
    assert job.title == "Brand Design Intern or Apprentice"
    assert job.company == "Mirakl"
    assert job.location == "Paris, France"
    assert job.url == job.apply_url == "https://job-boards.greenhouse.io/mirakl/jobs/6212914004"
    assert job.source_name == "greenhouse"
    # content is entity-escaped HTML in the API; the description is plain markdown
    assert "About Mirakl" in job.description
    assert "&lt;" not in job.description and "<div" not in job.description
    assert "<p>" not in job.description

    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/mirakl/jobs?content=true"
    assert "if-none-match" not in seen[0].headers


async def test_greenhouse_sends_weak_etag_verbatim_and_handles_304():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(304)

    async with client_for(handler) as client:
        res = await poll(client, CFG, 'W/"abc"', None)

    assert res.not_modified and res.jobs == []
    assert res.etag == 'W/"abc"'
    assert seen[0].headers["if-none-match"] == 'W/"abc"'


async def test_greenhouse_html_403_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            403, text="<html>Just a moment...</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_greenhouse_html_200_challenge_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            200, text="<html>Verify you are human</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


@pytest.mark.parametrize(
    "status,kind",
    [(401, "auth"), (403, "blocked"), (429, "blocked"), (999, "blocked"), (500, "transient"),
     (503, "transient"), (404, "transient")],
)
async def test_greenhouse_status_codes_map_to_error_kinds(status, kind):
    def handler(request):
        return httpx.Response(status, json={"error": "x"})

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == kind


async def test_greenhouse_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_greenhouse_unexpected_body_is_transient_not_empty_success():
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_greenhouse_skips_items_without_id_or_title():
    def handler(request):
        return httpx.Response(
            200,
            json={"jobs": [{"title": "No id"}, {"id": 7}, {"id": 8, "title": "Kept"}, "junk"]},
        )

    async with client_for(handler) as client:
        res = await poll(client, CFG, None, None)
    assert [j.external_id for j in res.jobs] == ["8"]
    assert res.jobs[0].company == "mirakl" and res.jobs[0].posted_at is None


def test_classify_response_passes_json_200():
    classify_response(httpx.Response(200, json={"jobs": []}))


def test_greenhouse_is_registered():
    assert ADAPTERS["greenhouse"] is poll
