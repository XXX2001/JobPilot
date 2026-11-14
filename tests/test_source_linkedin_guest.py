"""LinkedIn guest adapter: card parsing, time window, blocked responses, no cookies. No network."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from backend.scraping import json_utils
from backend.scraping.sources import ADAPTERS, linkedin_guest
from backend.scraping.sources.base import PollResult, SourceError
from backend.scraping.sources.linkedin_guest import poll

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "linkedin_guest_fragment.html"
CFG = {"adapter": "linkedin_guest", "keywords": "ingénieur", "location": "France"}
NOW = datetime(2026, 11, 14, 12, 0, 0)
HTML = {"content-type": "text/html; charset=utf-8"}


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch) -> datetime:
    monkeypatch.setattr(linkedin_guest, "naive_utc_now", lambda: NOW)
    monkeypatch.setattr(json_utils, "naive_utc_now", lambda: NOW)  # relative dates
    return NOW


def client_for(handler, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def recording(seen: list[httpx.Request], body: bytes | None = None):
    def handler(request):
        seen.append(request)
        content = FIXTURE.read_bytes() if body is None else body
        return httpx.Response(200, content=content, headers=HTML)

    return handler


async def raises_kind(handler, **kwargs) -> str:
    async with client_for(handler, **kwargs) as client:
        with pytest.raises(SourceError) as e:
            await poll(client, CFG, None, None)
    return e.value.kind


async def test_linkedin_parses_ten_cards():
    async with client_for(recording([])) as client:
        res = await poll(client, CFG, None, None)

    assert isinstance(res, PollResult)
    assert not res.not_modified and not res.truncated and res.etag is None
    assert [j.external_id for j in res.jobs] == [
        "li-4481950329",
        "li-4463024960",
        "li-4472937879",
        "li-4483136149",
        "li-4491072520",
        "li-4483109473",
        "li-4463454171",
        "li-4454105376",
        "li-4483137234",
        "li-4437020937",
    ]
    job = res.jobs[0]
    assert job.title == "Staff Software Engineer, AI Agents (Python)"
    assert job.company == "Qonto"
    assert job.location == "Paris, Île-de-France, France"
    assert job.description == ""
    assert job.source_name == "linkedin_guest"
    # UTF-8 survives, whitespace is collapsed
    assert res.jobs[1].title == "Ingénieur IA CONFIRME - Paris, France (H/F)"
    assert res.jobs[2].company == "NAVAL GROUP"
    assert res.jobs[2].location == "Ollioules, Provence-Alpes-Côte d'Azur, France"


async def test_linkedin_url_has_no_tracking_parameters():
    async with client_for(recording([])) as client:
        res = await poll(client, CFG, None, None)
    job = res.jobs[0]
    assert (
        job.url
        == job.apply_url
        == (
            "https://fr.linkedin.com/jobs/view/"
            "staff-software-engineer-ai-agents-python-at-qonto-4481950329"
        )
    )
    assert all("?" not in j.url and "trackingId" not in j.url for j in res.jobs)
    assert all(j.url.endswith(j.external_id.removeprefix("li-")) for j in res.jobs)


async def test_linkedin_posted_at_refined_by_relative_text():
    async with client_for(recording([])) as client:
        res = await poll(client, CFG, None, None)
    # <time datetime="2026-11-14"> is a bare day; "15 minutes ago" gives the time
    assert res.jobs[0].posted_at == NOW - timedelta(minutes=15)
    assert res.jobs[1].posted_at == NOW - timedelta(minutes=57)
    assert res.jobs[7].posted_at == NOW - timedelta(minutes=12)
    assert all(j.posted_at.tzinfo is None for j in res.jobs)


async def test_linkedin_posted_at_falls_back_to_datetime_attribute():
    body = FIXTURE.read_text(encoding="utf-8")
    # unparseable relative text -> the attribute's day; contradicting text -> the attribute
    body = body.replace("15 minutes ago", "récemment").replace("57 minutes ago", "3 months ago")
    async with client_for(recording([], body.encode())) as client:
        res = await poll(client, CFG, None, None)
    assert res.jobs[0].posted_at == datetime(2026, 11, 14)
    assert res.jobs[1].posted_at == datetime(2026, 11, 14)


async def test_linkedin_request_shape():
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        await poll(client, CFG, "ignored-etag", NOW - timedelta(minutes=5))

    req = seen[0]
    assert req.method == "GET"
    assert str(req.url).startswith(
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
    )
    assert dict(req.url.params) == {
        "keywords": "ingénieur",
        "location": "France",
        "f_TPR": "r1800",
        "sortBy": "DD",
        "start": "0",
    }
    assert "Mozilla/5.0" in req.headers["user-agent"] and "Mobile" not in req.headers["user-agent"]
    assert "if-none-match" not in req.headers


async def test_linkedin_window_is_twice_the_gap_within_bounds():
    seen: list[httpx.Request] = []
    async with client_for(recording(seen)) as client:
        await poll(client, CFG, None, NOW - timedelta(minutes=5))  # floor 1800
        await poll(client, CFG, None, NOW - timedelta(hours=1))  # 2 x 3600
        await poll(client, CFG, None, NOW - timedelta(days=3))  # cap 86400
        await poll(client, CFG, None, None)  # never polled: widest window
    assert [r.url.params["f_TPR"] for r in seen] == ["r1800", "r7200", "r86400", "r86400"]


async def test_empty_200_is_zero_jobs_not_error():
    for body in (b"", b"  \n "):
        async with client_for(recording([], body)) as client:
            res = await poll(client, CFG, None, None)
        assert res.jobs == [] and not res.not_modified and not res.truncated


async def test_429_999_and_authwall_raise_blocked():  # Review Focus 2
    for status in (429, 999, 403):
        assert await raises_kind(lambda request, s=status: httpx.Response(s, text="")) == "blocked"

    def authwall(request):
        if "authwall" in str(request.url):
            return httpx.Response(200, content=FIXTURE.read_bytes(), headers=HTML)
        target = "https://www.linkedin.com/authwall?trk=bf&sessionRedirect=x"
        return httpx.Response(302, headers={"location": target})

    assert await raises_kind(authwall) == "blocked"
    # even a client that follows redirects must not parse what the wall serves
    assert await raises_kind(authwall, follow_redirects=True) == "blocked"


async def test_200_without_cards_raises_blocked():  # Review Focus 2
    def page(html: str):
        return lambda request: httpx.Response(200, text=html, headers=HTML)

    wall = "<html><body><h1>Sign in to LinkedIn</h1><form></form></body></html>"
    assert await raises_kind(page(wall)) == "blocked"
    # a challenge page with <li> navigation but no job card is still not a result
    nav = "<html><body><ul><li><a href='/login'>Sign in</a></li></ul></body></html>"
    assert await raises_kind(page(nav)) == "blocked"


async def test_server_and_network_errors_are_transient():
    assert await raises_kind(lambda request: httpx.Response(503, text="")) == "transient"
    assert await raises_kind(lambda request: httpx.Response(400, text="")) == "transient"

    def timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    assert await raises_kind(timeout) == "transient"


async def test_request_carries_no_cookie_header():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        headers = HTML | {"set-cookie": "JSESSIONID=ajax:123; Domain=.linkedin.com; Path=/"}
        return httpx.Response(200, content=FIXTURE.read_bytes(), headers=headers)

    # an account cookie on the shared client, and a cookie set by the first answer
    async with client_for(handler, cookies={"li_at": "account-session"}) as client:
        await poll(client, CFG, None, None)
        await poll(client, CFG, None, NOW - timedelta(minutes=5))
    assert len(seen) == 2
    assert all("cookie" not in r.headers for r in seen)


async def test_linkedin_skips_cards_without_id_or_title():
    body = FIXTURE.read_text(encoding="utf-8")
    body = body.replace("urn:li:jobPosting:4481950329", "urn:li:other:1")
    body = body.replace("Data Engineer", " ")
    async with client_for(recording([], body.encode())) as client:
        res = await poll(client, CFG, None, None)
    ids = [j.external_id for j in res.jobs]
    assert len(ids) == 8 and "li-4481950329" not in ids and "li-4463454171" not in ids


def test_linkedin_guest_is_registered():
    assert ADAPTERS["linkedin_guest"] is poll
