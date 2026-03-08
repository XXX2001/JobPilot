"""Unit tests for _resolve_cv_path — CV auto-detection fallback logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from backend.scheduler.morning_batch import _resolve_cv_path


# ── Helpers ───────────────────────────────────────────────────────────────────


def _profile(base_cv_path: str | None = None) -> MagicMock:
    obj = MagicMock()
    obj.base_cv_path = base_cv_path
    return obj


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_cv_path_from_profile_used_when_set(tmp_path: Path) -> None:
    """When profile.base_cv_path points to a real .tex file, use it as-is."""
    cv_file = tmp_path / "my_resume.tex"
    cv_file.write_text(r"\documentclass{article}\begin{document}cv\end{document}")

    profile = _profile(base_cv_path=str(cv_file))
    result = _resolve_cv_path(profile, data_dir=tmp_path)

    assert result == cv_file


def test_cv_path_auto_detected_from_templates(tmp_path: Path) -> None:
    """When profile has no base_cv_path, auto-detect from templates/ dir."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    cv_file = templates_dir / "my_cv.tex"
    cv_file.write_text(r"\documentclass{article}\begin{document}cv\end{document}")

    profile = _profile(base_cv_path=None)
    result = _resolve_cv_path(profile, data_dir=tmp_path)

    assert result == cv_file


def test_cv_path_none_when_no_templates(tmp_path: Path) -> None:
    """When profile has no path and templates/ is empty, return None."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()

    profile = _profile(base_cv_path=None)
    result = _resolve_cv_path(profile, data_dir=tmp_path)

    assert result is None


def test_cv_path_auto_detected_is_alphabetically_first(tmp_path: Path) -> None:
    """With multiple .tex files in templates/, pick the alphabetically first one."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "zzz_cv.tex").write_text("z")
    (templates_dir / "aaa_cv.tex").write_text("a")
    (templates_dir / "mmm_cv.tex").write_text("m")

    profile = _profile(base_cv_path=None)
    result = _resolve_cv_path(profile, data_dir=tmp_path)

    assert result == templates_dir / "aaa_cv.tex"


def test_cv_path_fallback_used_when_profile_path_does_not_exist(tmp_path: Path) -> None:
    """If profile.base_cv_path is set but the file is gone, fall back to templates/."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    fallback = templates_dir / "fallback.tex"
    fallback.write_text("fallback")

    profile = _profile(base_cv_path=str(tmp_path / "nonexistent.tex"))
    result = _resolve_cv_path(profile, data_dir=tmp_path)

    assert result == fallback
