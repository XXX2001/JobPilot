"""Manual apply strategy — just open the URL in the user's browser."""

from __future__ import annotations

import logging
import shutil
import webbrowser
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApplicationResult(BaseModel):
    status: str  # "applied" | "assisted" | "manual" | "cancelled"
    method: str
    message: str = ""


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
            webbrowser.open(apply_url)
            logger.info("Opened URL in browser: %s", apply_url)
            message = f"Opened {apply_url} in your browser."
        except Exception as exc:
            logger.warning("webbrowser.open failed: %s", exc)
            message = f"Could not open browser automatically. Please visit: {apply_url}."

        if copied_files:
            message += f" Documents copied to ~/Downloads: {', '.join(copied_files)}"
        elif cv_pdf:
            message += f" Documents are in: {cv_pdf.parent}"

        return ApplicationResult(status="manual", method="manual", message=message)


__all__ = ["ManualApplyStrategy", "ApplicationResult"]
