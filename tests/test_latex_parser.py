from __future__ import annotations

from backend.latex.parser import LaTeXParser
from backend.latex.injector import LaTeXInjector


# Inline fixture with JOBPILOT markers — used by LaTeXParser / LaTeXInjector tests.
# sample_cv.tex no longer has markers (new pipeline is marker-free), so we keep
# a self-contained marker string here for the LetterPipeline-compatible parser tests.
MARKER_TEX = """\
\\documentclass{article}
\\begin{document}

% --- JOBPILOT:SUMMARY:START ---
Experienced software engineer with 5 years in distributed systems.
% --- JOBPILOT:SUMMARY:END ---

\\section{Experience}
% --- JOBPILOT:EXPERIENCE:START ---
\\textbf{Software Engineer} \\hfill 2022--Present \\\\
\\textit{TechCorp}
\\begin{itemize}
    \\item Designed distributed data pipeline processing 10TB/day
    \\item Led migration to microservices, reducing deploy time by 80\\%
    \\item Mentored 3 junior engineers on system design
\\end{itemize}
% --- JOBPILOT:EXPERIENCE:END ---

\\end{document}
"""


def read_fixture() -> str:
    return MARKER_TEX


def test_extract_with_markers():
    tex = read_fixture()
    parser = LaTeXParser()
    secs = parser.extract_sections(tex)
    assert secs.has_markers is True
    assert secs.summary is not None


def test_extract_bullets_from_fixture():
    tex = read_fixture()
    parser = LaTeXParser()
    secs = parser.extract_sections(tex)
    assert len(secs.experience_bullets) == 3


def test_no_markers_graceful_fallback():
    parser = LaTeXParser()
    secs = parser.extract_sections("no markers here")
    assert secs.has_markers is False


def test_validate_markers_no_warnings():
    tex = read_fixture()
    parser = LaTeXParser()
    warnings = parser.validate_markers(tex)
    assert warnings == []


def test_inject_summary_round_trip():
    tex = read_fixture()
    parser = LaTeXParser()
    injector = LaTeXInjector()
    secs = parser.extract_sections(tex)
    assert secs.summary is not None
    new_summary = "Updated summary: focused on backend, APIs, and testing."
    new_tex = injector.inject_summary_edit(tex, new_summary)
    secs2 = parser.extract_sections(new_tex)
    assert secs2.summary is not None
    assert new_summary in secs2.summary
