from backend.defaults import (
    GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    GAP_SEVERITY_THRESHOLD_BALANCED,
    GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
    EMBEDDING_MODEL,
    SIMILARITY_FULL_MATCH,
    SIMILARITY_PARTIAL_MATCH,
    MIN_JOB_SKILLS_FOR_FIT_ENGINE,
)


def test_gap_severity_thresholds_ordered():
    assert GAP_SEVERITY_THRESHOLD_CONSERVATIVE < GAP_SEVERITY_THRESHOLD_BALANCED
    assert GAP_SEVERITY_THRESHOLD_BALANCED < GAP_SEVERITY_THRESHOLD_AGGRESSIVE


def test_similarity_thresholds_ordered():
    assert SIMILARITY_PARTIAL_MATCH < SIMILARITY_FULL_MATCH


def test_embedding_model_set():
    assert EMBEDDING_MODEL == "text-embedding-004"


def test_min_job_skills_positive():
    assert MIN_JOB_SKILLS_FOR_FIT_ENGINE >= 1
