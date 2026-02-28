"""FastAPI routes for /api/documents (T14 - document management)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.models.document import TailoredDocument
from backend.models.job import JobMatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class DocumentOut(BaseModel):
    id: int
    job_match_id: Optional[int]
    doc_type: str
    tex_path: Optional[str]
    pdf_path: Optional[str]
    diff_json: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


class RegenerateRequest(BaseModel):
    force: bool = False


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[DocumentOut])
async def list_documents(db: DBSession):
    """List all tailored documents."""
    stmt = select(TailoredDocument).order_by(TailoredDocument.created_at.desc())
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [DocumentOut.model_validate(d) for d in docs]


@router.get("/{match_id}/cv/pdf")
async def get_cv_pdf(match_id: int, db: DBSession):
    """Stream the tailored CV PDF for a given job match."""
    stmt = select(TailoredDocument).where(
        TailoredDocument.job_match_id == match_id,
        TailoredDocument.doc_type == "cv",
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

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

    pdf_path = Path(doc.pdf_path)
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"CV PDF file not found on disk: {doc.pdf_path}",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"cv_match_{match_id}.pdf",
    )


@router.get("/{match_id}/letter/pdf")
async def get_letter_pdf(match_id: int, db: DBSession):
    """Stream the tailored cover letter PDF for a given job match."""
    stmt = select(TailoredDocument).where(
        TailoredDocument.job_match_id == match_id,
        TailoredDocument.doc_type == "letter",
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

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

    pdf_path = Path(doc.pdf_path)
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Letter PDF file not found on disk: {doc.pdf_path}",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"letter_match_{match_id}.pdf",
    )


@router.get("/{match_id}/diff")
async def get_cv_diff(match_id: int, db: DBSession):
    """Return the JSON diff of CV changes for a given job match."""
    stmt = select(TailoredDocument).where(
        TailoredDocument.job_match_id == match_id,
        TailoredDocument.doc_type == "cv",
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No CV document found for match {match_id}",
        )

    return {
        "match_id": match_id,
        "diff": doc.diff_json or [],
        "generated_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.post("/{match_id}/regenerate")
async def regenerate_documents(
    match_id: int,
    body: RegenerateRequest,
    background_tasks: BackgroundTasks,
    db: DBSession,
):
    """Trigger re-generation of tailored CV and letter for a job match."""
    # Verify the match exists
    match_stmt = select(JobMatch).where(JobMatch.id == match_id)
    match_result = await db.execute(match_stmt)
    match = match_result.scalar_one_or_none()

    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"JobMatch {match_id} not found",
        )

    # Mark existing documents as stale (delete them so they'll be regenerated)
    if body.force:
        existing_stmt = select(TailoredDocument).where(TailoredDocument.job_match_id == match_id)
        existing_result = await db.execute(existing_stmt)
        existing_docs = existing_result.scalars().all()
        for d in existing_docs:
            await db.delete(d)
        await db.commit()

    # Queue background regeneration (actual pipeline call deferred to Wave 3 scheduler)
    logger.info("Regeneration queued for match_id=%d (force=%s)", match_id, body.force)

    return {
        "match_id": match_id,
        "status": "queued",
        "message": "Document regeneration has been queued",
    }
