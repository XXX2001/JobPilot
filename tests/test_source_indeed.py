"""Indeed adapter: IDs, ms timestamps, time window, truncation, errors. No network."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from backend.scraping.sources import ADAPTERS, indeed
from backend.scraping.sources.base import PollResult, SourceError
from backend.scraping.sources.indeed import poll

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "indeed_search.json"
CFG = {
    "adapter": "indeed",
    "what": "ingénieur",
    "where": "Paris",
    "country": "FR",
    "radius_km": 30,
    "window_hours": 2,
}
NOW = datetime(2026, 11, 14, 12, 0, 0)
JSON = {"content-type": "application/json"}


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch) -> datetime:
    monkeypatch.setattr(indeed, "naive_utc_now", lambda: NOW)
    return NOW


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def recording(seen: list[httpx.Request], body: bytes | None = None):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=body or FIXTURE.read_bytes(), headers=JSON)

    return handler


def sent_query(request: httpx.Request) -> str:
    return json.loads(request.content)["query"]


def body_with(n: int) -> bytes:
    job = json.loads(FIXTURE.read_bytes())["data"]["jobSearch"]["results"][0]["job"]
    results = [{"job": job | {"key": f"k{i:04d}"}} for i in range(n)]
    return json.dumps({"data": {"jobSearch": {"results": results}}}).encode()


async def test_indeed_parses_ids_and_fields():
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        res = await poll(client, CFG, None, None)

    assert isinstance(res, PollResult)
    assert not res.not_modified and not res.truncated and res.etag is None
    assert [j.external_id for j in res.jobs] == [
        "in-3f1c9a7be2d04a61",
        "in-a84e02d5c7716b90",
        "in-5d20b6f3e9a1c478",
    ]
    job = res.jobs[0]
    assert job.title == "Ingénieur Robotique H/F"
    assert job.company == "Exotec"
    assert job.location == "Paris, IDF, FR"
    assert job.url == "https://fr.indeed.com/viewjob?jk=3f1c9a7be2d04a61"
    assert job.source_name == "indeed" and job.country == "FR"
    assert "ROS 2" in job.description and "<p" not in job.description
    # employer.name wins over sourceEmployerName; a null employer falls back
    assert res.jobs[2].company == "Safran Electronics & Defense"
    assert res.jobs[1].company == "Actemium Paris"


async def test_indeed_request_shape():
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        await poll(client, CFG, "ignored-etag", None)

    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://apis.indeed.com/graphql"
    assert req.headers["indeed-co"] == "FR"
    assert req.headers["indeed-locale"] == "fr-FR"
    assert req.headers["indeed-api-key"] and "Indeed App" in req.headers["user-agent"]
    assert "if-none-match" not in req.headers
    q = sent_query(req)
    assert 'what: "ingénieur"' in q
    assert 'location: {where: "Paris", radius: 30, radiusUnit: KILOMETERS}' in q
    assert "limit: 100" in q and "sort: DATE" in q
    assert 'date: { field: "dateOnIndeed", start: "2h" }' in q


async def test_indeed_search_term_is_escaped_not_injected():
    seen: list[httpx.Request] = []
    cfg = CFG | {"what": 'a" limit: 1 #'}
    async with client_for(recording(seen)) as client:
        await poll(client, cfg, None, None)
    assert 'what: "a\\" limit: 1 #"' in sent_query(seen[0])


async def test_indeed_keeps_millisecond_timestamp():
    async with client_for(recording([])) as client:
        res = await poll(client, CFG, None, None)
    # dateOnIndeed 1794655505123 ms -> naive UTC, milliseconds intact
    assert res.jobs[0].posted_at == datetime(2026, 11, 14, 11, 25, 5, 123000)
    assert res.jobs[2].posted_at == datetime(2026, 11, 14, 9, 30, 0, 999000)
    assert res.jobs[0].posted_at.tzinfo is None


async def test_indeed_window_widens_after_downtime():  # Review Focus 4
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        await poll(client, CFG, None, NOW - timedelta(hours=9))
        await poll(client, CFG, None, NOW - timedelta(hours=8, minutes=1))  # rounds up
        await poll(client, CFG, None, NOW - timedelta(minutes=5))  # never below cfg
    assert 'start: "9h"' in sent_query(seen[0])
    assert 'start: "9h"' in sent_query(seen[1])
    assert 'start: "2h"' in sent_query(seen[2])


async def test_indeed_window_capped_at_72h():
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        await poll(client, CFG, None, NOW - timedelta(days=30))
        await poll(client, CFG | {"window_hours": 500}, None, None)
    assert 'start: "72h"' in sent_query(seen[0])
    assert 'start: "72h"' in sent_query(seen[1])


async def test_indeed_reports_truncated_at_limit():  # Review Focus 5
    async with client_for(recording([], body_with(100))) as client:
        res = await poll(client, CFG, None, None)
    assert res.truncated and len(res.jobs) == 100

    async with client_for(recording([], body_with(99))) as client:
        assert not (await poll(client, CFG, None, None)).truncated


async def test_indeed_401_is_auth_error():
    def handler(request):
        return httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]})

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "auth"


async def test_indeed_graphql_errors_body_is_transient():
    body = {"errors": [{"message": "Cannot query field"}], "data": None}
    async with client_for(lambda request: httpx.Response(200, json=body)) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_indeed_html_403_raises_blocked():  # Review Focus 2
    def handler(request):
        return httpx.Response(
            403, text="<html>Just a moment...</html>", headers={"content-type": "text/html"}
        )

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "blocked"


async def test_indeed_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with client_for(handler) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_indeed_unexpected_body_is_transient_not_empty_success():
    async with client_for(lambda request: httpx.Response(200, json={"data": {}})) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    assert e.value.kind == "transient"


async def test_indeed_external_apply_url_preferred():
    async with client_for(recording([])) as client:
        res = await poll(client, CFG, None, None)
    external, indeed_apply, indeed_hosted = res.jobs

    # external ATS link, no Indeed Apply
    assert external.apply_url == "https://jobs.lever.co/exotec/9c1d2e3f-0000-4000-8000-abcdef123456"
    assert external.apply_method == "external"
    # Indeed Apply, no recruit URL
    assert indeed_apply.apply_url == "https://fr.indeed.com/viewjob?jk=a84e02d5c7716b90"
    assert indeed_apply.apply_method == "indeed_apply"
    # viewJobUrl on indeed.com is not an external link
    assert indeed_hosted.apply_url == indeed_hosted.url
    assert indeed_hosted.apply_method == "indeed_apply"


async def test_indeed_external_url_wins_over_indeed_apply_scopes():
    data = json.loads(FIXTURE.read_bytes())
    job = data["data"]["jobSearch"]["results"][0]["job"]
    job["indeedApply"] = {"scopes": ["DESKTOP"]}
    async with client_for(recording([], json.dumps(data).encode())) as client:
        res = await poll(client, CFG, None, None)
    assert res.jobs[0].apply_method == "external"
    assert res.jobs[0].apply_url.startswith("https://jobs.lever.co/")


async def test_indeed_skips_items_without_key_or_title():
    results = [{"job": {"title": "No key"}}, {"job": {"key": "x"}}, "junk", {"job": None}]
    results.append({"job": {"key": "kept", "title": "Kept"}})
    body = {"data": {"jobSearch": {"results": results}}}
    async with client_for(lambda request: httpx.Response(200, json=body)) as client:
        res = await poll(client, CFG, None, None)
    assert [j.external_id for j in res.jobs] == ["in-kept"]
    assert res.jobs[0].posted_at is None and res.jobs[0].apply_method == "external"


def test_indeed_is_registered():
    assert ADAPTERS["indeed"] is poll
