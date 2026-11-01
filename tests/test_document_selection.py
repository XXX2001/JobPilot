"""W04 / F10, F16 — the selected document is the uploaded document.

Drives ``POST /api/applications/{match_id}/apply`` against the real test DB
with a spy engine, and asserts on the exact files the engine would upload.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from backend.config import DATA_DIR


def _pdf(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n" + body.encode() + b"\n%%EOF\n")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _seed(*, base_cv_path: str | None = None) -> int:
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch
    from backend.models.user import UserProfile

    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.id == 1))
        ).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(id=1, full_name="T", email="t@example.com")
            db.add(profile)
        profile.base_cv_path = base_cv_path
        profile.base_letter_path = None
        job = Job(
            title="Eng", company=f"Co{tag}", url=f"https://e.x/{tag}", dedup_hash=f"sel-{tag}"
        )
        db.add(job)
        await db.flush()
        match = JobMatch(job_id=job.id, score=80.0, status="new")
        db.add(match)
        await db.commit()
        return match.id


async def _add_doc(
    match_id: int, doc_type: str, pdf: Path | None, *, tex: Path | None = None
) -> int:
    from backend.database import AsyncSessionLocal
    from backend.models.document import TailoredDocument

    async with AsyncSessionLocal() as db:
        doc = TailoredDocument(
            job_match_id=match_id,
            doc_type=doc_type,
            tex_path=str(tex) if tex else None,
            pdf_path=str(pdf) if pdf else None,
            pdf_sha256=_sha(pdf) if pdf and pdf.exists() else None,
        )
        db.add(doc)
        await db.commit()
        return doc.id


@pytest.fixture
def client(test_app: TestClient, monkeypatch):
    """Spy engine + fake base-CV compiler; auto-submit irrelevant (manual)."""
    from backend.applier.engine import ApplicationEngine
    from backend.applier.manual_apply import ApplicationResult

    engine = MagicMock(spec=ApplicationEngine)
    engine.apply = AsyncMock(return_value=ApplicationResult(status="awaiting_user_submission", method="manual"))
    test_app.app.state.apply_engine = engine  # type: ignore[attr-defined]

    compiled: list[Path] = []

    async def compile_base_cv(source: Path, output_dir: Path) -> Path:
        compiled.append(source)
        return _pdf(output_dir / "cv.pdf", "BASE " + source.read_text())

    pipeline = MagicMock()
    pipeline.compile_base_cv = compile_base_cv
    test_app.app.state.cv_pipeline = pipeline  # type: ignore[attr-defined]
    test_app.engine_spy = engine  # type: ignore[attr-defined]
    test_app.compiled = compiled  # type: ignore[attr-defined]
    return test_app


def _apply(client: TestClient, match_id: int, **body):
    return client.post(f"/api/applications/{match_id}/apply", json={"method": "manual", **body})


def _uploaded(client: TestClient) -> tuple[Path | None, Path | None]:
    kw = client.engine_spy.apply.await_args.kwargs  # type: ignore[attr-defined]
    return kw["cv_pdf"], kw["letter_pdf"]


def _base_tex(name: str) -> Path:
    p = DATA_DIR / "templates" / f"{name}.tex"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"\\documentclass{{article}} % {name}")
    return p


def test_use_base_cv_uploads_base_pdf_even_when_tailored_exists(client):
    tex = _base_tex(f"base-{uuid.uuid4().hex[:6]}")
    mid = asyncio.run(_seed(base_cv_path=str(tex)))
    tailored = _pdf(DATA_DIR / "cvs" / f"{mid}_t" / "cv.pdf", "TAILORED")
    asyncio.run(_add_doc(mid, "cv", tailored))

    resp = _apply(client, mid, use_base_cv=True)

    assert resp.status_code == 200, resp.text
    cv, letter = _uploaded(client)
    assert cv is not None and cv.suffix == ".pdf"
    assert cv.read_bytes().startswith(b"%PDF") and b"BASE" in cv.read_bytes()
    assert letter is None  # explicit selection without a letter omits it


def test_explicit_tailored_document_is_used_not_a_newer_one(client):
    mid = asyncio.run(_seed())
    reviewed = _pdf(DATA_DIR / "cvs" / f"{mid}_a" / "cv.pdf", "REVIEWED")
    reviewed_id = asyncio.run(_add_doc(mid, "cv", reviewed))
    asyncio.run(_add_doc(mid, "cv", _pdf(DATA_DIR / "cvs" / f"{mid}_b" / "cv.pdf", "NEWER")))

    resp = _apply(client, mid, cv_document_id=reviewed_id)

    assert resp.status_code == 200, resp.text
    assert _uploaded(client)[0].read_bytes() == reviewed.read_bytes()  # pinned copy (I6)


def test_document_of_another_match_is_rejected(client):
    mid = asyncio.run(_seed())
    other = asyncio.run(_seed())
    foreign = asyncio.run(
        _add_doc(other, "cv", _pdf(DATA_DIR / "cvs" / f"{other}_x" / "cv.pdf", "X"))
    )

    resp = _apply(client, mid, cv_document_id=foreign)

    assert resp.status_code == 409
    client.engine_spy.apply.assert_not_awaited()


@pytest.mark.parametrize("damage", ["modify", "delete"])
def test_modified_or_removed_artifact_fails_before_submission(client, damage):
    mid = asyncio.run(_seed())
    pdf = _pdf(DATA_DIR / "cvs" / f"{mid}_{damage}" / "cv.pdf", "ORIGINAL")
    doc_id = asyncio.run(_add_doc(mid, "cv", pdf))
    if damage == "modify":
        _pdf(pdf, "TAMPERED")
    else:
        pdf.unlink()

    resp = _apply(client, mid, cv_document_id=doc_id)

    assert resp.status_code == 409, resp.text
    client.engine_spy.apply.assert_not_awaited()


def test_explicit_letter_is_attached_and_validated(client):
    mid = asyncio.run(_seed())
    cv_id = asyncio.run(_add_doc(mid, "cv", _pdf(DATA_DIR / "cvs" / f"{mid}_c" / "cv.pdf", "CV")))
    letter = _pdf(DATA_DIR / "letters" / f"{mid}" / "letter.pdf", "LETTER")
    letter_id = asyncio.run(_add_doc(mid, "letter", letter))

    assert (
        _apply(client, mid, cv_document_id=cv_id, letter_document_id=letter_id).status_code == 200
    )
    assert _uploaded(client)[1].read_bytes() == letter.read_bytes()  # pinned copy (I6)
    # A CV document cannot be passed off as the letter.
    assert _apply(client, mid, cv_document_id=cv_id, letter_document_id=cv_id).status_code == 409


def test_legacy_request_never_passes_tex_source_as_pdf(client):
    """F16: no tailored docs + base_cv_path pointing at an existing .tex."""
    tex = _base_tex(f"legacy-{uuid.uuid4().hex[:6]}")
    mid = asyncio.run(_seed(base_cv_path=str(tex)))

    assert _apply(client, mid).status_code == 200
    cv, _ = _uploaded(client)
    assert cv is not None and cv.suffix == ".pdf" and cv.read_bytes().startswith(b"%PDF")


def test_legacy_request_skips_tailored_row_whose_pdf_is_missing(client):
    """A stale tailored row must not be passed through; base PDF is compiled instead."""
    tex = _base_tex(f"stale-{uuid.uuid4().hex[:6]}")
    mid = asyncio.run(_seed(base_cv_path=str(tex)))
    asyncio.run(_add_doc(mid, "cv", DATA_DIR / "cvs" / f"{mid}_gone" / "cv.pdf"))

    assert _apply(client, mid).status_code == 200
    cv, _ = _uploaded(client)
    assert cv is not None and b"BASE" in cv.read_bytes()


def test_base_pdf_is_reused_for_unchanged_source(client):
    tex = _base_tex(f"reuse-{uuid.uuid4().hex[:6]}")
    mid = asyncio.run(_seed(base_cv_path=str(tex)))
    _apply(client, mid, use_base_cv=True)
    _apply(client, mid, use_base_cv=True)
    assert client.compiled.count(tex) == 1  # type: ignore[attr-defined]


def test_relative_and_absolute_base_paths_resolve_to_same_file(tmp_path):
    from backend.latex.artifacts import resolve_data_path

    (tmp_path / "templates").mkdir()
    target = tmp_path / "templates" / "cv.tex"
    target.write_text("x")
    assert resolve_data_path("templates/cv.tex", tmp_path) == target
    assert resolve_data_path(str(target), tmp_path) == target
    assert resolve_data_path("templates/missing.tex", tmp_path) is None
    assert resolve_data_path("", tmp_path) is None


def test_diff_reports_the_reviewed_document_id_with_several_versions(client):
    mid = asyncio.run(_seed())
    asyncio.run(_add_doc(mid, "cv", _pdf(DATA_DIR / "cvs" / f"{mid}_1" / "cv.pdf", "1")))
    newest = asyncio.run(_add_doc(mid, "cv", _pdf(DATA_DIR / "cvs" / f"{mid}_2" / "cv.pdf", "2")))

    resp = client.get(f"/api/documents/{mid}/diff")

    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"] == newest
    assert resp.json()["letter_document_id"] is None
    pdf = client.get(f"/api/documents/{mid}/cv/pdf?document_id={newest}")
    assert pdf.status_code == 200 and b"2" in pdf.content


def test_explicit_base_cv_without_a_base_source_is_rejected(client, monkeypatch):
    import backend.api.applications as apps

    mid = asyncio.run(_seed(base_cv_path="does/not/exist.tex"))
    monkeypatch.setattr(apps, "DATA_DIR", DATA_DIR / f"empty-{uuid.uuid4().hex[:6]}")

    resp = _apply(client, mid, use_base_cv=True)

    assert resp.status_code == 409 and "base CV" in resp.json()["detail"]
    client.engine_spy.apply.assert_not_awaited()


def test_selected_pdf_is_pinned_so_a_later_overwrite_is_not_uploaded(client):
    """Review I6: the engine gets an attempt-owned copy verified against the stored hash."""
    mid = asyncio.run(_seed())
    src = _pdf(DATA_DIR / "cvs" / f"{mid}_pin" / "cv.pdf", "REVIEWED")
    doc_id = asyncio.run(_add_doc(mid, "cv", src))

    assert _apply(client, mid, cv_document_id=doc_id).status_code == 200
    cv, _ = _uploaded(client)
    _pdf(src, "OVERWRITTEN LATER")

    assert cv != src and b"REVIEWED" in cv.read_bytes()
