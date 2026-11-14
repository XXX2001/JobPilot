"""_parse_posted_date: naive UTC out, explicit unit words only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.scraping import json_utils
from backend.scraping.json_utils import _parse_posted_date

NOW = datetime(2026, 11, 14, 12, 0, 0)


@pytest.fixture
def frozen_now(monkeypatch) -> datetime:
    monkeypatch.setattr(json_utils, "naive_utc_now", lambda: NOW)
    return NOW


@pytest.mark.parametrize(
    "text,expected_delta",
    [
        ("3 minutes ago", timedelta(minutes=3)),
        ("il y a 12 minutes", timedelta(minutes=12)),
        ("5 min", timedelta(minutes=5)),
        ("2 hours ago", timedelta(hours=2)),
        ("il y a 1 heure", timedelta(hours=1)),
        ("3h", timedelta(hours=3)),
        ("il y a 3 jours", timedelta(days=3)),
        ("2 days ago", timedelta(days=2)),
        ("30+ days ago", timedelta(days=30)),
        ("1 week ago", timedelta(weeks=1)),
        ("il y a 2 semaines", timedelta(weeks=2)),
        ("il y a 2 mois", timedelta(days=60)),
        ("yesterday", timedelta(days=1)),
        ("Just posted", timedelta(0)),
    ],
)
def test_relative_dates(text, expected_delta, frozen_now):
    assert _parse_posted_date(text) == frozen_now - expected_delta


def test_relative_dates_are_naive(frozen_now):
    assert _parse_posted_date("2 hours ago").tzinfo is None


def test_iso_with_z_and_offset_become_naive_utc():
    assert _parse_posted_date("2026-11-13T10:00:00+02:00") == datetime(2026, 11, 13, 8, 0)
    assert _parse_posted_date("2026-11-13T08:00:00Z") == datetime(2026, 11, 13, 8, 0)
    assert _parse_posted_date("2026-11-13T08:00:00.123Z") == datetime(
        2026, 11, 13, 8, 0, 0, 123000
    )


def test_plain_dates_still_parse_and_are_naive():
    assert _parse_posted_date("2026-11-13") == datetime(2026, 11, 13)
    assert _parse_posted_date("2026-11-13T08:00:00") == datetime(2026, 11, 13, 8, 0)
    assert _parse_posted_date("13/11/2026") == datetime(2026, 11, 13)
    assert _parse_posted_date("November 13, 2026") == datetime(2026, 11, 13)


def test_12_dec_is_not_twelve_days_ago(frozen_now):
    assert _parse_posted_date("12 Dec") != frozen_now - timedelta(days=12)
    assert _parse_posted_date("12 Dec") is None


def test_bare_letters_are_not_units(frozen_now):
    assert _parse_posted_date("3 developers wanted") is None
    assert _parse_posted_date("4 weeks of holiday") == frozen_now - timedelta(weeks=4)
    assert _parse_posted_date("2 widgets") is None


@pytest.mark.parametrize("value", ["soon", "", None, "null", "none"])
def test_garbage_is_none(value):
    assert _parse_posted_date(value) is None


async def test_orchestrator_age_filter_accepts_naive_posted_at():
    """The post-scrape cutoff compares against the naive datetimes the parser returns."""
    from unittest.mock import MagicMock

    from backend.matching.filters import JobFilters
    from backend.models.schemas import RawJob
    from backend.scraping.orchestrator import ScrapingOrchestrator
    from backend.utils.time import naive_utc_now

    def job(n: int, posted) -> RawJob:
        return RawJob(title=f"Job {n}", company="ACME", url=f"https://x.test/{n}", posted_at=posted)

    jobs = [
        job(1, _parse_posted_date("2 days ago")),
        job(2, _parse_posted_date("3 weeks ago")),
        job(3, None),
        job(4, naive_utc_now().replace(tzinfo=timezone.utc)),
    ]

    class Adzuna:
        async def search(self, **kwargs):
            return jobs

    class Dedup:
        def deduplicate(self, items):
            return items

    src = MagicMock(type="api", url=None, config={"country": "fr"})
    src.name = "adzuna"
    kept = await ScrapingOrchestrator(adzuna_client=Adzuna(), deduplicator=Dedup()).scrape_batch(
        keywords=["python"],
        filters=JobFilters(keywords=["python"], locations=[], remote_only=False),
        sources=[src],
        max_age_days=7,
    )
    assert sorted(j.title for j in kept) == ["Job 1", "Job 3", "Job 4"]
