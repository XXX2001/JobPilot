"""Tests for CVApplicator — applies CVReplacement items with safety checks."""
from __future__ import annotations
from backend.latex.applicator import CVApplicator
from backend.llm.validators import CVReplacement

SAMPLE_CV = r"""\begin{rSection}{Profile}
Junior Food Scientist with laboratory experience in fish cell lines.
\end{rSection}
\begin{rSection}{Additional Information}
Skills & Cell culture techniques, XTT assays, HACCP, GMP
\end{rSection}
"""


def _make_replacement(**kwargs) -> CVReplacement:
    defaults = dict(
        section="Profile",
        original_text="Junior Food Scientist with laboratory experience in fish cell lines.",
        replacement_text="Junior Food Scientist with laboratory experience in fish cell lines, motivated to develop ISO 22000 expertise.",
        reason="Addresses ISO 22000 gap",
        job_requirement_matched="ISO 22000",
        confidence=0.85,
    )
    defaults.update(kwargs)
    return CVReplacement(**defaults)


def test_applicator_applies_valid_replacement():
    applicator = CVApplicator()
    replacement = _make_replacement()
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert replacement.replacement_text in result_tex
    assert len(applied) == 1


def test_applicator_rejects_low_confidence():
    applicator = CVApplicator()
    replacement = _make_replacement(confidence=0.5)
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV
    assert len(applied) == 0


def test_applicator_rejects_missing_original():
    """Replacement whose original_text is not in the CV is skipped."""
    applicator = CVApplicator()
    replacement = _make_replacement(original_text="This text does not exist in the CV at all.")
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV
    assert len(applied) == 0


def test_applicator_rejects_new_latex_commands():
    """Replacement that introduces new LaTeX commands is rejected."""
    applicator = CVApplicator()
    replacement = _make_replacement(
        replacement_text=r"\textbf{Junior} Food Scientist with laboratory experience in fish cell lines.",
    )
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV
    assert len(applied) == 0


def test_applicator_caps_at_three():
    """Only first 3 (by confidence) are ever applied."""
    applicator = CVApplicator()
    # 4 replacements in distinct non-overlapping text
    cv = "aaa bbb ccc ddd eee"
    replacements = [
        CVReplacement(section="Profile", original_text=text, replacement_text=text + "X",
                      reason="r", job_requirement_matched="x", confidence=conf)
        for text, conf in [("aaa", 0.95), ("bbb", 0.90), ("ccc", 0.85), ("ddd", 0.80)]
    ]
    result_tex, applied = applicator.apply(cv, replacements)
    assert len(applied) == 3
    assert "dddX" not in result_tex  # 4th was dropped


def test_applicator_preserves_original_string():
    """The original cv_tex string is never mutated (Python str is immutable, but document intent)."""
    applicator = CVApplicator()
    original_cv = SAMPLE_CV  # same reference
    replacement = _make_replacement()
    applicator.apply(SAMPLE_CV, [replacement])
    assert SAMPLE_CV == original_cv


def test_applicator_applies_multiple_valid_replacements():
    """Two non-overlapping valid replacements are both applied."""
    applicator = CVApplicator()
    cv = r"\begin{rSection}{Profile}First phrase. Second phrase.\end{rSection}"
    r1 = CVReplacement(section="Profile", original_text="First phrase.",
                       replacement_text="First phrase updated.", reason="r1",
                       job_requirement_matched="x", confidence=0.9)
    r2 = CVReplacement(section="Profile", original_text="Second phrase.",
                       replacement_text="Second phrase updated.", reason="r2",
                       job_requirement_matched="y", confidence=0.8)
    result_tex, applied = applicator.apply(cv, [r1, r2])
    assert len(applied) == 2
    assert "First phrase updated." in result_tex
    assert "Second phrase updated." in result_tex
