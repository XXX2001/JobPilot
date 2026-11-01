"""Manual apply strategy — just open the URL in the user's browser."""

from __future__ import annotations

import logging
import shutil
import webbrowser
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: Canonical strategy-outcome vocabulary, mirrored as ``RESULT_*`` string
#: constants in ``backend.applier.__init__``. Defined here as the single
#: source of truth for the *typed* surface so call sites can be checked.
ResultStatus = Literal[
    "confirmed", "awaiting_user_submission", "needs_verification", "cancelled", "failed"
]
ResultMethod = Literal["auto", "assisted", "manual"]


class ApplicationResult(BaseModel):
    status: ResultStatus
    method: ResultMethod
    message: str = ""
    #: Destination evidence for ``confirmed`` / reason for ``needs_verification``.
    evidence: str = ""
    #: Where the user continues (the job's apply page) when they must act.
    next_action_url: str = ""


class ManualApplyStrategy:
    async def apply(
        self,
        apply_url: str,
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> ApplicationResult:
        downloads_dir = Path.home() / "Downloads"
        downloads_dir.mkdir(exist_ok=True)

        copied_files: list[str] = []
        for src, label in [(cv_pdf, "CV"), (letter_pdf, "Cover Letter")]:
            if src and src.exists():
                dest = downloads_dir / src.name
                # Avoid overwriting — add suffix if file exists
                if dest.exists():
                    dest = (
                        downloads_dir / f"{src.stem}_{label.lower().replace(' ', '_')}{src.suffix}"
                    )
                try:
                    shutil.copy2(src, dest)
                    copied_files.append(f"{label}: {dest.name}")
                    logger.info("Copied %s to %s", src, dest)
                except Exception as exc:
                    logger.warning("Failed to copy %s to Downloads: %s", src, exc)

        try:
            opened = webbrowser.open(apply_url)
        except Exception as exc:
            logger.warning("webbrowser.open failed: %s", exc)
            opened = False
        # webbrowser.open returns False when no browser could be launched
        # (e.g. a headless container) — say so instead of claiming it opened.
        message = (
            f"Opened {apply_url} in your browser."
            if opened
            else f"Could not open a browser automatically. Please visit: {apply_url}."
        )

        if copied_files:
            message += f" Documents copied to ~/Downloads: {', '.join(copied_files)}"
        elif cv_pdf:
            message += f" Documents are in: {cv_pdf.parent}"

        message += " Mark it as submitted in the tracker once you have applied."
        return ApplicationResult(
            status="awaiting_user_submission",
            method="manual",
            message=message,
            next_action_url=apply_url,
        )


__all__ = ["ManualApplyStrategy", "ApplicationResult"]
