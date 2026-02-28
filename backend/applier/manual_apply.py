"""Manual apply strategy — just open the URL in the user's browser."""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApplicationResult(BaseModel):
    status: str  # "applied" | "assisted" | "manual" | "cancelled"
    method: str
    message: str = ""


class ManualApplyStrategy:
    """Opens the job application URL in the default browser.

    No automation — the user completes the application themselves.
    CV / letter paths are surfaced in the result message so the user
    knows where the tailored documents live.
    """

    async def apply(
        self,
        apply_url: str,
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> ApplicationResult:
        docs_dir = str(cv_pdf.parent) if cv_pdf else "data/cvs"
        try:
            webbrowser.open(apply_url)
            logger.info("Opened URL in browser: %s", apply_url)
            message = (
                f"Opened {apply_url} in your browser. Your tailored documents are in: {docs_dir}"
            )
        except Exception as exc:
            logger.warning("webbrowser.open failed: %s", exc)
            message = (
                f"Could not open browser automatically. "
                f"Please visit: {apply_url}. "
                f"Documents: {docs_dir}"
            )

        return ApplicationResult(status="manual", method="manual", message=message)


__all__ = ["ManualApplyStrategy", "ApplicationResult"]
