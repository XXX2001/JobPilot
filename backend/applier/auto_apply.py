"""Auto apply strategy — two-tier: Playwright direct (Tier 1) + browser-use fallback (Tier 2)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from backend.applier._strategy_common import (
    PHONE_NUMBER_NOTE,
    build_browser,
    is_multi_step_site,
    site_profile_key,
)
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

# T4a: ``_site_key`` removed in favour of the canonical
# :func:`backend.applier.captcha_handler.site_profile_key` (re-exported via
# ``_strategy_common`` and imported at the top of the file). The old helper
# returned just the first label ("linkedin") while ``captcha_handler`` saved
# sessions under the full underscore form ("linkedin_com"), so Tier 2 looked
# in the wrong directory and never picked up the saved login.
#
# M1-T6: ``_is_multi_step_site`` and the ``_MULTI_STEP_DOMAINS`` set were
# moved to ``_strategy_common.is_multi_step_site`` (identical copy shared
# with ``assisted_apply``).


class AutoApplyStrategy:
    """Full auto-apply.

    Tries Tier 1 (PlaywrightFormFiller — direct Playwright + 1 Gemini call) first,
    but skips Tier 1 for multi-step sites (e.g. LinkedIn Easy Apply).
    Falls back to Tier 2 (browser-use agent loop) on any Tier 1 exception.
    ALWAYS pauses before submitting to emit an ``apply_review`` WS message
    and waits for the user to confirm via ``confirm_submit`` or cancel via
    ``cancel_apply``.
    """

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        on_review: Optional[Callable[..., None]] = None,
        on_get_patches: Optional[Callable[[int], dict]] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or settings.GOOGLE_MODEL
        # Engine callback invoked at apply_review broadcast time so the
        # pending-review snapshot is cached for HTTP re-fetch.
        self._on_review = on_review
        # Engine accessor returning the user's field edits to re-fill before
        # submit (selector→value), threaded down to the Tier-1 form filler.
        self._on_get_patches = on_get_patches

        try:
            from backend.llm.gemini_client import GeminiClient
            from backend.applier.form_filler import PlaywrightFormFiller
            self._form_filler = PlaywrightFormFiller(
                gemini_client=GeminiClient(),
                on_review=on_review,
                on_get_patches=on_get_patches,
            )
        except Exception as exc:
            logger.warning("Could not initialise PlaywrightFormFiller: %s — Tier 1 disabled", exc)
            self._form_filler = None  # type: ignore[assignment]

        # Live Tier-2 browser for the current apply() call, surfaced to the
        # engine so the FSM owns failure-path cleanup. Reset per apply().
        self._active_browser = None

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

        self._active_browser = None

        apply_url = sanitize_url(apply_url)
        if not apply_url:
            return ApplicationResult(
                status="cancelled", method="auto", message="Invalid apply URL"
            )

        # ── Tier 1: Playwright direct + single Gemini call ──────────────
        # Skip Tier 1 for multi-step sites (LinkedIn, etc.) — they need
        # browser-use agent to click through modals and multi-page forms.
        use_tier1 = (
            settings.APPLY_TIER1_ENABLED
            and self._form_filler is not None
            and not is_multi_step_site(apply_url)
        )

        if use_tier1:
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

        # ── Tier 2: browser-use agent (Gemini + Playwright) ─────────────
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
        """Build the browser-use agent task prompt for filling application forms."""

        is_linkedin = "linkedin.com" in apply_url.lower()

        lines: list[str] = []

        # Site-specific instructions
        if is_linkedin:
            lines.append(
                "You are on a LinkedIn job page. Your task is to apply for this job.\n\n"
                "STEPS:\n"
                "1. Navigate to the job page URL below.\n"
                "2. Look for an 'Easy Apply' or 'Apply' button on the job listing and click it.\n"
                "   - If you see a login page, log in using the saved session. If not logged in, "
                "report that login is required.\n"
                "3. A modal/dialog will open with an application form.\n"
                "4. Fill in ALL form fields in the modal using the applicant details below.\n"
                "5. If there are multiple pages/steps in the form, click 'Next' to proceed "
                "through each page, filling fields as you go.\n"
                "6. Upload the CV/resume file when you see a file upload field.\n"
                "7. STOP before the final 'Submit application' / 'Review' button. "
                "Do NOT click Submit.\n"
            )
        else:
            lines.append(
                "You are applying for a job. Your task is to fill out the application form.\n\n"
                "STEPS:\n"
                "1. Navigate to the job page URL below.\n"
                "2. If there is an 'Apply' or 'Apply Now' button, click it to open the form.\n"
                "3. Fill in ALL form fields using the applicant details below.\n"
                "4. Upload the CV/resume file when you see a file upload field.\n"
                "5. STOP before clicking the final 'Submit' / 'Send Application' button. "
                "Do NOT submit.\n"
            )

        lines.append(f"\nURL: {apply_url}\n")

        lines.append(
            "\nAPPLICANT DETAILS (use these to fill the form):\n"
            f"  Full Name: {full_name}\n"
            f"  Email: {email}\n"
            f"  Phone: {phone}\n"
            f"  Location: {location}\n"
            + PHONE_NUMBER_NOTE
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
                lines.append(f"\nAdditional context: {additional_answers[:500]}\n")

        lines.append(
            "\nIMPORTANT RULES:\n"
            "- Do NOT click the final Submit/Send/Apply button.\n"
            "- If a field asks for something not in the applicant details, "
            "use your best judgment or leave it blank.\n"
            "- If you encounter a CAPTCHA, wait for the user to solve it.\n"
            "- After filling all fields and uploading files, STOP and report "
            "all the fields you filled as a JSON object like: "
            '{\"field_name\": \"value_filled\", ...}\n'
        )

        return "".join(lines)

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
        """Tier 2: browser-use agent with Gemini + persistent browser profile."""

        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser
            webbrowser.open(apply_url)
            return ApplicationResult(
                status="manual",
                method="auto",
                message=f"browser-use not installed. Opened {apply_url} manually.",
            )

        # ── Reuse saved session (cookies/auth) from scraping ──────────────
        # T4a: use the canonical profile key — same as captcha_handler /
        # form_filler — so the storage_state file we read here is the
        # one preflight wrote.
        site_key = site_profile_key(apply_url)
        profiles_dir = Path(settings.jobpilot_data_dir) / "browser_profiles"
        state_path = profiles_dir / site_key / "state.json"

        logger.info(
            "[Tier 2] Starting browser-use auto-apply for job_id=%d url=%s state=%s",
            job_id, apply_url, state_path,
        )

        fill_task = self._build_fill_task(
            apply_url=apply_url,
            full_name=full_name,
            email=email,
            phone=phone,
            location=location,
            additional_answers=additional_answers,
            cv_pdf=cv_pdf,
            letter_pdf=letter_pdf,
        )

        filled_fields: dict[str, str] = {}
        screenshot_b64: str | None = None

        browser_kwargs: dict = dict(
            headless=False,
            keep_alive=True,
            minimum_wait_page_load_time=3.0,
            wait_for_network_idle_page_load_time=15.0,
            disable_security=True,
        )
        if state_path.exists():
            logger.info("[Tier 2] Loading saved session from %s", state_path)
        else:
            logger.warning("[Tier 2] No saved session at %s — browser will not be logged in", state_path)

        browser = build_browser(browser_kwargs, state_path)
        self._active_browser = browser
        try:
            llm = ChatGoogle(
                model=self._model,
                api_key=self._api_key,
            )
            # Collect file paths the agent is allowed to upload
            file_paths = []
            if cv_pdf and cv_pdf.exists():
                file_paths.append(str(cv_pdf.resolve()))
            if letter_pdf and letter_pdf.exists():
                file_paths.append(str(letter_pdf.resolve()))

            agent = Agent(
                task=fill_task, llm=llm, browser=browser,
                available_file_paths=file_paths or None,
            )
            logger.info("[Tier 2] Agent started — filling form for job_id=%d", job_id)
            result = await agent.run()

            raw = result.final_result() if hasattr(result, "final_result") else ""
            logger.info("[Tier 2] Agent result for job_id=%d: %s", job_id, (raw or "")[:500])

            # Try to extract filled fields JSON from agent output
            m = re.search(r"\{[^{}]+\}", raw or "", re.DOTALL)
            if m:
                try:
                    filled_fields = json.loads(m.group())
                except Exception:
                    pass

            # Get screenshot from browser (browser-use page.screenshot() returns base64 str)
            try:
                page = await browser.get_current_page()
                if page:
                    ss = await page.screenshot()
                    if isinstance(ss, str) and len(ss) < 5_000_000:
                        screenshot_b64 = ss
                    elif isinstance(ss, bytes):
                        import base64
                        screenshot_b64 = base64.b64encode(ss).decode()
            except Exception as ss_exc:
                logger.debug("[Tier 2] Screenshot failed: %s", ss_exc)

        except Exception as exc:
            logger.error("Auto-apply fill phase failed for job_id=%d: %s", job_id, exc, exc_info=True)
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
            logger.info("[Tier 2] Broadcast apply_review for job_id=%d — waiting for confirmation", job_id)
        except Exception as exc:
            logger.warning("Could not broadcast apply_review: %s", exc)

        # Cache the same snapshot the WS broadcast just sent so a
        # reconnecting client can re-fetch it over HTTP.
        if self._on_review is not None:
            self._on_review(
                job_id,
                filled_fields=filled_fields,
                screenshot_b64=screenshot_b64,
            )

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

        if not done:
            logger.warning("Auto-apply confirmation timed out for job_id=%d", job_id)
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message="Confirmation timed out after 30 minutes.",
            )

        if not confirm_event.is_set():
            logger.info("User cancelled auto-apply for job_id=%d", job_id)
            return ApplicationResult(
                status="cancelled", method="auto", message="Cancelled by user."
            )

        # Submit
        try:
            submit_task = (
                "The application form is already filled out. "
                "Click the final Submit / Send Application / Submit application button "
                "to complete the application. If there is a 'Review' step, click through "
                "it and then click the final submit button."
            )
            llm2 = ChatGoogle(
                model=self._model,
                api_key=self._api_key,
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
