"""Watchlist import: CSV rows -> monitor sources, through a mocked adapter. No network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.job import JobSource
from backend.models.schemas import RawJob
from backend.models.user import SearchSettings
from backend.scraping.sources import ADAPTERS, PollResult, SourceError

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "tests" / "fixtures" / "sources" / "watchlist_greenhouse.csv"

_spec = importlib.util.spec_from_file_location(
    "import_watchlist", ROOT / "scripts" / "import_watchlist.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def raw(title: str, location: str) -> RawJob:
    return RawJob(external_id=title, title=title, company="Acme", location=location)


# What each board of the fixture CSV answers. teads1 is blocked; the two rows on
# a company domain (Helsing, Datadog) carry no slug and are never requested.
BOARDS: dict[str, list[RawJob]] = {
    "mozilla": [raw("Accountant", "Paris, France"), raw("Robotics Engineer", "Berlin, Germany")],
    "quberesearchandtechnologies": [raw("Quant Developer", "München, Bavaria, Germany")],
    "ivalua": [raw("Embedded Software Engineer", "Lyon")],
}


@pytest.fixture
def polled(monkeypatch) -> list[str]:
    """Replace the greenhouse adapter; return the slugs it was asked for."""
    calls: list[str] = []

    async def fake_poll(client, cfg, etag, last_scraped_at) -> PollResult:
        calls.append(cfg["slug"])
        if cfg["slug"] not in BOARDS:
            raise SourceError("blocked", "HTTP 403")
        return PollResult(jobs=BOARDS[cfg["slug"]])

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_poll)
    return calls


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request to {request.url}")


async def no_sleep(seconds: float) -> None:
    return None


async def run_import(settings: SearchSettings | None = None, **kw) -> tuple[int, int]:
    kw.setdefault("sleep", no_sleep)
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(SearchSettings))).first() is None:
            db.add(
                settings
                or SearchSettings(
                    id=1, keywords=[], locations={"items": ["Paris"]}, countries={"items": ["fr"]}
                )
            )
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
            return await script.import_watchlist(db, client, "greenhouse", CSV, **kw)


async def monitor_sources() -> list[JobSource]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(JobSource).where(JobSource.type == "monitor").order_by(JobSource.name)
        )
        return list(rows.scalars())


def test_slug_comes_from_the_board_url():
    assert (
        script.slug_from_url("https://job-boards.greenhouse.io/mozilla/jobs/8360414") == "mozilla"
    )
    assert script.slug_from_url("https://job-boards.eu.greenhouse.io/teads1/jobs/1") == "teads1"
    assert (
        script.slug_from_url("https://boards.greenhouse.io/ivalua/jobs/7568867?gh_jid=7568867")
        == "ivalua"
    )
    assert script.slug_from_url("https://jobs.ashbyhq.com/alan/d457e0f1") == "alan"
    assert script.slug_from_url("https://jobs.smartrecruiters.com/SopraSteria1/7440") == (
        "SopraSteria1"
    )
    # a board embedded in the company's own site: the slug is not in the URL
    assert script.slug_from_url("https://helsing.ai/jobs/4953387101?gh_jid=4953387101") is None
    assert script.slug_from_url("https://evil.example/greenhouse.io/x") is None
    assert script.slug_from_url("") is None


async def test_keeps_a_board_with_a_french_job_and_skips_one_without(polled):
    kept, skipped = await run_import()

    assert (kept, skipped) == (2, 4)
    sources = await monitor_sources()
    # mozilla: "Paris" is one of the owner's locations; ivalua: Lyon is in country "fr"
    assert [s.name for s in sources] == ["greenhouse:ivalua", "greenhouse:mozilla"]
    for s in sources:
        slug = s.name.split(":")[1]
        assert s.config == {"adapter": "greenhouse", "slug": slug}
        assert s.enabled is True
        assert s.last_scraped_at is None  # the monitor's first poll still seeds silently
    assert polled == ["mozilla", "quberesearchandtechnologies", "teads1", "ivalua"]


async def test_import_is_idempotent(polled):
    await run_import()
    polled.clear()

    kept, skipped = await run_import()

    assert (kept, skipped) == (0, 6)
    assert [s.name for s in await monitor_sources()] == ["greenhouse:ivalua", "greenhouse:mozilla"]
    assert "mozilla" not in polled and "ivalua" not in polled  # known boards cost no request


async def test_title_regex_must_match_a_job_in_the_owners_area(polled):
    # mozilla's robotics job is in Berlin and its Paris job is an accountant
    kept, skipped = await run_import(title_regex="robot|embedded")

    assert (kept, skipped) == (1, 5)
    assert [s.name for s in await monitor_sources()] == ["greenhouse:ivalua"]


async def test_limit_bounds_the_rows_read(polled):
    kept, skipped = await run_import(limit=2)

    assert (kept, skipped) == (1, 1)
    assert polled == ["mozilla", "quberesearchandtechnologies"]


async def test_one_request_per_second(polled):
    naps: list[float] = []

    async def record(seconds: float) -> None:
        naps.append(seconds)

    await run_import(sleep=record)

    assert len(polled) == 4
    assert naps == [1, 1, 1]  # between requests only, not after rows that cost none


async def test_location_terms_match_whole_words(polled, monkeypatch):
    # "uk" (country gb) must not match inside "Dunkerque"
    monkeypatch.setitem(BOARDS, "mozilla", [raw("Engineer", "Dunkerque")])
    settings = SearchSettings(id=1, keywords=[], countries={"items": ["gb"]})

    kept, _ = await run_import(settings)

    assert kept == 0


async def test_refuses_to_run_without_a_location_preference(polled):
    with pytest.raises(SystemExit):
        await run_import(SearchSettings(id=1, keywords=[]))

    assert polled == []
    assert await monitor_sources() == []
