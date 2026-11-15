# tests/test_cv_parser.py
"""Tests for the CV LaTeX parser: gazetteer skills with context tagging."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.matching.cv_parser import CVParser, CVProfile
from backend.matching.gazetteer import Gazetteer

EXAMPLE_CV = Path(__file__).resolve().parents[1] / "scripts/defaults/templates/example_cv.tex"

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


@pytest.fixture(scope="module")
def g() -> Gazetteer:
    return Gazetteer.load()


def _by_name(cv_tex: str, g: Gazetteer) -> dict[str, tuple[str, float]]:
    return {s.text: (s.context, s.weight) for s in CVParser().parse(cv_tex, g)}


def test_parser_returns_canonical_gazetteer_skills(g):
    skills = _by_name(SAMPLE_CV, g)
    assert {"python", "fastapi", "docker", "kubernetes", "machine learning"} <= set(skills)
    assert set(skills) == g.skills(SAMPLE_CV)


def test_parser_lists_each_skill_once_with_its_strongest_context(g):
    names = [s.text for s in CVParser().parse(SAMPLE_CV, g)]
    assert len(names) == len(set(names))
    skills = _by_name(SAMPLE_CV, g)
    assert skills["docker"] == ("experience_recent", 1.0)  # also in the skills table
    assert skills["java"] == ("skills_section", 0.6)
    assert skills["machine learning"] == ("profile", 0.5)
    assert skills["airflow"] == ("experience_older", 0.4)
    assert skills["python"] == ("skills_section", 0.6)  # beats the profile mention


def test_cv_profile_hash(g):
    profile = CVParser().build_profile(SAMPLE_CV, g)
    assert isinstance(profile, CVProfile)
    assert len(profile.raw_text_hash) == 64  # SHA-256 hex
    assert len(profile.skills) > 0


def test_parser_reads_a_cv_without_known_sections(g):
    minimal_cv = r"""
    Some text about Python and Docker and machine learning.
    Also mentions SQL and FastAPI for good measure.
    """
    skills = set(_by_name(minimal_cv, g))
    assert skills == {"python", "docker", "machine learning", "sql", "fastapi"}


def test_example_cv_skills_include_go_kubernetes_fastapi(g):
    """The pattern-based parser missed these in the bundled example CV."""
    skills = _by_name(EXAMPLE_CV.read_text(encoding="utf-8"), g)
    assert {"go", "kubernetes", "fastapi"} <= set(skills)


def test_owner_keywords_extend_the_cv_skills():
    g = Gazetteer.load(["soudure tig"])
    assert "soudure tig" in _by_name(r"Soudeur, soudure TIG et MIG.", g)


# --- LaTeX escapes -----------------------------------------------------------------


def _names(cv_tex: str, g: Gazetteer) -> set[str]:
    return {entry.text for entry in CVParser().parse(cv_tex, g)}


def test_latex_escapes_are_read_as_plain_text(g):
    cv = r"\begin{rSection}{Exp} Ingénieur R\&D, C\#, ROS~2 \end{rSection}"
    names = _names(cv, g)
    assert {"c#", "ros 2"} <= names
    assert not names & {"r", "c"}


def test_latex_accent_macros_are_read_as_letters(g):
    assert "embedded systems" in _names(r"syst\`emes embarqu\'es", g)
    assert "embedded systems" in _names(r"syst{\`e}mes embarqu\'{e}s", g)


def test_tabular_column_spec_is_not_cv_text(g):
    cv = r"\begin{tabular}{@{} L C @{\hspace{6ex}} R} Python & Docker \\ \end{tabular}"
    assert _names(cv, g) == {"python", "docker"}
    assert _names(r"\begin{tabularx}{\textwidth}{L C R} Python \\ \end{tabularx}", g) == {"python"}


def test_latex_line_breaks_and_sections_survive_de_escaping(g):
    # "\\" before a letter is a line break, not an accent macro; contexts still resolve.
    cv = r"""
\begin{rSection}{Experience}
\textbf{Dev} \hfill 2023 \\
Rust \\'and Go
\end{rSection}
"""
    by_name = {e.text: e.context for e in CVParser().parse(cv, g)}
    assert by_name == {"rust": "experience_recent", "go": "experience_recent"}
