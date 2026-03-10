"""Auto apply strategy — two-tier: Playwright direct (Tier 1) + browser-use fallback (Tier 2)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from backend.applier.manual_apply import ApplicationResult
from backend.config import settings
from backend.security.sanitizer import sanitize_url

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
    """Full auto-apply.

    Tries Tier 1 (PlaywrightFormFiller — direct Playwright + 1 Gemini call) first.
    Falls back to Tier 2 (browser-use agent loop) on any Tier 1 exception.
    ALWAYS pauses before submitting to emit an ``apply_review`` WS message
    and waits for the user to confirm via ``confirm_submit`` or cancel via
    ``cancel_apply``.
    """

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self._api_key = api_key
        self._model = model or settings.GOOGLE_MODEL

        try:
            from backend.llm.gemini_client import GeminiClient
            from backend.applier.form_filler import PlaywrightFormFiller
            self._form_filler = PlaywrightFormFiller(gemini_client=GeminiClient())
        except Exception as exc:
            logger.warning("Could not initialise PlaywrightFormFiller: %s — Tier 1 disabled", exc)
            self._form_filler = None  # type: ignore[assignment]

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
        """Run fill + review + submit. Tier 1 → Tier 2 fallback."""

        apply_url = sanitize_url(apply_url)
        if not apply_url:
            return ApplicationResult(
                status="cancelled", method="auto", message="Invalid apply URL"
            )

        # ── Tier 1: Playwright direct + single Gemini call ──────────────
        if settings.APPLY_TIER1_ENABLED and self._form_filler is not None:
            try:
                result = await self._form_filler.fill_and_submit(
                    apply_url=apply_url,
                    job_id=job_id,
                    full_name=full_name,
                    email=email,
                    phone=phone,
                    location=location,
                    additional_answers=additional_answers,
                    cv_pdf=cv_pdf,
                    letter_pdf=letter_pdf,
                    confirm_event=confirm_event,
                    cancel_event=cancel_event,
                )
                status = result.get("status", "cancelled")
                if status in ("applied", "cancelled"):
                    return ApplicationResult(
                        status=status,
                        method="auto",
                        message="Applied via Tier 1 (Playwright direct)"
                        if status == "applied"
                        else "Cancelled by user.",
                    )
            except Exception as exc:
                logger.warning(
                    "[Tier 1] apply failed for job_id=%d — falling back to browser-use: %s",
                    job_id,
                    exc,
                )

        # ── Tier 2: browser-use agent (original logic) ──────────────────
        return await self._browser_use_apply(
            job_id=job_id,
            apply_url=apply_url,
            full_name=full_name,
            email=email,
            phone=phone,
            location=location,
            additional_answers=additional_answers,
            cv_pdf=cv_pdf,
            letter_pdf=letter_pdf,
            confirm_event=confirm_event,
            cancel_event=cancel_event,
        )

    async def _browser_use_apply(
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
        """Tier 2: original browser-use agent loop."""

        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser
            webbrowser.open(apply_url)
            return ApplicationResult(
                status="manual",
                method="auto",
                message=f"browser-use not installed. Opened {apply_url} manually.",
            )

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
            # Parse JSON answers into readable key-value pairs for the agent
            try:
                parsed_answers = json.loads(additional_answers)
                if isinstance(parsed_answers, dict):
                    fill_task += "For additional questions use these answers:\n"
                    for k, v in parsed_answers.items():
                        fill_task += f"  {k}: {v}\n"
                else:
                    fill_task += f"For additional questions use: {additional_answers}\n"
            except Exception:
                fill_task += f"For additional questions use: {additional_answers}\n"
        fill_task += (
            "\nAfter filling everything, PAUSE and DO NOT click Submit.\n"
            "Report all fields you filled in as a JSON object."
        )

        filled_fields: dict[str, str] = {}
        screenshot_b64: str | None = None

        browser = Browser(headless=False)
        try:
            llm = ChatGoogleGenerativeAI(
                model=self._model,
                google_api_key=self._api_key,
            )
            agent = Agent(task=fill_task, llm=llm, browser=browser)
            result = await agent.run()

            raw = result.final_result() if hasattr(result, "final_result") else ""
            m = re.search(r"\{[^{}]+\}", raw or "", re.DOTALL)
            if m:
                try:
                    filled_fields = json.loads(m.group())
                except Exception:
                    pass

            if hasattr(result, "screenshot_base64"):
                ss = result.screenshot_base64
                if isinstance(ss, str) and len(ss) < 5_000_000:
                    screenshot_b64 = ss
                else:
                    logger.warning("Screenshot data invalid or too large, skipping")

        except Exception as exc:
            logger.error("Auto-apply fill phase failed for job_id=%d: %s", job_id, exc)
            try:
                await browser.stop()
            except Exception:
                pass
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message=f"Fill-out phase failed: {exc}",
            )

        # Broadcast review
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

        # Wait for user
        if confirm_event is None:
            confirm_event = asyncio.Event()
        if cancel_event is None:
            cancel_event = asyncio.Event()

        done, _ = await asyncio.wait(
            [
                asyncio.ensure_future(confirm_event.wait()),
                asyncio.ensure_future(cancel_event.wait()),
            ],
            timeout=1800,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Fix: always stop browser, even on timeout
        if not done:
            logger.warning("Auto-apply confirmation timed out for job_id=%d", job_id)
            try:
                await browser.stop()
            except Exception:
                pass
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message="Confirmation timed out after 30 minutes.",
            )

        if not confirm_event.is_set():
            logger.info("User cancelled auto-apply for job_id=%d", job_id)
            try:
                await browser.stop()
            except Exception:
                pass
            return ApplicationResult(
                status="cancelled", method="auto", message="Cancelled by user."
            )

        # Submit
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
        finally:
            try:
                await browser.stop()
            except Exception:
                pass


__all__ = ["AutoApplyStrategy"]
