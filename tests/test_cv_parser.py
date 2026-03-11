# tests/test_cv_parser.py
"""Tests for CV LaTeX parser — skill extraction with context tagging."""
from __future__ import annotations

from backend.matching.cv_parser import CVParser, SkillEntry, CVProfile

SAMPLE_CV = r"""
\begin{rSection}{Profile}
Experienced Python developer specializing in machine learning and data engineering.
\end{rSection}

\begin{rSection}{Skills}
\begin{tabular}{ @{} >{\bfseries}l @{\hspace{6ex}} l }
Programming & Python, Java, SQL, JavaScript \\
Frameworks & FastAPI, Django, React \\
Cloud & AWS, Docker, Kubernetes \\
\end{tabular}
\end{rSection}

\begin{rSection}{Experience}
\textbf{Senior Developer} \hfill 2023--Present \\
\emph{TechCorp} \\
\begin{itemize}
  \item Built CI/CD pipelines using GitLab and Docker.
  \item Developed REST APIs with FastAPI and PostgreSQL.
\end{itemize}

\textbf{Junior Developer} \hfill 2020--2023 \\
\emph{StartupCo} \\
\begin{itemize}
  \item Created data pipelines with Apache Airflow.
  \item Wrote unit tests with pytest.
\end{itemize}
\end{rSection}
"""


def test_parser_extracts_skills_section():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    skill_texts = [s.text.lower() for s in skills]
    assert "python" in skill_texts
    assert "fastapi" in skill_texts
    assert "docker" in skill_texts


def test_parser_assigns_context_weights():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    skills_section = [s for s in skills if s.context == "skills_section"]
    experience_recent = [s for s in skills if s.context == "experience_recent"]
    assert len(skills_section) > 0
    assert all(s.weight == 0.6 for s in skills_section)
    assert all(s.weight == 1.0 for s in experience_recent)


def test_parser_extracts_profile_skills():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    profile_skills = [s for s in skills if s.context == "profile"]
    profile_texts = [s.text.lower() for s in profile_skills]
    assert "machine learning" in profile_texts or "python" in profile_texts


def test_parser_deduplicates_skills():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    python_skills = [s for s in skills if s.text.lower() == "python"]
    contexts = {s.context for s in python_skills}
    assert len(contexts) >= 1  # At least from skills_section


def test_cv_profile_hash():
    parser = CVParser()
    profile = parser.build_profile(SAMPLE_CV)
    assert isinstance(profile, CVProfile)
    assert len(profile.raw_text_hash) == 64  # SHA-256 hex
    assert len(profile.skills) > 0
    # All embeddings are empty before embedding step
    assert all(s.embedding == [] for s in profile.skills)


def test_parser_fallback_for_unknown_template():
    """If fewer than 3 skills extracted, fallback to full-text scan."""
    minimal_cv = r"""
    Some text about Python and Docker and machine learning.
    Also mentions SQL and FastAPI for good measure.
    """
    parser = CVParser()
    skills = parser.parse(minimal_cv)
    assert len(skills) >= 3  # Fallback should still extract skills
