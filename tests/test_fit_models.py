"""Test that new DB columns exist on SearchSettings and JobMatch."""
from backend.models.user import SearchSettings
from backend.models.job import JobMatch


def test_search_settings_has_sensitivity():
    ss = SearchSettings(id=1, keywords={"include": []})
    assert hasattr(ss, "cv_modification_sensitivity")
    assert ss.cv_modification_sensitivity == "balanced"


def test_job_match_has_fit_columns():
    jm = JobMatch(id=1, job_id=1, score=50.0)
    assert hasattr(jm, "gap_severity")
    assert hasattr(jm, "ats_score")
    assert hasattr(jm, "fit_assessment_json")
    assert jm.gap_severity is None
    assert jm.ats_score is None
    assert jm.fit_assessment_json is None
