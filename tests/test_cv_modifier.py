"""Tests for CVModifier — all using mocked GeminiClient."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.llm.cv_modifier import CVModifier
from backend.llm.validators import CVModifierOutput, CVReplacement
from backend.llm.job_context import JobContext
from backend.models.schemas import JobDetails

SAMPLE_CV = r"""
\begin{rSection}{Profile}
Junior Food Scientist with laboratory experience in fish cell lines.
\end{rSection}
\begin{rSection}{Experience}
\begin{itemize}
  \item Conducted quality control tests on raw materials.
  \item Performed aseptic sampling.
\end{itemize}
\end{rSection}
"""


def _make_job() -> JobDetails:
    return JobDetails(id=1, title="QC Technician", company="Nestlé",
                      description="Requires HACCP. ISO 22000 preferred.")


def _make_context() -> JobContext:
    return JobContext(
        required_skills=["HACCP"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety"],
        candidate_matches=["HACCP ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["dates", "grades"],
        top_changes_hint=["Profile: add motivation to learn ISO 22000"],
    )


def _mock_client(return_value) -> MagicMock:
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=return_value)
    return client


@pytest.mark.asyncio
async def test_cv_modifier_returns_output():
    expected = CVModifierOutput(replacements=[
        CVReplacement(
            section="Profile",
            original_text="Junior Food Scientist with laboratory experience in fish cell lines.",
            replacement_text="Junior Food Scientist with laboratory experience in fish cell lines, motivated to develop ISO 22000 expertise.",
            reason="Addresses gap in ISO 22000",
            job_requirement_matched="ISO 22000",
            confidence=0.8,
        )
    ])
    modifier = CVModifier(client=_mock_client(expected))
    result = await modifier.modify(_make_job(), SAMPLE_CV, _make_context())
    assert isinstance(result, CVModifierOutput)
    assert len(result.replacements) == 1


@pytest.mark.asyncio
async def test_cv_modifier_caps_at_three():
    """Even if LLM returns 4 replacements, top_three() caps at 3."""
    four_replacements = CVModifierOutput(replacements=[
        CVReplacement(section="Profile", original_text=f"fish cell lines",
                      replacement_text="fish cell lines.",
                      reason="r", job_requirement_matched="x",
                      confidence=0.9 - i * 0.05)
        for i in range(4)
    ])
    modifier = CVModifier(client=_mock_client(four_replacements))
    result = await modifier.modify(_make_job(), SAMPLE_CV, _make_context())
    assert len(result.top_three()) == 3


@pytest.mark.asyncio
async def test_cv_modifier_propagates_error():
    from backend.llm.gemini_client import GeminiJSONError
    client = MagicMock()
    client.generate_json = AsyncMock(side_effect=GeminiJSONError("bad"))
    modifier = CVModifier(client=client)
    with pytest.raises(GeminiJSONError):
        await modifier.modify(_make_job(), SAMPLE_CV, _make_context())


from backend.matching.fit_engine import FitAssessment, SkillGap


@pytest.mark.asyncio
async def test_cv_modifier_from_assessment():
    """modify_from_assessment() should accept FitAssessment and return CVModifierOutput."""
    expected = CVModifierOutput(replacements=[
        CVReplacement(
            section="Skills",
            original_text="Python, Java, SQL, JavaScript",
            replacement_text="Python, Docker, SQL, JavaScript",
            reason="Adds Docker to address critical gap",
            job_requirement_matched="Docker",
            confidence=0.85,
        )
    ])
    modifier = CVModifier(client=_mock_client(expected))
    assessment = FitAssessment(
        severity=0.55,
        should_modify=True,
        simulated_ats_score=45.0,
        covered_skills=["Python", "SQL"],
        partial_matches=[],
        critical_gaps=[
            SkillGap(skill="Docker", criticality=0.9, best_cv_match="CI/CD", similarity=0.58),
        ],
        preferred_gaps=[],
    )
    result = await modifier.modify_from_assessment(
        _make_job(), SAMPLE_CV, assessment
    )
    assert isinstance(result, CVModifierOutput)
    assert len(result.replacements) <= 3
