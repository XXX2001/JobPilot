"""Tests for JobContext model and its to_markdown() serialization."""
from backend.llm.job_context import JobContext


def test_job_context_to_markdown_contains_required_fields():
    ctx = JobContext(
        required_skills=["HACCP", "aseptic sampling"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety", "traceability"],
        candidate_matches=["HACCP ✓", "aseptic sampling ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["dates", "grades", "company names", "certifications"],
        top_changes_hint=[
            "Profile: add motivation to learn ISO 22000",
            "Skills: reorder to put HACCP first",
        ],
    )
    md = ctx.to_markdown(job_title="QC Technician", company="Nestlé")

    assert "QC Technician" in md
    assert "Nestlé" in md
    assert "HACCP" in md
    assert "ISO 22000" in md
    assert "DO NOT TOUCH" in md
    assert "Profile:" in md


def test_job_context_to_markdown_empty_gaps():
    ctx = JobContext(
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["data"],
        candidate_matches=["Python ✓"],
        candidate_gaps=[],
        do_not_touch=["dates"],
        top_changes_hint=["Profile: emphasise Python"],
    )
    md = ctx.to_markdown(job_title="Analyst", company="Acme")
    # When no gaps, should say "none" rather than an empty list
    assert "none" in md.lower()
