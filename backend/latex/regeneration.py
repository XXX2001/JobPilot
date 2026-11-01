"""Durable CV regeneration (W05 / F09).

``request_cv_regeneration`` persists a ``DocumentOperation`` before the HTTP
response; ``schedule`` runs it on an application-owned task with its own DB
session; ``resume_pending`` re-runs accepted work after a restart.

Publication rule: a new ``TailoredDocument`` row is inserted only after the
generated PDF exists and is valid, in the same transaction that marks the
operation succeeded. A failure inserts nothing, so the previous version stays
the newest valid document. Output goes to a per-operation directory, so a
retry or crash never overwrites a published PDF.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from backend.latex.artifacts import (
    ArtifactError,
    diff_to_json,
    pdf_sha256,
    resolve_base_source,
    validate_document,
)
from backend.models.document import TailoredDocument
from backend.models.job import Job, JobMatch
from backend.models.operation import ACTIVE_STATES, DocumentOperation
from backend.models.schemas import JobDetails
from backend.models.user import UserProfile

logger = logging.getLogger(__name__)

KIND = "regenerate_cv"
_MAX_ERROR_CHARS = 2000


async def _load(db, match_id: int) -> tuple[JobMatch, Job, UserProfile | None]:
    match = await db.get(JobMatch, match_id)
    if match is None:
        raise LookupError(f"JobMatch {match_id} not found")
    job = await db.get(Job, match.job_id)
    if job is None:
        raise LookupError(f"Job {match.job_id} not found")
    profile = (await db.execute(select(UserProfile).limit(1))).scalar_one_or_none()
    return match, job, profile


def _input_version(source: Path, job: Job) -> str:
    h = hashlib.sha256(KIND.encode())
    h.update(source.read_bytes())
    for part in (job.title, job.company, job.location, job.description):
        h.update(b"\0" + (part or "").encode())
    return h.hexdigest()


async def request_cv_regeneration(
    db, match_id: int, *, force: bool, data_dir: Path
) -> DocumentOperation:
    """Return the operation that will (or did) produce this regeneration.

    An active operation for the match is returned as-is. Without *force*, a
    succeeded operation whose inputs are unchanged and whose document is still
    valid is reused. *force* only bypasses that reuse; it never deletes history.
    """
    _match, job, profile = await _load(db, match_id)
    source = resolve_base_source(profile.base_cv_path if profile else None, data_dir, "*.tex")
    if source is None:
        raise ArtifactError("No base CV is configured; upload one in Settings → Profile.")
    version = _input_version(source, job)

    async def active() -> DocumentOperation | None:
        stmt = select(DocumentOperation).where(
            DocumentOperation.kind == KIND,
            DocumentOperation.job_match_id == match_id,
            DocumentOperation.state.in_(ACTIVE_STATES),
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    existing = await active()
    if existing is not None:
        return existing

    if not force:
        last = (
            await db.execute(
                select(DocumentOperation)
                .where(
                    DocumentOperation.kind == KIND,
                    DocumentOperation.job_match_id == match_id,
                    DocumentOperation.state == "succeeded",
                    DocumentOperation.input_version == version,
                )
                .order_by(DocumentOperation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last.document_id is not None:
            doc = await db.get(TailoredDocument, last.document_id)
            try:
                validate_document(doc, match_id=match_id, doc_type="cv", data_dir=data_dir)
                return last
            except ArtifactError:
                pass  # the reusable output is gone; generate again

    op = DocumentOperation(kind=KIND, job_match_id=match_id, state="queued", input_version=version)
    db.add(op)
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent request inserted the active operation first.
        await db.rollback()
        winner = await active()
        if winner is None:
            raise
        return winner
    return op


def additional_context(profile: Any) -> str:
    """Profile facts handed to the CV modifier (shared with the batch runner)."""
    parts: list[str] = []
    if profile:
        if profile.driver_license:
            parts.append(f"Driver license: {profile.driver_license}")
        if profile.mobility:
            parts.append(f"Mobility / relocation: {profile.mobility}")
        if profile.additional_info and isinstance(profile.additional_info, dict):
            parts.extend(f"{k}: {v}" for k, v in profile.additional_info.items())
    return "\n".join(parts)


async def run_operation(op_id: int, *, pipeline: Any, session_factory: Any, data_dir: Path) -> None:
    """Claim a queued operation and run it to succeeded/failed."""
    async with session_factory() as db:
        claimed = await db.execute(
            update(DocumentOperation)
            .where(DocumentOperation.id == op_id, DocumentOperation.state == "queued")
            .values(state="running", attempts=DocumentOperation.attempts + 1)
        )
        await db.commit()
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            return  # someone else owns it, or it is already finished
        op = await db.get(DocumentOperation, op_id)
        assert op is not None
        try:
            _match, job, profile = await _load(db, op.job_match_id)
            source = resolve_base_source(
                profile.base_cv_path if profile else None, data_dir, "*.tex"
            )
            if source is None:
                raise ArtifactError("No base CV is configured.")
            jd = JobDetails(
                id=job.id,
                title=job.title,
                company=job.company,
                location=job.location or "",
                description=job.description or "",
                url=job.url or "",
                apply_url=job.apply_url or "",
                apply_method=job.apply_method or "",
                country=job.country or "",
                posted_at=job.posted_at,
            )
            slug = re.sub(r"[^\w]+", "_", (job.title or "job").lower()).strip("_")[:50]
            out_dir = data_dir / "cvs" / f"{op.job_match_id}_{slug}" / f"op{op.id}"
            # ponytail: uses the JobAnalyzer+CVModifier path; the stored fit
            # assessment is not rehydrated (FitAssessment has no from_dict yet).
            tailored = await pipeline.generate_tailored_cv(
                base_cv_path=source,
                job=jd,
                output_dir=out_dir,
                additional_context=additional_context(profile),
            )
            pdf = Path(tailored.pdf_path) if getattr(tailored, "pdf_path", None) else None
            digest = pdf_sha256(pdf)
            if pdf is None or digest is None:
                raise ArtifactError("Regeneration did not produce a valid PDF.")
            doc = TailoredDocument(
                job_match_id=op.job_match_id,
                doc_type="cv",
                tex_path=str(tailored.tex_path) if getattr(tailored, "tex_path", None) else None,
                pdf_path=str(pdf),
                pdf_sha256=digest,
                diff_json=diff_to_json(tailored),
            )
            db.add(doc)
            await db.flush()
            op.state, op.document_id, op.error = "succeeded", doc.id, None
            await db.commit()
            logger.info(
                "Regenerated CV for match_id=%d (operation=%d, doc=%d)",
                op.job_match_id,
                op.id,
                doc.id,
            )
        except asyncio.CancelledError:
            # Shutdown: leave it 'running'; resume_pending requeues it at startup.
            raise
        except Exception as exc:
            await db.rollback()
            op = await db.get(DocumentOperation, op_id)
            assert op is not None
            op.state, op.error = "failed", f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            await db.commit()
            logger.exception("CV regeneration failed (operation=%d)", op_id)


def schedule(app: Any, op_id: int) -> None:
    """Run *op_id* on an app-owned task (kept referenced until done)."""
    from backend.database import AsyncSessionLocal
    from backend.config import DATA_DIR
    from backend.defaults import CONCURRENCY_LLM

    state = app.state
    if getattr(state, "operation_tasks", None) is None:
        state.operation_tasks = set()
        state.operation_sem = asyncio.Semaphore(CONCURRENCY_LLM)

    async def _run() -> None:
        async with state.operation_sem:
            await run_operation(
                op_id,
                pipeline=state.cv_pipeline,
                session_factory=AsyncSessionLocal,
                data_dir=DATA_DIR,
            )

    task = asyncio.create_task(_run(), name=f"document-operation-{op_id}")
    state.operation_tasks.add(task)
    task.add_done_callback(state.operation_tasks.discard)


async def resume_pending(app: Any, session_factory: Any) -> int:
    """Startup recovery: interrupted 'running' work is requeued, then all queued work runs.

    Single process (ponytail: no worker leases; W15 adds them if a second
    worker process ever exists), so anything 'running' at startup is orphaned.
    """
    async with session_factory() as db:
        await db.execute(
            update(DocumentOperation)
            .where(DocumentOperation.state == "running")
            .values(state="queued")
        )
        await db.commit()
        ids = (
            (
                await db.execute(
                    select(DocumentOperation.id).where(DocumentOperation.state == "queued")
                )
            )
            .scalars()
            .all()
        )
    for op_id in ids:
        schedule(app, op_id)
    if ids:
        logger.info("Resumed %d interrupted document operation(s)", len(ids))
    return len(ids)


async def cancel_all(app: Any) -> None:
    tasks = list(getattr(app.state, "operation_tasks", None) or ())
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
