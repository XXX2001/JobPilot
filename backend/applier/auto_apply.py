"""Auto apply strategy — full automation with mandatory user confirmation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.applier.manual_apply import ApplicationResult

logger = logging.getLogger(__name__)

try:
    from browser_use import Agent, Browser  # type: ignore
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore

    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False
    Agent = None  # type: ignore
    Browser = None  # type: ignore
    ChatGoogleGenerativeAI = None  # type: ignore


class AutoApplyStrategy:
    """Full auto-apply via browser-use.

    ALWAYS pauses before submitting to emit an ``apply_review`` WS message
    and waits for the user to confirm via ``confirm_submit`` or cancel via
    ``cancel_apply``.  The :class:`~backend.applier.engine.ApplicationEngine`
    sets/creates the matching :class:`asyncio.Event` and passes it here.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._api_key = api_key
        self._model = model

    async def apply(
        self,
        job_id: int,
        apply_url: str,
        full_name: str = "",
        email: str = "",
        phone: str = "",
        location: str = "",
        additional_answers: str = "",
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
        confirm_event: asyncio.Event | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ApplicationResult:
        """Run the fill-out phase, broadcast for review, then submit or cancel."""

        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser

            webbrowser.open(apply_url)
            return ApplicationResult(
                status="manual",
                method="auto",
                message=f"browser-use not installed. Opened {apply_url} manually.",
            )

        # ── Phase 1: Fill out form (do NOT submit yet) ──────────────────
        fill_task = (
            f"Go to: {apply_url}\n"
            f"Fill out the job application form:\n"
            f"  - Name: {full_name}\n"
            f"  - Email: {email}\n"
            f"  - Phone: {phone}\n"
            f"  - Location: {location}\n"
        )
        if cv_pdf and cv_pdf.exists():
            fill_task += f"Upload the CV/resume file: {cv_pdf}\n"
        if letter_pdf and letter_pdf.exists():
            fill_task += f"Upload the cover letter file: {letter_pdf}\n"
        if additional_answers:
            fill_task += f"For additional questions use: {additional_answers}\n"
        fill_task += (
            "\nAfter filling everything, PAUSE and DO NOT click Submit.\n"
            "Report all fields you filled in as a JSON object."
        )

        filled_fields: dict[str, str] = {}
        screenshot_b64: str | None = None

        try:
            llm = ChatGoogleGenerativeAI(
                model=self._model,
                google_api_key=self._api_key,
            )
            browser = Browser(headless=False)
            agent = Agent(task=fill_task, llm=llm, browser=browser)
            result = await agent.run()

            raw = result.final_result() if hasattr(result, "final_result") else ""
            import json
            import re

            m = re.search(r"\{[^{}]+\}", raw or "", re.DOTALL)
            if m:
                try:
                    filled_fields = json.loads(m.group())
                except Exception:
                    pass

            # Grab screenshot if available
            if hasattr(result, "screenshot_base64"):
                screenshot_b64 = result.screenshot_base64

        except Exception as exc:
            logger.error("Auto-apply fill phase failed for job_id=%d: %s", job_id, exc)
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message=f"Fill-out phase failed: {exc}",
            )

        # ── Phase 2: Broadcast review and wait for user decision ─────────
        try:
            from backend.api.ws import manager as ws_manager  # type: ignore
            from backend.api.ws_models import ApplyReview  # type: ignore

            await ws_manager.broadcast(
                ApplyReview(
                    type="apply_review",
                    job_id=job_id,
                    filled_fields=filled_fields,
                    screenshot_base64=screenshot_b64,
                )
            )
        except Exception as exc:
            logger.warning("Could not broadcast apply_review: %s", exc)

        # Wait for user to confirm or cancel (30 minute window)
        confirmed = False
        if confirm_event is None:
            confirm_event = asyncio.Event()
        if cancel_event is None:
            cancel_event = asyncio.Event()

        done, _ = await asyncio.wait(
            [
                asyncio.ensure_future(confirm_event.wait()),
                asyncio.ensure_future(cancel_event.wait()),
            ],
            timeout=1800,  # 30 minutes
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            logger.warning("Auto-apply confirmation timed out for job_id=%d", job_id)
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message="Confirmation timed out after 30 minutes.",
            )

        confirmed = confirm_event.is_set()

        if not confirmed:
            logger.info("User cancelled auto-apply for job_id=%d", job_id)
            return ApplicationResult(
                status="cancelled", method="auto", message="Cancelled by user."
            )

        # ── Phase 3: Submit ───────────────────────────────────────────────
        try:
            submit_task = (
                "The form is already filled out. "
                "Click the Submit / Apply / Send Application button to submit."
            )
            llm2 = ChatGoogleGenerativeAI(
                model=self._model,
                google_api_key=self._api_key,
            )
            submit_agent = Agent(task=submit_task, llm=llm2, browser=browser)
            await submit_agent.run()
            logger.info("Auto-apply submitted for job_id=%d", job_id)
            return ApplicationResult(status="applied", method="auto")
        except Exception as exc:
            logger.error("Submit phase failed for job_id=%d: %s", job_id, exc)
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message=f"Submit phase failed: {exc}",
            )


__all__ = ["AutoApplyStrategy"]
