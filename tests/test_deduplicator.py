from __future__ import annotations

import hashlib

from backend.models.schemas import RawJob
from backend.scraping.deduplicator import JobDeduplicator, dedup_key


def _make_job(
    title: str = "Engineer",
    company: str = "ACME",
    location: str = "Paris",
    description: str = "short",
) -> RawJob:
    return RawJob(
        title=title,
        company=company,
        location=location,
        description=description,
        url="http://example.com",
    )


def test_deduplicates_exact_same_job():
    """Two jobs with same company/title/location are collapsed to one."""
    dedup = JobDeduplicator()
    job1 = _make_job()
    job2 = _make_job()
    result = dedup.deduplicate([job1, job2])
    assert len(result) == 1


def test_keeps_longer_description():
    """When deduplicating, the job with the longer description is kept."""
    dedup = JobDeduplicator()
    short_job = _make_job(description="short description")
    long_job = _make_job(description="much longer description with more detail about the role")
    result = dedup.deduplicate([short_job, long_job])
    assert len(result) == 1
    assert result[0].description == long_job.description


def test_dedup_key_ignores_case_and_whitespace():
    assert dedup_key("ACME ", "Robotics  Engineer", " Paris") == dedup_key(
        "acme", "robotics engineer", "paris"
    )


def test_dedup_key_matches_legacy_hash_for_clean_input():
    legacy = hashlib.md5("acme|robotics engineer|paris".encode()).hexdigest()
    assert dedup_key("acme", "robotics engineer", "paris") == legacy


def test_dedup_key_tolerates_missing_location():
    assert dedup_key("acme", "engineer", None) == dedup_key("acme", "engineer", "")


def test_deduplicator_uses_dedup_key():
    job = _make_job(title="Robotics\t Engineer", company=" Acme", location="PARIS ")
    assert JobDeduplicator()._make_key(job) == dedup_key("acme", "robotics engineer", "paris")
