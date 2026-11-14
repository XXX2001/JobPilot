"""Owner skill files: $DATA/my_skills.txt is the CV side, $DATA/skills.txt the vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.config import settings
from backend.matching.filters import JobFilters
from backend.matching.gazetteer import check_skill_lines
from backend.matching.matcher import JobMatcher
from backend.models.schemas import JobDetails
from tests.test_rescore import _seed, _stored

FILTERS = JobFilters()


def _job(description: str, title: str = "Engineer") -> JobDetails:
    return JobDetails(title=title, company="ACME", location="Paris", description=description)


POSTING = "Requirements\n- Python\n- Zorglang\n- Docker\n" + "We build things. " * 6


@pytest.fixture
def data_dir():
    data = Path(settings.jobpilot_data_dir)
    yield data
    for name in ("my_skills.txt", "skills.txt"):
        (data / name).unlink(missing_ok=True)


def test_my_skills_file_replaces_cv_extraction():
    matcher = JobMatcher(cv_text="Docker, Kubernetes", my_skills="python | py\n")
    result = matcher.explain(_job(POSTING), FILTERS)
    assert "python" in result.covered
    assert "docker" not in result.covered  # the CV is not read when the file exists


def test_unknown_skill_in_my_skills_is_matched_in_postings():
    matcher = JobMatcher(my_skills="# mine\nzorglang | zorg-lang\npython\n")
    result = matcher.explain(_job(POSTING), FILTERS)
    assert {"python", "zorglang"} <= set(result.covered)
    assert "docker" in result.missing


def test_alias_of_a_bundled_skill_resolves_to_it():
    matcher = JobMatcher(my_skills="ros2\n")
    result = matcher.explain(_job("Requirements\n- ROS 2\n- Docker\n" + "x " * 50), FILTERS)
    assert "ros 2" in result.covered


def test_vocab_override_replaces_bundled_list(tmp_path):
    vocab = tmp_path / "skills.txt"
    vocab.write_text("zorglang\n", encoding="utf-8")
    matcher = JobMatcher(cv_text="Zorglang, Python", vocab_path=vocab)
    assert matcher._cv == {"zorglang"}  # python is not in this vocabulary
    assert matcher.score_basis != JobMatcher(cv_text="Zorglang, Python").score_basis


def test_editing_my_skills_changes_the_basis():
    assert JobMatcher(my_skills="python\n").score_basis != JobMatcher(my_skills="go\n").score_basis


def test_no_files_falls_back_to_cv():
    plain = JobMatcher(cv_text="Python")
    assert (
        plain.score_basis == JobMatcher(cv_text="Python", my_skills="# nothing yet\n").score_basis
    )
    assert "python" in plain.explain(_job(POSTING), FILTERS).covered


def test_check_skill_lines_names_the_bad_line():
    assert check_skill_lines("# c\npython | py\n\n!Go\n") == 2
    with pytest.raises(ValueError, match="line 2"):
        check_skill_lines("python\ndocker | | k8s\n")
    with pytest.raises(ValueError, match="line 1"):
        check_skill_lines("| python\n")


def test_put_skills_writes_the_file_and_rescores(test_app: TestClient, data_dir: Path):
    match_id = _seed(91)
    resp = test_app.put("/api/settings/skills", json={"my_skills": "python\nros2\nc++\n"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["skill_count"] == 3
    assert (data_dir / "my_skills.txt").read_text(encoding="utf-8") == "python\nros2\nc++\n"
    match = _stored(match_id)
    assert match.score_basis == JobMatcher(my_skills="python\nros2\nc++\n").score_basis
    assert {"python", "ros 2", "c++"} <= set(match.keyword_hits)

    got = test_app.get("/api/settings/skills").json()
    assert got == {
        "my_skills": "python\nros2\nc++\n",
        "vocabulary_overridden": False,
        "skill_count": 3,
    }


def test_put_skills_rejects_malformed_line_and_keeps_old_file(test_app: TestClient, data_dir: Path):
    (data_dir / "my_skills.txt").write_text("python\n", encoding="utf-8")
    resp = test_app.put("/api/settings/skills", json={"my_skills": "python\n| docker\n"})
    assert resp.status_code == 422
    assert "line 2" in resp.text
    assert (data_dir / "my_skills.txt").read_text(encoding="utf-8") == "python\n"
