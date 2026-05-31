"""HTTP tests for POST /api/documents/{match_id}/letter/regenerate (M1-T4).

This route regenerates ONLY the cover letter for a job match via the
``LetterPipeline`` singleton stored on ``app.state``. The pipeline is mocked
so no real LaTeX/Gemini work happens — mirroring how the apply-engine HTTP
tests stub ``app.state.apply_engine``.

Covered behaviour:
  1. Happy path: 200 + a letter doc reference, and the freshly persisted
     ``TailoredDocument(doc_type="letter")`` row is afterwards streamable via
     ``GET /api/documents/{match_id}/letter/pdf``.
  2. Unknown match_id → 404 (consistent with the existing letter/pdf handler).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _seed_match_with_letter_template(*, job_id_hint: str = "letter") -> int:
    """Insert a Job + JobMatch + UserProfile with a real base letter template.

    Returns the JobMatch.id. The base letter template is written under the
    test data dir so ``_resolve_letter_path`` finds an existing file.
    """
    from backend.config import settings
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch
    from backend.models.user import UserProfile

    data_dir = Path(settings.jobpilot_data_dir)
    templates_dir = data_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    base_letter = templates_dir / "base_letter.tex"
    base_letter.write_text(r"\documentclass{letter}\begin{document}Hi\end{document}", encoding="utf-8")

    async with AsyncSessionLocal() as db:
        db.add(
            UserProfile(
                id=1,
                full_name="Test User",
                email="test@example.com",
                base_letter_path="templates/base_letter.tex",
            )
        )
        job = Job(
            title="Senior Python Engineer",
            company="Acme",
            location="Paris",
            description="Build delightful Python.",
            url=f"https://jobs.example.com/{job_id_hint}",
            apply_url=f"https://jobs.example.com/{job_id_hint}/apply",
        )
        db.add(job)
        await db.flush()

        match = JobMatch(job_id=job.id, score=85.0, status="new")
        db.add(match)
        await db.commit()
        await db.refresh(match)
        return match.id


def _install_mock_letter_pipeline(test_app: TestClient) -> MagicMock:
    """Replace app.state.letter_pipeline with a mock that writes a fake PDF.

    ``generate_tailored_letter`` is awaited by the endpoint; it returns a
    ``TailoredLetter`` whose ``pdf_path``/``tex_path`` point at files that
    actually exist on disk so the subsequent letter/pdf stream succeeds.
    """
    from backend.config import settings
    from backend.latex.pipeline import LetterPipeline, TailoredLetter

    out_dir = Path(settings.jobpilot_data_dir) / "letters" / "mock"
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / "letter.tex"
    pdf_path = out_dir / "letter.pdf"
    tex_path.write_text("tex", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    mock_pipeline = MagicMock(spec=LetterPipeline)

    async def _fake_generate(*, base_letter_path, job, output_dir):  # noqa: ANN001
        return TailoredLetter(job_id=job.id, tex_path=tex_path, pdf_path=pdf_path)

    mock_pipeline.generate_tailored_letter = AsyncMock(side_effect=_fake_generate)
    test_app.app.state.letter_pipeline = mock_pipeline  # type: ignore[attr-defined]
    return mock_pipeline


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_letter_regenerate_happy_path(test_app: TestClient):
    """POST .../letter/regenerate returns 200 + a streamable letter doc."""
    match_id = asyncio.run(_seed_match_with_letter_template())
    mock_pipeline = _install_mock_letter_pipeline(test_app)

    resp = test_app.post(f"/api/documents/{match_id}/letter/regenerate")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["match_id"] == match_id
    assert body["doc_type"] == "letter"
    assert isinstance(body["doc_id"], int)
    assert body["pdf_path"]

    mock_pipeline.generate_tailored_letter.assert_awaited_once()

    # The persisted letter row is now streamable.
    pdf_resp = test_app.get(f"/api/documents/{match_id}/letter/pdf")
    assert pdf_resp.status_code == 200, pdf_resp.text
    assert pdf_resp.headers["content-type"] == "application/pdf"


def test_letter_regenerate_unknown_match_returns_404(test_app: TestClient):
    """POST .../letter/regenerate for an unknown match → 404."""
    _install_mock_letter_pipeline(test_app)
    resp = test_app.post("/api/documents/999999/letter/regenerate")
    assert resp.status_code == 404, resp.text
