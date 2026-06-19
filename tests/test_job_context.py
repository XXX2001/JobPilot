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


def test_cv_modifier_output_caps_at_three_replacements():
    """CVModifierOutput.top_three() returns at most 3 items sorted by confidence."""
    from backend.llm.validators import CVModifierOutput, CVReplacement
    output = CVModifierOutput(replacements=[
        CVReplacement(section="Profile", original_text=f"text{i}",
                      replacement_text=f"new{i}", reason=f"r{i}",
                      job_requirement_matched="x", confidence=0.9 - i * 0.05)
        for i in range(4)
    ])
    top = output.top_three()
    assert len(top) == 3
    # Should be sorted by confidence descending
    assert top[0].confidence > top[1].confidence > top[2].confidence


def test_cv_replacement_confidence_threshold():
    """is_applicable() returns True only at confidence >= 0.7."""
    from backend.llm.validators import CVReplacement
    low = CVReplacement(section="Profile", original_text="x", replacement_text="y",
                        reason="test", job_requirement_matched="req", confidence=0.65)
    assert not low.is_applicable()

    exact = CVReplacement(section="Profile", original_text="x", replacement_text="y",
                          reason="test", job_requirement_matched="req", confidence=0.7)
    assert exact.is_applicable()

    high = CVReplacement(section="Profile", original_text="x", replacement_text="y",
                         reason="test", job_requirement_matched="req", confidence=0.95)
    assert high.is_applicable()


def test_cv_modifier_output_empty_replacements():
    """top_three() on an empty CVModifierOutput returns empty list."""
    from backend.llm.validators import CVModifierOutput
    output = CVModifierOutput()
    assert output.top_three() == []
