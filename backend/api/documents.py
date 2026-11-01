from __future__ import annotations
# pyright: reportInvalidTypeForm=false

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.config import DATA_DIR, settings
from backend.latex.artifacts import pdf_sha256, resolve_base_source, resolve_data_path
from backend.models.document import TailoredDocument
from backend.models.job import Job, JobMatch
from backend.models.schemas import JobDetails
from backend.models.user import UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_match_id: Optional[int]
    doc_type: str
    tex_path: Optional[str]
    pdf_path: Optional[str]
    diff_json: Optional[Any]
    created_at: datetime


class RegenerateRequest(BaseModel):
    force: bool = False


class ValidateTemplateRequest(BaseModel):
    tex_content: str


class ValidateTemplateResponse(BaseModel):
    has_markers: bool
    warnings: list[str]


class CompileTestResponse(BaseModel):
    ok: bool
    error_log: Optional[str] = None


# Cap the captured compile log so a huge Tectonic dump can't bloat the response.
_MAX_ERROR_LOG_CHARS = 8000


class CVDiffResponse(BaseModel):
    match_id: int
    # W04: the reviewed CV version; send it back as ``cv_document_id`` on apply.
    document_id: int
    # Newest cover letter for the match, if any (``letter_document_id`` on apply).
    letter_document_id: Optional[int] = None
    diff: Any
    generated_at: Optional[str] = None


OperationState = Literal["queued", "running", "succeeded", "failed"]


class RegenerateResponse(BaseModel):
    match_id: int
    operation_id: int
    state: OperationState
    message: str


class DocumentOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    job_match_id: int
    state: OperationState
    document_id: Optional[int] = None
    error: Optional[str] = None
    attempts: int
    created_at: datetime
    updated_at: datetime


class LetterRegenerateResponse(BaseModel):
    match_id: int
    doc_id: int
    doc_type: Literal["letter"]
    tex_path: Optional[str]
    pdf_path: Optional[str]
    status: Literal["regenerated"]


def _resolve_letter_path(profile: Optional[UserProfile], data_dir: Path) -> Optional[Path]:
    """Base cover-letter source: the profile path, else ``templates/*letter*.tex``."""
    return resolve_base_source(profile.base_letter_path if profile else None, data_dir, "*letter*.tex")


def _resolve_cv_path(profile: Optional[UserProfile], data_dir: Path) -> Optional[Path]:
    """Base CV source: the profile path, else ``templates/*.tex``."""
    return resolve_base_source(profile.base_cv_path if profile else None, data_dir, "*.tex")


async def _latest_document(db, match_id: int, doc_type: str) -> Optional[TailoredDocument]:
    """Newest document of *doc_type* for a match (stable on equal timestamps)."""
    stmt = (
        select(TailoredDocument)
        .where(TailoredDocument.job_match_id == match_id, TailoredDocument.doc_type == doc_type)
        .order_by(TailoredDocument.created_at.desc(), TailoredDocument.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _doc_for(db, match_id: int, doc_type: str, document_id: Optional[int]) -> Optional[TailoredDocument]:
    """A specific version of this match/type, or the newest when *document_id* is None."""
    if document_id is None:
        return await _latest_document(db, match_id, doc_type)
    doc = await db.get(TailoredDocument, document_id)
    return doc if doc is not None and doc.job_match_id == match_id and doc.doc_type == doc_type else None


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[DocumentOut])
async def list_documents(db: DBSession):
    """List all tailored documents."""
    stmt = select(TailoredDocument).order_by(TailoredDocument.created_at.desc())
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [DocumentOut.model_validate(d) for d in docs]


@router.post("/validate-template", response_model=ValidateTemplateResponse)
async def validate_template(body: ValidateTemplateRequest) -> ValidateTemplateResponse:
    """Check whether a LaTeX CV template contains JOBPILOT markers."""
    from backend.latex.parser import LaTeXParser

    parser = LaTeXParser()
    sections = parser.extract_sections(body.tex_content)
    warnings = parser.validate_markers(body.tex_content)
    return ValidateTemplateResponse(has_markers=sections.has_markers, warnings=warnings)


@router.post("/compile-test", response_model=CompileTestResponse)
async def compile_test(db: DBSession) -> CompileTestResponse:
    """Compile the configured base CV template to surface Tectonic errors early.

    Resolves the stored base CV (same resolution as CV generation) and runs a
    real Tectonic compile on it. The JOBPILOT markers are LaTeX comments, so a
    bare, un-substituted template still compiles — no placeholder substitution
    is needed.

    A failed compile is a *successful test*: it returns ``{ok: false,
    error_log: ...}`` rather than a 5xx. Only a missing template (a
    configuration problem) raises 400.
    """
    import tempfile

    from backend.latex.compiler import (
        LaTeXCompilationError,
        LaTeXCompiler,
    )

    profile = (await db.execute(select(UserProfile).limit(1))).scalar_one_or_none()
    data_dir = Path(settings.jobpilot_data_dir)
    cv_path = _resolve_cv_path(profile, data_dir)
    if cv_path is None:
        raise HTTPException(
            status_code=400,
            detail="No base CV template configured",
        )

    compiler = LaTeXCompiler()
    # Compile into a throwaway directory so the test never pollutes the
    # templates folder with a stray PDF. ``LaTeXCompileTimeout`` is a subclass
    # of ``LaTeXCompilationError``, so the single ``except`` covers both.
    with tempfile.TemporaryDirectory(prefix="jobpilot-compile-test-") as tmp:
        try:
            await compiler.compile(cv_path, output_dir=Path(tmp))
        except LaTeXCompilationError as exc:
            return CompileTestResponse(ok=False, error_log=str(exc)[:_MAX_ERROR_LOG_CHARS])

    return CompileTestResponse(ok=True, error_log=None)


@router.get("/{match_id}/cv/pdf", response_class=FileResponse)
async def get_cv_pdf(match_id: int, db: DBSession, document_id: Optional[int] = None):
    """Stream a CV PDF for a job match: *document_id* if given, else the newest."""
    doc = await _doc_for(db, match_id, "cv", document_id)

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No CV document found for match {match_id}",
        )

    if not doc.pdf_path:
        raise HTTPException(
            status_code=404,
            detail="CV PDF has not been compiled yet",
        )

    pdf_path = resolve_data_path(doc.pdf_path, DATA_DIR)
    if pdf_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"CV PDF file not found on disk: {doc.pdf_path}",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"cv_match_{match_id}.pdf",
    )


@router.get("/{match_id}/letter/pdf", response_class=FileResponse)
async def get_letter_pdf(match_id: int, db: DBSession, document_id: Optional[int] = None):
    """Stream a cover letter PDF for a job match: *document_id* if given, else the newest."""
    doc = await _doc_for(db, match_id, "letter", document_id)

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No letter document found for match {match_id}",
        )

    if not doc.pdf_path:
        raise HTTPException(
            status_code=404,
            detail="Letter PDF has not been compiled yet",
        )

    pdf_path = resolve_data_path(doc.pdf_path, DATA_DIR)
    if pdf_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Letter PDF file not found on disk: {doc.pdf_path}",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"letter_match_{match_id}.pdf",
    )


@router.get("/{match_id}/diff", response_model=CVDiffResponse)
async def get_cv_diff(match_id: int, db: DBSession) -> CVDiffResponse:
    """Return the JSON diff of the newest CV version for a given job match."""
    doc = await _latest_document(db, match_id, "cv")

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No CV document found for match {match_id}",
        )

    letter = await _latest_document(db, match_id, "letter")
    return CVDiffResponse(
        match_id=match_id,
        document_id=doc.id,
        letter_document_id=letter.id if letter else None,
        diff=doc.diff_json or [],
        generated_at=doc.created_at.isoformat() if doc.created_at else None,
    )


@router.post("/{match_id}/regenerate", response_model=RegenerateResponse, status_code=202)
async def regenerate_documents(
    match_id: int, body: RegenerateRequest, db: DBSession, request: Request
) -> RegenerateResponse:
    """Accept a CV regeneration (W05). The operation is persisted before this
    returns; poll ``GET /api/documents/operations/{operation_id}``.

    Duplicate requests return the active operation. Without ``force`` an
    unchanged, still-valid result is reused; ``force`` only bypasses reuse —
    earlier versions are kept, and a failure leaves the newest valid PDF in place.
    """
    from backend.latex import regeneration
    from backend.latex.artifacts import ArtifactError

    try:
        op = await regeneration.request_cv_regeneration(db, match_id, force=body.force, data_dir=DATA_DIR)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if op.state == "queued":
        regeneration.schedule(request.app, op.id)
    logger.info("Regeneration operation=%d state=%s for match_id=%d", op.id, op.state, match_id)
    return RegenerateResponse(
        match_id=match_id,
        operation_id=op.id,
        state=op.state,  # type: ignore[arg-type]
        message="Regeneration accepted" if op.state in ("queued", "running") else "Reused unchanged result",
    )


@router.get("/operations/{operation_id}", response_model=DocumentOperationOut)
async def get_operation(operation_id: int, db: DBSession) -> DocumentOperationOut:
    from backend.models.operation import DocumentOperation

    op = await db.get(DocumentOperation, operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {operation_id} not found")
    return DocumentOperationOut.model_validate(op)


@router.post("/{match_id}/letter/regenerate", response_model=LetterRegenerateResponse)
async def regenerate_letter(
    match_id: int,
    request: Request,
    db: DBSession,
) -> LetterRegenerateResponse:
    """Regenerate ONLY the tailored cover letter for a job match.

    Reuses the ``LetterPipeline`` singleton on ``app.state`` (the same pipeline
    the batch runner is wired to), then upserts the
    ``TailoredDocument(doc_type="letter")`` row so the result is immediately
    streamable via ``GET /api/documents/{match_id}/letter/pdf``.
    """
    # Resolve the job match (and its job) — 404 for an unknown/match-less id,
    # consistent with the letter/pdf handler.
    stmt = (
        select(JobMatch, Job)
        .join(Job, Job.id == JobMatch.job_id)
        .where(JobMatch.id == match_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"JobMatch {match_id} not found",
        )
    _match, job = row

    pipeline = getattr(request.app.state, "letter_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="Letter generation pipeline is not available",
        )

    profile = (await db.execute(select(UserProfile).limit(1))).scalar_one_or_none()
    data_dir = Path(settings.jobpilot_data_dir)
    base_letter_path = _resolve_letter_path(profile, data_dir)
    if base_letter_path is None:
        raise HTTPException(
            status_code=400,
            detail="No base cover-letter template configured",
        )

    job_details = JobDetails(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location or "",
        description=job.description or "",
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        url=job.url,
        apply_url=job.apply_url or "",
        apply_method=job.apply_method or "",
        country=job.country or "",
        posted_at=job.posted_at,
    )

    import re

    slug = re.sub(r"[^\w]+", "_", (job.title or "job").lower()).strip("_")[:50]
    # W05: every regeneration is a new immutable version in its own directory.
    output_dir = data_dir / "letters" / f"{match_id}_{slug}" / uuid.uuid4().hex[:12]

    tailored = await pipeline.generate_tailored_letter(
        base_letter_path=base_letter_path,
        job=job_details,
        output_dir=output_dir,
    )

    # Insert a new version; earlier letters stay retrievable by id. Nothing is
    # written if generation raised above, so the previous letter stays current.
    doc = TailoredDocument(job_match_id=match_id, doc_type="letter")
    db.add(doc)
    letter_pdf = getattr(tailored, "pdf_path", None)
    doc.tex_path = str(tailored.tex_path) if getattr(tailored, "tex_path", None) else None
    doc.pdf_path = str(letter_pdf) if letter_pdf else None
    doc.pdf_sha256 = pdf_sha256(Path(letter_pdf)) if letter_pdf else None
    await db.commit()
    await db.refresh(doc)

    logger.info("Cover letter regenerated for match_id=%d (doc_id=%d)", match_id, doc.id)

    return LetterRegenerateResponse(
        match_id=match_id,
        doc_id=doc.id,
        doc_type="letter",
        tex_path=doc.tex_path,
        pdf_path=doc.pdf_path,
        status="regenerated",
    )
