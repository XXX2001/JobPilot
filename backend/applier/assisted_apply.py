"""Assisted apply strategy — Tier 1 form filler + browser-use fallback."""

from __future__ import annotations

import logging
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


class AssistedApplyStrategy:
    """Pre-fill the form and leave the browser open for user to review and submit.

    Tries Tier 1 (PlaywrightFormFiller.fill_only) first; falls back to the
    original browser-use agent on any exception.
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
        apply_url: str,
        full_name: str = "",
        email: str = "",
        phone: str = "",
        location: str = "",
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> ApplicationResult:
        apply_url = sanitize_url(apply_url)
        if not apply_url:
            return ApplicationResult(
                status="cancelled", method="assisted", message="Invalid apply URL"
            )

        # ── Tier 1: Playwright direct ────────────────────────────────────
        if settings.APPLY_TIER1_ENABLED and self._form_filler is not None:
            try:
                await self._form_filler.fill_only(
                    apply_url=apply_url,
                    full_name=full_name,
                    email=email,
                    phone=phone,
                    location=location,
                    cv_pdf=cv_pdf,
                    letter_pdf=letter_pdf,
                )
                return ApplicationResult(
                    status="assisted",
                    method="assisted",
                    message=(
                        "Form pre-filled. Please review the open browser window "
                        "and submit manually when ready."
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "[Tier 1] assisted fill failed for %s — falling back to browser-use: %s",
                    apply_url, exc,
                )

        # ── Tier 2: browser-use agent ────────────────────────────────────
        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser
            webbrowser.open(apply_url)
            return ApplicationResult(
                status="assisted",
                method="assisted",
                message=f"browser-use not installed. Opened {apply_url} manually.",
            )

        task = (
            f"Go to: {apply_url}\n"
            f"Fill in any form fields you can identify with:\n"
            f"  - Name: {full_name}\n"
            f"  - Email: {email}\n"
            f"  - Phone: {phone}\n"
            f"  - Location: {location}\n"
            f"Then STOP. Do NOT submit the form.\n"
            f"Report what fields you filled in."
        )

        browser = Browser(headless=False)
        try:
            llm = ChatGoogleGenerativeAI(
                model=self._model,
                google_api_key=self._api_key,
            )
            agent = Agent(task=task, llm=llm, browser=browser)
            await agent.run()
            logger.info("Assisted apply (Tier 2) completed for %s", apply_url)
        except Exception as exc:
            logger.error("Assisted apply agent failed for %s: %s", apply_url, exc)
            try:
                await browser.stop()
            except Exception:
                pass

        return ApplicationResult(
            status="assisted",
            method="assisted",
            message=(
                "Form pre-filled. Please review the open browser window "
                "and submit manually when ready."
            ),
        )


__all__ = ["AssistedApplyStrategy"]
