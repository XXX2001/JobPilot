"""SmartRecruiters adapter: IDs, dates, truncation, conditional GET, blocks. No network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from backend.scraping.sources import ADAPTERS
from backend.scraping.sources.base import PollResult, SourceError
from backend.scraping.sources.smartrecruiters import poll

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "smartrecruiters_ubisoft2.json"
CFG = {"adapter": "smartrecruiters", "slug": "Ubisoft2"}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body, headers={"etag": '"fresh"'})


async def test_smartrecruiters_parses_ids_and_dates():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return json_response(json.loads(FIXTURE.read_text()))

    async with client_for(handler) as client:
        res = await poll(client, CFG, None, None)

    assert isinstance(res, PollResult)
    assert not res.not_modified
    assert res.etag == '"fresh"'
    assert [j.external_id for j in res.jobs] == [
        "744000161565892",
        "744000161531273",
        "744000161524819",
    ]
    # releasedDate 2026-11-13T19:51:33.734Z -> naive UTC
    assert res.jobs[0].posted_at == datetime(2026, 11, 13, 19, 51, 33, 734000)
    assert res.jobs[2].posted_at == datetime(2026, 11, 13, 15, 58, 49, 344000)

    job = res.jobs[2]
    assert job.title == "Prompt Specialist"
    assert job.company == "Ubisoft"
    assert job.location == "Montreal, QC, Canada"
    assert job.url == job.apply_url == "https://jobs.smartrecruiters.com/Ubisoft2/744000161524819"
    assert job.description == ""  # the list has none
    assert job.source_name == "smartrecruiters"

    assert (
        str(seen[0].url)
        == "https://api.smartrecruiters.com/v1/companies/Ubisoft2/postings?limit=100"
    )
    assert "if-none-match" not in seen[0].headers
    assert len(seen) == 1  # page 1 only


async def test_smartrecruiters_marks_truncated_when_total_exceeds_page():
    body = json.loads(FIXTURE.read_text())
    assert body["totalFound"] == 288

    async with client_for(lambda request: json_response(body)) as client:
        assert (await poll(client, CFG, None, None)).truncated is True

    body["totalFound"] = 100
    async with client_for(lambda request: json_response(body)) as client:
        assert (await poll(client, CFG, None, None)).truncated is False


async def test_smartrecruiters_sends_etag_verbatim_and_handles_304():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(304)

    async with client_for(handler) as client:
        res = await poll(client, CFG, 'W/"abc"', None)

    assert res.not_modified and res.jobs == []
    assert res.etag == 'W/"abc"'
    assert seen[0].headers["if-none-match"] == 'W/"abc"'


async def test_smartrecruiters_html_403_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            403, text="<html>Just a moment...</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_smartrecruiters_html_200_challenge_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            200, text="<html>Verify you are human</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_smartrecruiters_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_smartrecruiters_unexpected_body_is_transient_not_empty_success():
    async with client_for(lambda request: httpx.Response(200, json={"jobs": []})) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_smartrecruiters_skips_items_without_id_or_name():
    body = {"content": [{"name": "No id"}, {"id": "7"}, {"id": "8", "name": "Kept"}, "junk"]}

    async with client_for(lambda request: json_response(body)) as client:
        res = await poll(client, CFG, None, None)
    assert [j.external_id for j in res.jobs] == ["8"]
    assert res.jobs[0].company == "Ubisoft2" and res.jobs[0].posted_at is None
    assert res.truncated is False  # no totalFound


def test_smartrecruiters_is_registered():
    assert ADAPTERS["smartrecruiters"] is poll
