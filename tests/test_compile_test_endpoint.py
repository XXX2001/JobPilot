"""HTTP tests for POST /api/documents/compile-test (M2-T5).

This route runs a real Tectonic compile of the user's configured base CV
template so they catch LaTeX errors BEFORE running a batch. Tectonic itself is
mocked (the ``LaTeXCompiler.compile`` coroutine) so no real LaTeX work happens
— mirroring how ``tests/test_latex_pipeline.py`` stubs the compiler.

A failed compile is NOT a server error: the endpoint returns
``{ok: false, error_log: ...}`` rather than raising 500.

Covered behaviour:
  1. Success path: compiler mocked to return a PDF path → ``ok: true``.
  2. Failure path: compiler raises ``LaTeXCompilationError`` → ``ok: false`` +
     ``error_log``.
  3. Timeout path: compiler raises ``LaTeXCompileTimeout`` → ``ok: false`` +
     ``error_log``.
  4. No template configured → 400.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _seed_profile_with_cv(data_dir: Path, *, write_template: bool = True) -> None:
    """Insert a UserProfile pointing at a base CV template under *data_dir*.

    When *write_template* is True a bare ``.tex`` file is written so
    ``_resolve_cv_path`` finds an existing file.
    """
    from backend.database import AsyncSessionLocal
    from backend.models.user import UserProfile

    if write_template:
        templates_dir = data_dir / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "base_cv.tex").write_text(
            r"\documentclass{article}\begin{document}Hi\end{document}",
            encoding="utf-8",
        )

    async with AsyncSessionLocal() as db:
        db.add(
            UserProfile(
                id=1,
                full_name="Test User",
                email="test@example.com",
                base_cv_path="templates/base_cv.tex",
            )
        )
        await db.commit()


def _patch_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``settings.jobpilot_data_dir`` at a clean *tmp_path*."""
    from backend.config import settings

    monkeypatch.setattr(settings, "jobpilot_data_dir", str(tmp_path))
    return tmp_path


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_compile_test_success(test_app: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Tectonic mocked to succeed → 200 + {ok: true, error_log: null}."""
    data_dir = _patch_data_dir(monkeypatch, tmp_path)
    asyncio.run(_seed_profile_with_cv(data_dir))

    async def _fake_compile(self, tex_path, output_dir=None):  # noqa: ANN001
        return Path(output_dir or tex_path.parent) / "base_cv.pdf"

    monkeypatch.setattr(
        "backend.latex.compiler.LaTeXCompiler.compile",
        _fake_compile,
    )

    resp = test_app.post("/api/documents/compile-test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["error_log"] is None


def test_compile_test_failure(test_app: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Compiler raises LaTeXCompilationError → 200 + {ok: false, error_log}."""
    from backend.latex.compiler import LaTeXCompilationError

    data_dir = _patch_data_dir(monkeypatch, tmp_path)
    asyncio.run(_seed_profile_with_cv(data_dir))

    monkeypatch.setattr(
        "backend.latex.compiler.LaTeXCompiler.compile",
        AsyncMock(side_effect=LaTeXCompilationError("Tectonic exited 1:\nUndefined control sequence \\foo")),
    )

    resp = test_app.post("/api/documents/compile-test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error_log"]
    assert "Undefined control sequence" in body["error_log"]


def test_compile_test_timeout(test_app: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Compiler raises LaTeXCompileTimeout → 200 + {ok: false, error_log}."""
    from backend.latex.compiler import LaTeXCompileTimeout

    data_dir = _patch_data_dir(monkeypatch, tmp_path)
    asyncio.run(_seed_profile_with_cv(data_dir))

    monkeypatch.setattr(
        "backend.latex.compiler.LaTeXCompiler.compile",
        AsyncMock(side_effect=LaTeXCompileTimeout("Tectonic timed out after 60.0s compiling base_cv.tex")),
    )

    resp = test_app.post("/api/documents/compile-test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error_log"]
    assert "timed out" in body["error_log"]


def test_compile_test_no_template_returns_400(
    test_app: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """No base CV configured / found → 400 (not a compile result)."""
    _patch_data_dir(monkeypatch, tmp_path)
    # No profile seeded and no templates dir → resolution returns None.

    # The compiler must never be invoked when there's no template.
    called = AsyncMock()
    monkeypatch.setattr("backend.latex.compiler.LaTeXCompiler.compile", called)

    resp = test_app.post("/api/documents/compile-test")
    assert resp.status_code == 400, resp.text
    called.assert_not_awaited()
