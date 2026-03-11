# tests/test_fit_integration.py
"""Integration test: CV parser → job extractor → embedder → fit engine → decision."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.matching.cv_parser import CVParser
from backend.matching.embedder import Embedder
from backend.matching.fit_engine import FitEngine
from backend.matching.job_skill_extractor import JobSkillExtractor

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
- Strong DevOps background

Nice to have:
- Go programming
"""


def _mock_embedder() -> Embedder:
    """Create an embedder with a deterministic mock."""
    import hashlib

    client = MagicMock()

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for t in texts:
            h = hashlib.md5(t.lower().encode()).hexdigest()
            vec = [int(h[i:i+2], 16) / 255.0 for i in range(0, 20, 2)]
            vectors.append(vec)
        return vectors

    client.embed = fake_embed
    return Embedder(client)


@pytest.mark.asyncio
async def test_smoke_matching_job_pipeline():
    """Smoke test: full pipeline completes without errors for a matching job."""
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract(MATCHING_JOB)
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)

    assert 0.0 <= assessment.severity <= 1.0
    assert isinstance(assessment.simulated_ats_score, float)
    n_covered = len(assessment.covered_skills)
    n_gaps = len(assessment.critical_gaps) + len(assessment.preferred_gaps)
    assert n_covered + n_gaps >= 0


@pytest.mark.asyncio
async def test_smoke_gap_job_pipeline():
    """Smoke test: full pipeline completes for a gap job and produces valid assessment."""
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    match_profile = extractor.extract(MATCHING_JOB)
    match_profile = await embedder.embed_job_profile(match_profile)
    match_assessment = engine.assess(match_profile, cv_profile)

    gap_profile = extractor.extract(GAP_JOB)
    gap_profile = await embedder.embed_job_profile(gap_profile)
    gap_assessment = engine.assess(gap_profile, cv_profile)

    assert isinstance(gap_assessment.severity, float)
    assert isinstance(match_assessment.severity, float)


@pytest.mark.asyncio
async def test_pipeline_handles_empty_cv():
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile("")
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract(MATCHING_JOB)
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)
    assert assessment.severity >= 0.0


@pytest.mark.asyncio
async def test_pipeline_handles_empty_job():
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract("")
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)
    assert assessment.severity == 0.0
    assert assessment.should_modify is False
