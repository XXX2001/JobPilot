from __future__ import annotations
from pathlib import Path
import pytest

from backend.latex.parser import LaTeXParser
from backend.latex.injector import LaTeXInjector


FIXTURE = Path(__file__).parent / "fixtures" / "sample_cv.tex"


def read_fixture() -> str:
    return FIXTURE.read_text(encoding="utf8")


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
