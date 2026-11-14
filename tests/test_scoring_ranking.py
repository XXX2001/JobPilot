"""Ranking on the 12 synthetic adversarial postings, scored against the example CV.

The labels in ``fixtures/scoring/adversarial.json`` are fixed: a failure here is a
scoring rule to fix where it lives (gazetteer, job_skills, scorer), never a label.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.schemas import JobDetails

ROOT = Path(__file__).resolve().parents[1]
POSTINGS = json.loads(
    (ROOT / "tests" / "fixtures" / "scoring" / "adversarial.json").read_text(encoding="utf-8")
)
CV = (ROOT / "scripts" / "defaults" / "templates" / "example_cv.tex").read_text(encoding="utf-8")


@pytest.mark.parametrize("keywords", [[], ["python", "backend"]], ids=["no-keywords", "keywords"])
def test_every_good_posting_outranks_every_bad_one(keywords):
    filters = JobFilters(keywords=keywords, locations=["Paris"])
    matcher = JobMatcher(CV, keywords)
    scores = {
        p["id"]: matcher.score(
            JobDetails(
                title=p["title"],
                company=p["company"],
                location=p["location"],
                description=p["description"],
            ),
            filters,
        )
        for p in POSTINGS
    }
    good = {p["id"]: scores[p["id"]] for p in POSTINGS if p["label"] == "good"}
    bad = {p["id"]: scores[p["id"]] for p in POSTINGS if p["label"] == "bad"}
    assert len(POSTINGS) == 12 and good and bad
    lowest_good = min(good, key=good.__getitem__)
    highest_bad = max(bad, key=bad.__getitem__)
    assert good[lowest_good] > bad[highest_bad], (
        f"{highest_bad} ({bad[highest_bad]}) outranks {lowest_good} ({good[lowest_good]}): {scores}"
    )
