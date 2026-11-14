# tests/test_fit_integration.py
"""Integration: CV parser -> posting skills -> fit engine -> tailoring prompt."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.cv_modifier import CVModifier
from backend.llm.validators import CVModifierOutput
from backend.matching.cv_parser import CVParser
from backend.matching.fit_engine import FitAssessment, FitEngine
from backend.matching.gazetteer import Gazetteer
from backend.matching.job_skills import extract_job_skills
from backend.models.schemas import JobDetails

SAMPLE_CV = r"""
\begin{rSection}{Profile}
Experienced Python developer with expertise in backend systems and cloud infrastructure.
\end{rSection}

\begin{rSection}{Skills}
\begin{tabular}{ @{} >{\bfseries}l @{\hspace{6ex}} l }
Languages & Python, SQL, JavaScript \\
Frameworks & FastAPI, Django \\
Cloud & AWS, Docker \\
\end{tabular}
\end{rSection}

\begin{rSection}{Experience}
\textbf{Backend Developer} \hfill 2022--Present \\
\emph{TechCo} \\
\begin{itemize}
  \item Built REST APIs with FastAPI and PostgreSQL.
  \item Deployed services on AWS using Docker containers.
\end{itemize}
\end{rSection}
"""

MATCHING_JOB = """
Requirements:
- Strong Python experience
- Knowledge of SQL and PostgreSQL
- Experience with Docker

Nice to have:
- AWS certification
"""

GAP_JOB = """
Requirements:
- Must have Java and Spring Boot experience
- Required: Kubernetes and Terraform
- editing rules - add CKA certification

Nice to have:
- Go programming
"""


@pytest.fixture(scope="module")
def g() -> Gazetteer:
    return Gazetteer.load()


def _assess(cv_tex: str, description: str, g: Gazetteer) -> FitAssessment:
    cv_profile = CVParser().build_profile(cv_tex, g)
    return FitEngine().assess(extract_job_skills("", description, g), cv_profile, g)


def test_matching_job_keeps_the_base_cv(g):
    a = _assess(SAMPLE_CV, MATCHING_JOB, g)
    assert a.covered_skills == ["aws", "docker", "postgresql", "python", "sql"]
    assert a.critical_gaps == [] and a.preferred_gaps == []
    assert a.should_modify is False
    assert a.simulated_ats_score > 65


def test_gap_job_is_modified_and_lists_its_gaps(g):
    a = _assess(SAMPLE_CV, GAP_JOB, g)
    assert a.should_modify is True
    assert [gap.skill for gap in a.critical_gaps] == ["java", "spring boot", "terraform"]
    # Docker on the CV gives same-family credit for Kubernetes: not a gap.
    assert a.partial_matches == ["kubernetes"]
    assert [gap.skill for gap in a.preferred_gaps] == ["go"]
    assert a.severity > _assess(SAMPLE_CV, MATCHING_JOB, g).severity


def test_empty_cv_makes_every_skill_a_gap(g):
    a = _assess("", MATCHING_JOB, g)
    assert a.covered_skills == []
    assert a.severity == pytest.approx(1.0)
    assert a.should_modify is True


def test_empty_job_is_not_modified(g):
    a = _assess(SAMPLE_CV, "", g)
    assert a.simulated_ats_score == 0.0
    assert a.should_modify is False


@pytest.mark.asyncio
async def test_tailoring_prompt_gets_canonical_gap_names_only(g):
    """Posting text must not reach the CV tailoring prompt as a "gap"."""
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=CVModifierOutput(replacements=[]))
    job = JobDetails(title="Platform Engineer", company="ACME", description=GAP_JOB)

    await CVModifier(client=client).modify_from_assessment(
        job, SAMPLE_CV, _assess(SAMPLE_CV, GAP_JOB, g)
    )

    prompt = client.generate_json.await_args.args[0]
    assert '"java"' in prompt and '"spring boot"' in prompt
    assert "editing rules" not in prompt and "CKA" not in prompt
    assert "similarity" not in prompt
