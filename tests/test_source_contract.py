"""Rules every adapter must hold: logged out, one bad item never fails a poll. No network."""

from __future__ import annotations

import httpx
import pytest

from backend.scraping.sources import ADAPTERS

CFGS = {
    "greenhouse": {"adapter": "greenhouse", "slug": "acme"},
    "smartrecruiters": {"adapter": "smartrecruiters", "slug": "acme"},
    "ashby": {"adapter": "ashby", "slug": "acme"},
    "indeed": {"adapter": "indeed", "what": "robot", "where": "Paris"},
    "linkedin_guest": {"adapter": "linkedin_guest", "keywords": "robot", "location": "France"},
}
# The key each adapter reads the posting date and the description from.
DATE_KEY = {
    "greenhouse": "first_published",
    "smartrecruiters": "releasedDate",
    "ashby": "publishedAt",
    "linkedin_guest": "date",
}
OUT_OF_RANGE = ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"]
BAD_URL = "https://[::1/x"  # urlsplit/urlparse raise ValueError: Invalid IPv6 URL
CARD = (
    '<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{id}">'
    '<a class="base-card__full-link" href="{href}">x</a><h3>Title {id}</h3><h4>Acme</h4>'
    '<time datetime="{date}"></time></div></li>'
)


def respond(adapter: str, items: list[dict], headers: dict | None = None) -> httpx.Response:
    """A 200 answer of ``adapter`` holding one job per dict of field overrides."""
    if adapter == "linkedin_guest":
        cards = [
            CARD.format(
                **{"id": 100 + i, "href": "https://fr.linkedin.com/jobs/view/x", "date": ""} | over
            )
            for i, over in enumerate(items)
        ]
        return httpx.Response(200, html="<ul>" + "".join(cards) + "</ul>", headers=headers)
    if adapter == "indeed":
        results = [{"job": {"key": f"k{i}", "title": "t"} | o} for i, o in enumerate(items)]
        body: dict = {"data": {"jobSearch": {"results": results}}}
    elif adapter == "smartrecruiters":
        body = {"content": [{"id": f"k{i}", "name": "t"} | o for i, o in enumerate(items)]}
    else:  # greenhouse, ashby
        body = {"jobs": [{"id": f"k{i}", "title": "t"} | o for i, o in enumerate(items)]}
    return httpx.Response(200, json=body, headers=headers)


async def poll(adapter: str, items: list[dict]):
    transport = httpx.MockTransport(lambda request: respond(adapter, items))
    async with httpx.AsyncClient(transport=transport) as client:
        return await ADAPTERS[adapter](client, CFGS[adapter], None, None)


def test_every_adapter_is_covered():
    assert set(CFGS) == set(ADAPTERS)


@pytest.mark.parametrize("adapter", sorted(CFGS))
async def test_request_carries_no_cookie_or_auth_header(adapter):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return respond(adapter, [{}], headers={"set-cookie": "sid=track; Path=/"})

    # an account cookie and credentials on the shared client, and a cookie set by
    # the first answer: none of them may reach the source
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), cookies={"li_at": "ACCOUNT"}, auth=("user", "pw")
    ) as client:
        for _ in range(2):
            res = await ADAPTERS[adapter](client, CFGS[adapter], None, None)
            assert len(res.jobs) == 1
    assert len(seen) == 2
    for request in seen:
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers


@pytest.mark.parametrize(
    "adapter, bad",
    [
        ("greenhouse", {"location": "Paris"}),
        ("greenhouse", {"location": ["Paris"]}),
        ("greenhouse", {"absolute_url": BAD_URL}),
        ("smartrecruiters", {"company": "Acme"}),
        ("smartrecruiters", {"location": "Paris"}),
        ("ashby", {"jobUrl": BAD_URL}),
        ("ashby", {"applyUrl": BAD_URL}),
        ("indeed", {"recruit": "x"}),
        ("indeed", {"description": "x"}),
        ("indeed", {"location": "Paris"}),
        ("indeed", {"employer": "Acme"}),
        ("indeed", {"indeedApply": "x"}),
    ],
)
async def test_malformed_item_is_skipped_and_the_rest_kept(adapter, bad):
    res = await poll(adapter, [bad, {}, bad])
    assert len(res.jobs) == 1
    assert res.jobs[0].external_id.endswith("k1")


async def test_linkedin_bad_href_falls_back_to_the_canonical_url():
    res = await poll("linkedin_guest", [{"href": BAD_URL}, {}])
    assert [j.external_id for j in res.jobs] == ["li-100", "li-101"]
    assert res.jobs[0].url == "https://www.linkedin.com/jobs/view/100"
    assert res.jobs[1].url == "https://fr.linkedin.com/jobs/view/x"


@pytest.mark.parametrize("adapter", sorted(DATE_KEY))
@pytest.mark.parametrize("date", OUT_OF_RANGE)
async def test_out_of_range_date_keeps_the_job_without_a_date(adapter, date):
    res = await poll(adapter, [{DATE_KEY[adapter]: date}, {}])
    assert len(res.jobs) == 2
    assert res.jobs[0].posted_at is None


@pytest.mark.parametrize("ms", [10**30, -(10**30)])
async def test_indeed_out_of_range_timestamp_keeps_the_job_without_a_date(ms):
    res = await poll("indeed", [{"dateOnIndeed": ms}, {"dateOnIndeed": 1794655505123}])
    assert [j.external_id for j in res.jobs] == ["in-k0", "in-k1"]
    assert res.jobs[0].posted_at is None
    assert res.jobs[1].posted_at is not None


@pytest.mark.parametrize(
    "adapter, field",
    [
        ("greenhouse", lambda text: {"content": f"<p>{text}</p>"}),
        ("ashby", lambda text: {"descriptionPlain": text}),
        ("indeed", lambda text: {"description": {"html": f"<p>{text}</p>"}}),
    ],
    ids=["greenhouse", "ashby", "indeed"],
)
async def test_long_description_is_capped_at_the_rawjob_limit(adapter, field):
    res = await poll(adapter, [field("word " * 10000)])  # 50,000 chars
    assert len(res.jobs) == 1
    assert 19000 < len(res.jobs[0].description) <= 20000
