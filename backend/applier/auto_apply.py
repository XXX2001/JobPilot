"""Auto apply strategy — two-tier: Playwright direct (Tier 1) + browser-use fallback (Tier 2)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from backend.applier._strategy_common import open_own_tab, restricted_tools, run_capped
from backend.applier._strategy_common import (
    PHONE_NUMBER_NOTE,
    build_browser,
    is_multi_step_site,
    site_profile_key,
)
from backend.applier.manual_apply import ApplicationResult
from backend.config import settings
from backend.defaults import (
    MAX_LEN_ADDITIONAL_ANSWERS,
    MAX_LEN_EMAIL,
    MAX_LEN_FULL_NAME,
    MAX_LEN_LOCATION,
    MAX_LEN_PHONE,
)
from backend.security.sanitizer import sanitize_for_prompt, sanitize_url

logger = logging.getLogger(__name__)

try:
    from browser_use import Agent, Browser  # type: ignore

    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False
    Agent = None  # type: ignore
    Browser = None  # type: ignore

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

    Tries Tier 1 (PlaywrightFormFiller — direct Playwright + 1 LLM call) first,
    but skips Tier 1 for multi-step sites (e.g. LinkedIn Easy Apply).
    Falls back to Tier 2 (browser-use agent loop) on any Tier 1 exception.
    ALWAYS pauses before submitting to emit an ``apply_review`` WS message
    and waits for the user to confirm via ``confirm_submit`` or cancel via
    ``cancel_apply``.
    """

    def __init__(
        self,
        on_review: Optional[Callable[..., Optional[dict]]] = None,
        on_get_patches: Optional[Callable[[int], dict]] = None,
    ) -> None:
        # Engine callback invoked at apply_review broadcast time so the
        # pending-review snapshot is cached for HTTP re-fetch.
        self._on_review = on_review
        # Engine accessor returning the user's field edits to re-fill before
        # submit (selector→value), threaded down to the Tier-1 form filler.
        self._on_get_patches = on_get_patches

        try:
            from backend.llm.factory import make_llm_client
            from backend.applier.form_filler import PlaywrightFormFiller
            self._form_filler = PlaywrightFormFiller(
                llm_client=make_llm_client(),
                on_review=on_review,
                on_get_patches=on_get_patches,
            )
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
        on_browser: Callable[[Any], None] | None = None,
    ) -> ApplicationResult:
        """Run fill + review + submit. Tier 1 → Tier 2 fallback.

        *on_browser* receives this run's live Tier-2 browser (per run, never
        strategy state: concurrent applies must not see each other's handle).
        """

        apply_url = sanitize_url(apply_url)
        if not apply_url:
            return ApplicationResult(
                status="cancelled", method="auto", message="Invalid apply URL"
            )

        # ── Tier 1: Playwright direct + single LLM call ─────────────────
        # Skip Tier 1 for multi-step sites (LinkedIn, etc.) — they need
        # browser-use agent to click through modals and multi-page forms.
        form_filler = self._form_filler
        use_tier1 = (
            settings.APPLY_TIER1_ENABLED
            and form_filler is not None
            and not is_multi_step_site(apply_url)
        )

        if use_tier1 and form_filler is not None:
            try:
                result = await form_filler.fill_and_submit(
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
                return _tier1_result(result)
            except Exception as exc:
                # Only raised BEFORE the submit click (see _submit_and_verify),
                # so retrying on Tier 2 cannot duplicate a submission.
                logger.warning(
                    "[Tier 1] apply failed for job_id=%d — falling back to browser-use: %s",
                    job_id,
                    exc,
                )

        # ── Tier 2: browser-use agent (LLM + Playwright) ────────────────
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
            on_browser=on_browser,
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

        # Sanitize every applicant field before it lands in the agent's task
        # prompt: strip control chars / injection-shaped lines so a crafted
        # profile value can't override the STOP-before-Submit instruction.
        lines.append(
            "\nAPPLICANT DETAILS (use these to fill the form):\n"
            f"  Full Name: {sanitize_for_prompt(full_name, MAX_LEN_FULL_NAME, 'full_name')}\n"
            f"  Email: {sanitize_for_prompt(email, MAX_LEN_EMAIL, 'email')}\n"
            f"  Phone: {sanitize_for_prompt(phone, MAX_LEN_PHONE, 'phone')}\n"
            f"  Location: {sanitize_for_prompt(location, MAX_LEN_LOCATION, 'location')}\n"
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
                        sk = sanitize_for_prompt(str(k), MAX_LEN_LOCATION, "answer_key")
                        sv = sanitize_for_prompt(str(v), MAX_LEN_ADDITIONAL_ANSWERS, "answer_value")
                        lines.append(f"  {sk}: {sv}\n")
            except Exception:
                lines.append(
                    "\nAdditional context: "
                    f"{sanitize_for_prompt(additional_answers, 500, 'additional_answers')}\n"
                )

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
        on_browser: Callable[[Any], None] | None = None,
    ) -> ApplicationResult:
        """Tier 2: browser-use agent with the LLM + persistent browser profile."""

        if not _BROWSER_USE_AVAILABLE or Agent is None:
            logger.warning("browser-use not available — falling back to manual open")
            import webbrowser
            webbrowser.open(apply_url)
            return ApplicationResult(
                status="awaiting_user_submission",
                method="auto",
                next_action_url=apply_url,
                message=f"browser-use not installed. Opened {apply_url}; submit it yourself.",
            )

        from backend.llm.factory import make_browser_llm

        try:
            llm = make_browser_llm()
        except ValueError as exc:
            logger.warning("[Tier 2] %s — Tier 2 disabled", exc)
            return ApplicationResult(
                status="awaiting_user_submission",
                method="auto",
                next_action_url=apply_url,
                message="Browser model unavailable. Open the application URL and complete it manually.",
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
            headless=settings.jobpilot_apply_headless,
            keep_alive=True,
            minimum_wait_page_load_time=3.0,
            wait_for_network_idle_page_load_time=15.0,
            disable_security=True,
        )
        if state_path.exists():
            logger.info("[Tier 2] Loading saved session from %s", state_path)
        else:
            logger.warning("[Tier 2] No saved session at %s — browser will not be logged in", state_path)

        browser = build_browser(browser_kwargs, state_path, apply_url=apply_url)
        if on_browser is not None:
            on_browser(browser)
        try:
            await open_own_tab(browser, apply_url)
            # Collect file paths the agent is allowed to upload
            file_paths = []
            if cv_pdf and cv_pdf.exists():
                file_paths.append(str(cv_pdf.resolve()))
            if letter_pdf and letter_pdf.exists():
                file_paths.append(str(letter_pdf.resolve()))

            agent = Agent(
                task=fill_task, llm=llm, browser=browser,
                available_file_paths=file_paths or None,
                # W08: no navigation, scripts or file writes while preparing.
                tools=restricted_tools(allow_upload=True),
                # Retry malformed action-JSON (common with local reasoning
                # models) on a fresh client instead of aborting the run.
                fallback_llm=make_browser_llm(),
            )
            logger.info("[Tier 2] Agent started — filling form for job_id=%d", job_id)
            result = await run_capped(agent)
            # Step cap (or the agent gave up): a half-filled form is not reviewed.
            if getattr(result, "is_done", lambda: True)() is False:
                raise RuntimeError(
                    f"browser agent did not finish within {settings.APPLY_TIER2_MAX_STEPS} steps"
                )

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
            # Review I3: the agent may have clicked submit before it crashed.
            early = await submitted_during_preparation(browser)
            if early is not None:
                return early
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message=f"Fill-out phase failed: {exc}",
            )

        # W08: the fill agent must not have submitted on its own.
        early = await submitted_during_preparation(browser)
        if early is not None:
            return early

        return await self._review_and_submit_tier2(
            browser,
            job_id=job_id,
            filled_fields=filled_fields,
            screenshot_b64=screenshot_b64,
            documents=[p for p in (cv_pdf, letter_pdf) if p and p.exists()],
            confirm_event=confirm_event or asyncio.Event(),
            cancel_event=cancel_event or asyncio.Event(),
        )

    async def _review_and_submit_tier2(
        self,
        browser,
        *,
        job_id: int,
        filled_fields: dict[str, str],
        screenshot_b64: str | None,
        documents: list[Path],
        confirm_event: asyncio.Event,
        cancel_event: asyncio.Event,
    ) -> ApplicationResult:
        """Tier-2 review rounds (W08): corrections are applied by a no-submit
        agent and re-reviewed; only an approval that changes nothing, on the
        same host with unchanged documents, reaches the submit agent."""
        from urllib.parse import urlsplit

        from backend.applier._strategy_common import restricted_tools
        from backend.applier.form_filler import _MAX_REVIEW_ROUNDS, PlaywrightFormFiller
        from backend.latex.artifacts import sha256_file
        from backend.llm.factory import make_browser_llm

        agent_cls = Agent
        if agent_cls is None:
            return ApplicationResult(status="failed", method="auto", message="browser-use is not installed.")
        doc_hashes = {p: sha256_file(p) for p in documents}
        fields = dict(filled_fields)
        note = ""
        for _round in range(_MAX_REVIEW_ROUNDS):
            reviewed_host = urlsplit(await _current_url(browser)).hostname
            confirm_event.clear()  # before publishing: an instant approval must not be erased
            snapshot = (
                self._on_review(job_id, filled_fields=fields, screenshot_b64=screenshot_b64)
                if self._on_review is not None
                else None
            ) or {}
            try:
                from backend.api.ws import manager as ws_manager  # type: ignore
                from backend.api.ws_models import ApplyReview  # type: ignore

                await ws_manager.broadcast(
                    ApplyReview(
                        type="apply_review",
                        job_id=job_id,
                        filled_fields=fields,
                        screenshot_base64=screenshot_b64,
                        review_id=snapshot.get("review_id"),
                        revision=snapshot.get("revision"),
                        note=note or None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not broadcast apply_review: %s", exc)

            try:
                approved = await PlaywrightFormFiller._wait_decision(confirm_event, cancel_event)
            except RuntimeError:
                return ApplicationResult(
                    status="cancelled", method="auto", message="Confirmation timed out after 30 minutes."
                )
            if not approved:
                return ApplicationResult(status="cancelled", method="auto", message="Cancelled by user.")

            patches = (self._on_get_patches(job_id) if self._on_get_patches else None) or {}
            if patches:
                task = (
                    "Change these form fields to exactly these values, then STOP. "
                    "Do NOT click Submit/Send/Apply and do not navigate away:\n"
                    + "\n".join(f"- field {sel!r} → {val!r}" for sel, val in patches.items())
                )
                try:
                    await run_capped(agent_cls(
                        task=task, llm=make_browser_llm(), browser=browser,
                        tools=restricted_tools(allow_upload=False), fallback_llm=make_browser_llm(),
                    ))
                except Exception as exc:  # noqa: BLE001 — the re-review shows the result
                    logger.warning("[Tier 2] correction agent failed for job_id=%d: %s", job_id, exc)
                early = await submitted_during_preparation(browser)
                if early is not None:
                    return early
                # ponytail: Tier 2 cannot read field values back; the new
                # screenshot is what the user re-approves.
                fields.update(patches)
                screenshot_b64 = await _screenshot_b64(browser) or screenshot_b64
                note = "Corrections were applied by the browser agent — check the screenshot."
                continue

            host = urlsplit(await _current_url(browser)).hostname
            if host != reviewed_host:
                note = f"The page moved to {host} — check it again."
                screenshot_b64 = await _screenshot_b64(browser) or screenshot_b64
                continue
            changed = [p.name for p, h in doc_hashes.items() if not p.exists() or sha256_file(p) != h]
            if changed:
                return ApplicationResult(
                    status="failed", method="auto",
                    evidence=f"attached document(s) changed after review: {', '.join(changed)}",
                    message="An attached document changed after review — nothing was submitted.",
                )
            # Baseline: only confirmation text that is new after the click counts.
            before_text = await _page_text(browser)
            try:
                submit_agent = agent_cls(
                    task=(
                        "The application form is already filled out and approved. Do not change any "
                        "field and do not upload files. Click the final Submit / Send application "
                        "button; if there is a 'Review' step, click through it to the final submit."
                    ),
                    llm=make_browser_llm(), browser=browser,
                    tools=restricted_tools(allow_upload=False), fallback_llm=make_browser_llm(),
                )
                history = await run_capped(submit_agent)
            except Exception as exc:
                logger.error("Submit phase failed for job_id=%d: %s", job_id, exc)
                return outcome_after_agent_error(exc)
            result = await outcome_after_agent(browser, history, before_text=before_text)
            logger.info("Auto-apply job_id=%d submit outcome=%s (%s)", job_id, result.status, result.evidence)
            return result
        return ApplicationResult(
            status="failed", method="auto",
            message=f"No stable approved state after {_MAX_REVIEW_ROUNDS} review rounds — nothing was submitted.",
        )


async def _current_url(browser) -> str:
    try:
        return await browser.get_current_page_url()
    except Exception:  # noqa: BLE001
        return ""


async def _page_text(browser) -> str | None:
    from backend.applier.verification import page_text

    try:
        page = await browser.get_current_page()
    except Exception:  # noqa: BLE001
        return None
    return await page_text(page) if page is not None else None


async def _screenshot_b64(browser) -> str | None:
    try:
        page = await browser.get_current_page()
        ss = await page.screenshot() if page else None
    except Exception:  # noqa: BLE001
        return None
    if isinstance(ss, bytes):
        import base64

        return base64.b64encode(ss).decode()
    return ss if isinstance(ss, str) and len(ss) < 5_000_000 else None


async def submitted_during_preparation(browser) -> ApplicationResult | None:
    """W08 safety net: the preparation agent can click anything, including
    submit. If the page already shows a confirmation, record it as a possible
    unapproved submission instead of continuing (and never submit again)."""
    from backend.applier.verification import verify_page

    try:
        page = await browser.get_current_page()
    except Exception:  # noqa: BLE001
        return None
    if page is None:
        return None
    check = await verify_page(page)
    if check.outcome != "confirmed":
        return None
    logger.error("Browser agent appears to have submitted before approval: %s", check.evidence)
    return ApplicationResult(
        status="needs_verification", method="auto", evidence=check.evidence,
        message="The browser agent appears to have submitted before your approval — check the site.",
    )


def _tier1_result(result: dict) -> ApplicationResult:
    """Map a Tier-1 outcome. Every post-click status is final for this attempt.

    ``rejected`` only ever means "nothing was clicked" (see
    ``PlaywrightFormFiller._review_and_submit``); a post-click validation
    failure arrives as ``needs_verification`` and keeps its slot.
    """
    status = result.get("status", "cancelled")
    evidence = result.get("evidence", "")
    if status == "confirmed":
        return ApplicationResult(
            status="confirmed", method="auto", evidence=evidence,
            message="Submitted via Tier 1 and confirmed by the site.",
        )
    if status == "rejected":
        return ApplicationResult(
            status="failed", method="auto", evidence=evidence,
            message=f"The site did not accept the submission: {evidence}",
        )
    if status == "needs_verification":
        return ApplicationResult(
            status="needs_verification", method="auto", evidence=evidence,
            message="Submit was clicked but the site showed no confirmation — check it before retrying.",
        )
    return ApplicationResult(status="cancelled", method="auto", message="Cancelled by user.")


async def outcome_after_agent(
    browser, history, *, before_text: str | None
) -> ApplicationResult:
    """Tier 2: agent completion is a hint; only the destination page confirms (W06).

    *before_text* is the page text read before the submit agent ran: only
    confirmation text that is new counts, and without a baseline nothing is
    confirmed. No outcome here is ``failed``: the click may have happened, so
    the attempt keeps its slot and the job cannot be submitted again.
    """
    from backend.applier.verification import verify_page

    try:
        page = await browser.get_current_page()
    except Exception:  # noqa: BLE001
        page = None
    check = await verify_page(page, before_text=before_text) if page is not None else None
    if check is not None and check.outcome == "confirmed" and before_text is not None:
        return ApplicationResult(
            status="confirmed", method="auto", evidence=check.evidence,
            message="Submitted via the browser agent and confirmed by the site.",
        )
    agent_note = ""
    try:
        agent_note = f"agent success={history.is_successful()}, result={history.final_result()!r}"
    except Exception:  # noqa: BLE001
        pass
    page_note = "page unavailable"
    if check is not None:
        no_baseline = "no pre-submit baseline; " if check.outcome == "confirmed" else ""
        page_note = no_baseline + check.evidence
    return ApplicationResult(
        status="needs_verification",
        method="auto",
        evidence="; ".join(p for p in (page_note, agent_note) if p)[:300],
        message="The agent finished but the site showed no new confirmation — check it first.",
    )


def outcome_after_agent_error(exc: Exception) -> ApplicationResult:
    """The submit agent raised mid-way: it may already have clicked submit."""
    return ApplicationResult(
        status="needs_verification", method="auto", evidence=f"submit agent error: {exc}"[:300],
        message="Submission was interrupted and may have reached the site — check it before retrying.",
    )


__all__ = ["AutoApplyStrategy"]
