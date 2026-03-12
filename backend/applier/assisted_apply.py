"""Assisted apply strategy — Tier 1 form filler + browser-use fallback."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from backend.applier.manual_apply import ApplicationResult
from backend.config import settings
from backend.security.sanitizer import sanitize_url

logger = logging.getLogger(__name__)

try:
    from browser_use import Agent, Browser  # type: ignore
    from browser_use.llm.google import ChatGoogle  # type: ignore

    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False
    Agent = None  # type: ignore
    Browser = None  # type: ignore
    ChatGoogle = None  # type: ignore

# Sites that require clicking "Apply" / "Easy Apply" before the form appears
_MULTI_STEP_DOMAINS = {"linkedin.com", "www.linkedin.com"}


def _is_multi_step_site(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname.lstrip("www.") in {h.lstrip("www.") for h in _MULTI_STEP_DOMAINS}


def _site_key(url: str) -> str:
    """Extract a site key matching the scraping session convention (e.g. 'linkedin')."""
    hostname = urlparse(url).hostname or "unknown"
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname.split(".")[0]


class AssistedApplyStrategy:
    """Pre-fill the form and leave the browser open for user to review and submit.

    Tries Tier 1 (PlaywrightFormFiller.fill_only) first for simple sites;
    skips to Tier 2 (browser-use + Gemini) for multi-step sites like LinkedIn.
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
        additional_answers: str = "",
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> ApplicationResult:
        apply_url = sanitize_url(apply_url)
        if not apply_url:
            return ApplicationResult(
                status="cancelled", method="assisted", message="Invalid apply URL"
            )

        # ── Tier 1: Playwright direct (skip for multi-step sites) ─────────
        use_tier1 = (
            settings.APPLY_TIER1_ENABLED
            and self._form_filler is not None
            and not _is_multi_step_site(apply_url)
        )

        if use_tier1:
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

        # ── Tier 2: browser-use agent (Gemini + Playwright) ───────────────
        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser
            webbrowser.open(apply_url)
            return ApplicationResult(
                status="assisted",
                method="assisted",
                message=f"browser-use not installed. Opened {apply_url} manually.",
            )

        # Build a detailed task prompt
        task = self._build_fill_task(
            apply_url=apply_url,
            full_name=full_name,
            email=email,
            phone=phone,
            location=location,
            additional_answers=additional_answers,
            cv_pdf=cv_pdf,
            letter_pdf=letter_pdf,
        )

        # Reuse saved session (cookies/auth) from scraping
        site_key = _site_key(apply_url)
        profiles_dir = Path(settings.jobpilot_data_dir) / "browser_profiles"
        state_path = profiles_dir / site_key / "state.json"

        logger.info(
            "[Tier 2 assisted] Starting browser-use for %s state=%s",
            apply_url, state_path,
        )

        browser_kwargs: dict = dict(
            headless=False,
            keep_alive=True,
            minimum_wait_page_load_time=3.0,
            wait_for_network_idle_page_load_time=15.0,
            disable_security=True,
        )
        if state_path.exists():
            browser_kwargs["storage_state"] = state_path.resolve().as_posix()
            browser_kwargs["user_data_dir"] = None
            logger.info("[Tier 2 assisted] Loading saved session from %s", state_path)
        else:
            logger.warning("[Tier 2 assisted] No saved session — browser will not be logged in")

        browser = Browser(**browser_kwargs)
        try:
            llm = ChatGoogle(
                model=self._model,
                api_key=self._api_key,
            )
            file_paths = []
            if cv_pdf and cv_pdf.exists():
                file_paths.append(str(cv_pdf.resolve()))
            if letter_pdf and letter_pdf.exists():
                file_paths.append(str(letter_pdf.resolve()))

            agent = Agent(
                task=task, llm=llm, browser=browser,
                available_file_paths=file_paths or None,
            )
            result = await agent.run()
            logger.info("[Tier 2 assisted] Agent completed for %s", apply_url)
        except Exception as exc:
            logger.error("Assisted apply agent failed for %s: %s", apply_url, exc)
            try:
                await browser.stop()
            except Exception:
                pass

        # Browser stays open — user reviews and submits manually
        return ApplicationResult(
            status="assisted",
            method="assisted",
            message=(
                "Form pre-filled via Gemini agent. Please review the open browser "
                "window and submit manually when ready."
            ),
        )

    def _build_fill_task(
        self,
        apply_url: str,
        full_name: str,
        email: str,
        phone: str,
        location: str,
        additional_answers: str,
        cv_pdf: Path | None,
        letter_pdf: Path | None,
    ) -> str:
        """Build the browser-use agent task prompt."""
        is_linkedin = "linkedin.com" in apply_url.lower()

        lines: list[str] = []

        if is_linkedin:
            lines.append(
                "You are on a LinkedIn job page. Your task is to fill the application form.\n\n"
                "STEPS:\n"
                "1. Navigate to the job page URL below.\n"
                "2. Look for an 'Easy Apply' or 'Apply' button and click it.\n"
                "   - If you see a login page, report that login is required and stop.\n"
                "3. A modal/dialog will open with an application form.\n"
                "4. Fill in ALL form fields using the applicant details below.\n"
                "5. If there are multiple pages/steps, click 'Next' to proceed, "
                "filling fields as you go.\n"
                "6. Upload the CV/resume file when you see a file upload field.\n"
                "7. STOP before the final 'Submit application' button. Do NOT submit.\n"
            )
        else:
            lines.append(
                "You are applying for a job. Fill out the application form.\n\n"
                "STEPS:\n"
                "1. Navigate to the job page URL below.\n"
                "2. If there is an 'Apply' button, click it to open the form.\n"
                "3. Fill in ALL form fields using the applicant details below.\n"
                "4. Upload the CV/resume file when you see a file upload field.\n"
                "5. STOP before clicking Submit. Do NOT submit.\n"
            )

        lines.append(f"\nURL: {apply_url}\n")
        lines.append(
            f"\nAPPLICANT DETAILS:\n"
            f"  Full Name: {full_name}\n"
            f"  Email: {email}\n"
            f"  Phone: {phone}\n"
            f"  Location: {location}\n"
            "\n  NOTE on phone number: Some websites auto-fill the country code prefix "
            "(e.g. +33 for France). If you see the country code is already pre-filled "
            "in the phone field, enter ONLY the local part without the country code "
            "to avoid duplication like '+33+33612345678'.\n"
        )

        if cv_pdf and cv_pdf.exists():
            lines.append(f"\nCV/Resume file to upload: {cv_pdf.resolve()}\n")
        if letter_pdf and letter_pdf.exists():
            lines.append(f"Cover letter file to upload: {letter_pdf.resolve()}\n")

        if additional_answers:
            try:
                parsed = json.loads(additional_answers)
                if isinstance(parsed, dict) and parsed:
                    lines.append("\nAdditional information for form questions:\n")
                    for k, v in parsed.items():
                        lines.append(f"  {k}: {v}\n")
            except Exception:
                pass

        lines.append(
            "\nIMPORTANT: Do NOT click Submit. Stop after filling all fields.\n"
        )

        return "".join(lines)


__all__ = ["AssistedApplyStrategy"]
