"""Document artifacts: one path resolver and one selection validator (W04).

Invariant: the document the user selected is the document that is uploaded.
A selection is either an explicit ``TailoredDocument`` (validated against its
match, type, file and recorded content hash) or the base CV, compiled from its
LaTeX source into a content-addressed directory. Nothing here falls back to a
different document than the one requested, and a ``.tex`` source never leaves
as an upload.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"

CompileBaseCV = Callable[[Path, Path], Awaitable[Path]]


class ArtifactError(Exception):
    """A selected document cannot be used; the message is user-facing."""


def resolve_data_path(raw: Optional[str], data_dir: Path) -> Optional[Path]:
    """Resolve a stored path (absolute, or relative to *data_dir*) to an existing file."""
    if not raw:
        return None
    p = Path(raw)
    candidate = p if p.is_absolute() else data_dir / p
    return candidate if candidate.is_file() else None


def resolve_base_source(raw: Optional[str], data_dir: Path, pattern: str) -> Optional[Path]:
    """Return the base LaTeX source: the profile path if it exists, else the
    alphabetically first ``templates/<pattern>`` (deterministic)."""
    found = resolve_data_path(raw, data_dir)
    if found is not None:
        return found
    templates = data_dir / "templates"
    candidates = sorted(templates.glob(pattern)) if templates.is_dir() else []
    if candidates:
        logger.warning("No usable base path in profile — using auto-detected %s", candidates[0])
        return candidates[0]
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_sha256(path: Optional[Path]) -> Optional[str]:
    """Hash of a real PDF file, or ``None`` if *path* is missing or not a PDF."""
    if path is None or path.suffix.lower() != ".pdf" or not path.is_file():
        return None
    with path.open("rb") as f:
        if f.read(len(PDF_MAGIC)) != PDF_MAGIC:
            return None
    return sha256_file(path)


def validate_document(doc, *, match_id: int, doc_type: str, data_dir: Path) -> Path:
    """Return the PDF path of a selected ``TailoredDocument`` or raise ArtifactError."""
    label = "CV" if doc_type == "cv" else "cover letter"
    if doc is None:
        raise ArtifactError(f"The selected {label} does not exist.")
    if doc.job_match_id != match_id:
        raise ArtifactError(f"The selected {label} belongs to a different job.")
    if doc.doc_type != doc_type:
        raise ArtifactError(f"Document {doc.id} is not a {label}.")
    path = resolve_data_path(doc.pdf_path, data_dir)
    actual = pdf_sha256(path)
    if path is None or actual is None:
        raise ArtifactError(f"The selected {label} PDF is missing or invalid; regenerate it.")
    if doc.pdf_sha256 is None:
        # ponytail: rows predating hashes and missed by the backfill are pinned
        # on first use; a later change then fails the comparison below.
        doc.pdf_sha256 = actual
    elif doc.pdf_sha256 != actual:
        raise ArtifactError(
            f"The selected {label} changed after it was generated; review it again."
        )
    return path


async def ensure_base_cv_pdf(source: Path, data_dir: Path, compile_base_cv: CompileBaseCV) -> Path:
    """Compile the base CV source into ``cvs/base/<source-hash>/`` (reused when unchanged).

    The base CV is job-independent, so its identity is its source content. A
    compile goes to a staging directory that is renamed into place only after
    a valid PDF exists; a failure never leaves a half-written published PDF.
    """
    if source.suffix.lower() != ".tex":
        raise ArtifactError(f"Base CV source must be a .tex file, got {source.name}.")
    digest = sha256_file(source)[:16]
    final_dir = data_dir / "cvs" / "base" / digest
    published = final_dir / "cv.pdf"
    if pdf_sha256(published) is not None:
        return published

    staging = final_dir.with_name(f".{digest}.{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        pdf = await compile_base_cv(source, staging)
        if pdf_sha256(pdf) is None:
            raise ArtifactError("The base CV did not compile to a valid PDF.")
        if pdf.name != "cv.pdf":
            pdf.rename(staging / "cv.pdf")
        try:
            os.replace(staging, final_dir)
        except OSError:
            # ponytail: a concurrent compile published first; identical source → identical artifact.
            if pdf_sha256(published) is None:
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return published


def diff_to_json(tailored) -> list | None:
    """Serialize a generation result's diff entries for ``TailoredDocument.diff_json``."""
    diff = getattr(tailored, "diff", None)
    if not diff:
        return None
    try:
        return [d.__dict__ if hasattr(d, "__dict__") else str(d) for d in diff]
    except Exception:
        logger.warning("Could not serialize document diff", exc_info=True)
        return None
