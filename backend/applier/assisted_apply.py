"""Assisted apply strategy — pre-fill form fields, stop before submit."""

from __future__ import annotations

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


class AssistedApplyStrategy:
    """Uses browser-use to pre-fill form fields, then stops.

    The browser window remains open and visible (headless=False) so the
    user can review the filled fields and click Submit themselves.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._api_key = api_key
        self._model = model

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

        try:
            llm = ChatGoogleGenerativeAI(
                model=self._model,
                google_api_key=self._api_key,
            )
            browser = Browser(headless=False)
            agent = Agent(task=task, llm=llm, browser=browser)
            await agent.run()
            logger.info("Assisted apply agent completed for %s", apply_url)
        except Exception as exc:
            logger.error("Assisted apply agent failed for %s: %s", apply_url, exc)

        return ApplicationResult(
            status="assisted",
            method="assisted",
            message=(
                "Form pre-filled. Please review the open browser window "
                "and submit manually when ready."
            ),
        )


__all__ = ["AssistedApplyStrategy"]
