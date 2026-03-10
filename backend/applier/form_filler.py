"""Tier 1 apply: Playwright direct form filler + single Gemini call.

Architecture mirrors ScraplingFetcher (scraping Tier 1):
  1. preflight_check_url()  — CAPTCHA detection (reuses captcha_handler)
  2. launch_persistent_context() — load saved browser profile (cookies/auth)
  3. page.goto(apply_url)
  4. _clean_form_html()     — strip page to form skeleton
  5. _build_fill_prompt()   — build single Gemini prompt
  6. Gemini call            — returns JSON field mapping
  7. page.fill() / page.set_input_files()
  8. broadcast apply_review WS
  9. wait confirm/cancel
  10. page.click(submit_selector)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from backend.config import settings

if TYPE_CHECKING:
    from backend.llm.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

_MAX_FORM_CHARS = 15_000

# Tags to strip — keep form skeleton only
_NOISE_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"}
# Attributes to keep on form elements
_KEEP_ATTRS = {"id", "name", "type", "placeholder", "required", "for", "class", "action", "method"}


class PlaywrightFormFiller:
    """Tier 1 apply: direct Playwright DOM manipulation + single Gemini call.

    Raises on any unrecoverable error so the caller can fall back to Tier 2.
    """

    def __init__(self, gemini_client: "GeminiClient") -> None:
        self._gemini = gemini_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fill_and_submit(
        self,
        apply_url: str,
        job_id: int,
        full_name: str = "",
        email: str = "",
        phone: str = "",
        location: str = "",
        additional_answers: str = "",
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
        confirm_event: asyncio.Event | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """Fill form, wait for user review, then submit.

        Returns a dict with keys: status, filled_fields, screenshot_b64.
        Raises on failure so AutoApplyStrategy can fall back to Tier 2.
        """
        from playwright.async_api import async_playwright

        from backend.applier.captcha_handler import (
            get_session_path,
            preflight_check_url,
            _domain_key,
        )

        # Phase 1: preflight CAPTCHA check
        accessible = await preflight_check_url(apply_url, job_id=job_id)
        if not accessible:
            raise RuntimeError(f"Preflight check failed for {apply_url} (CAPTCHA not resolved)")

        site_key = _domain_key(apply_url)
        profile_dir = Path(settings.jobpilot_data_dir) / "browser_profiles" / site_key
        profile_dir.mkdir(parents=True, exist_ok=True)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--disable-infobars",
        ]

        pw = await async_playwright().start()
        context = None
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=launch_args,
            )

            # Apply stealth if available
            try:
                from playwright_stealth import stealth_async  # type: ignore
                page = await context.new_page()
                await stealth_async(page)
            except ImportError:
                page = await context.new_page()

            await page.goto(apply_url, wait_until="domcontentloaded", timeout=20_000)

            # Phase 2: extract form structure + single Gemini call
            html = await page.content()
            form_content = self._clean_form_html(html)
            prompt = self._build_fill_prompt(
                form_content=form_content,
                full_name=full_name,
                email=email,
                phone=phone,
                location=location,
                additional_answers=additional_answers or None,
                has_cv=cv_pdf is not None and cv_pdf.exists(),
                has_letter=letter_pdf is not None and letter_pdf.exists(),
            )

            raw = await self._gemini.generate_text(prompt)
            mapping = self._parse_gemini_response(raw)

            # Phase 3: fill fields
            filled_fields: dict[str, str] = {}
            for field in mapping.get("fields", []):
                sel = field.get("selector", "")
                val = field.get("value", "")
                if not sel or not val:
                    continue
                try:
                    await page.fill(sel, val, timeout=3_000)
                    filled_fields[sel] = val
                except Exception as exc:
                    logger.debug("Could not fill %r: %s", sel, exc)

            # Phase 4: file uploads
            if cv_pdf and cv_pdf.exists():
                for fi in mapping.get("file_inputs", []):
                    if fi.get("file") == "cv":
                        try:
                            await page.set_input_files(fi["selector"], str(cv_pdf), timeout=3_000)
                            logger.info("Uploaded CV: %s", cv_pdf)
                        except Exception as exc:
                            logger.debug("CV upload failed for %r: %s", fi["selector"], exc)

            if letter_pdf and letter_pdf.exists():
                for fi in mapping.get("file_inputs", []):
                    if fi.get("file") == "letter":
                        try:
                            await page.set_input_files(fi["selector"], str(letter_pdf), timeout=3_000)
                            logger.info("Uploaded letter: %s", letter_pdf)
                        except Exception as exc:
                            logger.debug("Letter upload failed for %r: %s", fi["selector"], exc)

            # Phase 5: screenshot
            screenshot_b64: str | None = None
            try:
                raw_ss = await page.screenshot(full_page=False)
                import base64
                screenshot_b64 = base64.b64encode(raw_ss).decode()
            except Exception:
                pass

            # Phase 6: broadcast apply_review for user inspection
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

            # Phase 7: wait for confirm or cancel
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
                raise RuntimeError("Confirmation timed out after 30 minutes")

            if not confirm_event.is_set():
                return {"status": "cancelled", "filled_fields": filled_fields, "screenshot_b64": screenshot_b64}

            # Phase 8: submit
            submit_sel = mapping.get("submit_selector", "button[type=submit]")
            await page.click(submit_sel, timeout=5_000)
            logger.info("[Tier 1] Submitted application for job_id=%d", job_id)

            return {"status": "applied", "filled_fields": filled_fields, "screenshot_b64": screenshot_b64}

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                await pw.stop()
            except Exception:
                pass

    async def fill_only(
        self,
        apply_url: str,
        full_name: str = "",
        email: str = "",
        phone: str = "",
        location: str = "",
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> dict:
        """Fill form fields and stop — for assisted apply (user submits manually).

        Returns dict with status='assisted', filled_fields.
        Raises on failure so AssistedApplyStrategy can fall back to browser-use.
        """
        from playwright.async_api import async_playwright

        from backend.applier.captcha_handler import _domain_key

        site_key = _domain_key(apply_url)
        profile_dir = Path(settings.jobpilot_data_dir) / "browser_profiles" / site_key
        profile_dir.mkdir(parents=True, exist_ok=True)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]

        pw = await async_playwright().start()
        context = None
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=launch_args,
            )
            try:
                from playwright_stealth import stealth_async  # type: ignore
                page = await context.new_page()
                await stealth_async(page)
            except ImportError:
                page = await context.new_page()

            await page.goto(apply_url, wait_until="domcontentloaded", timeout=20_000)

            html = await page.content()
            form_content = self._clean_form_html(html)
            prompt = self._build_fill_prompt(
                form_content=form_content,
                full_name=full_name,
                email=email,
                phone=phone,
                location=location,
                additional_answers=None,
                has_cv=cv_pdf is not None and cv_pdf.exists(),
                has_letter=letter_pdf is not None and letter_pdf.exists(),
            )

            raw = await self._gemini.generate_text(prompt)
            mapping = self._parse_gemini_response(raw)

            filled_fields: dict[str, str] = {}
            for field in mapping.get("fields", []):
                sel = field.get("selector", "")
                val = field.get("value", "")
                if not sel or not val:
                    continue
                try:
                    await page.fill(sel, val, timeout=3_000)
                    filled_fields[sel] = val
                except Exception as exc:
                    logger.debug("Could not fill %r: %s", sel, exc)

            if cv_pdf and cv_pdf.exists():
                for fi in mapping.get("file_inputs", []):
                    if fi.get("file") == "cv":
                        try:
                            await page.set_input_files(fi["selector"], str(cv_pdf), timeout=3_000)
                        except Exception as exc:
                            logger.debug("CV upload failed: %s", exc)

            if letter_pdf and letter_pdf.exists():
                for fi in mapping.get("file_inputs", []):
                    if fi.get("file") == "letter":
                        try:
                            await page.set_input_files(fi["selector"], str(letter_pdf), timeout=3_000)
                        except Exception as exc:
                            logger.debug("Letter upload failed: %s", exc)

            logger.info("[Tier 1 assisted] Form pre-filled for %s", apply_url)
            # Keep browser open — user completes submission manually
            context = None  # prevent finally from closing it
            return {"status": "assisted", "filled_fields": filled_fields}

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                await pw.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Pure helpers (testable without browser/LLM)
    # ------------------------------------------------------------------

    def _clean_form_html(self, html: str) -> str:
        """Strip page HTML to form skeleton only (~15 KB max).

        Same pipeline as ScraplingFetcher._clean_html but scoped to forms.
        """
        try:
            from lxml.html import fromstring  # type: ignore
            from lxml import etree  # type: ignore
            from markdownify import markdownify  # type: ignore
        except ImportError:
            logger.warning("[Tier 1] lxml/markdownify not installed — using raw truncation")
            return html[:_MAX_FORM_CHARS]

        try:
            root = fromstring(html)
        except Exception as exc:
            logger.warning("[Tier 1] HTML parse error: %s", exc)
            return html[:_MAX_FORM_CHARS]

        # Remove noise tags
        for tag in _NOISE_TAGS:
            for elem in root.findall(f".//{tag}"):
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)

        # Strip non-essential attributes
        for elem in root.iter():
            attribs = dict(elem.attrib)
            for attr in attribs:
                if attr not in _KEEP_ATTRS:
                    del elem.attrib[attr]

        try:
            html_str = etree.tostring(root, encoding="unicode", method="html")
            md = markdownify(html_str, heading_style="ATX", strip=["img"])
        except Exception as exc:
            logger.warning("[Tier 1] markdownify failed: %s", exc)
            md = ""

        # markdownify drops void elements like <input> — fall back to cleaned HTML
        if not md or not md.strip():
            try:
                md = etree.tostring(root, encoding="unicode", method="html")
            except Exception:
                md = html[:_MAX_FORM_CHARS]

        md = re.sub(r"\n{3,}", "\n\n", md)
        md = re.sub(r"[ \t]+", " ", md)
        return md[:_MAX_FORM_CHARS]

    def _build_fill_prompt(
        self,
        form_content: str,
        full_name: str,
        email: str,
        phone: str,
        location: str,
        additional_answers: str | None,
        has_cv: bool,
        has_letter: bool,
    ) -> str:
        """Build the single Gemini prompt for form field mapping."""
        lines = [
            "You are a job application form analyst.",
            "Analyse the form content below and return a JSON object with instructions",
            "for filling every visible field. Use CSS selectors.",
            "",
            "Applicant details:",
            f"  Name: {full_name}",
            f"  Email: {email}",
            f"  Phone: {phone}",
            f"  Location: {location}",
        ]

        if additional_answers:
            try:
                parsed = json.loads(additional_answers)
                lines.append("")
                lines.append("Additional answers for custom questions:")
                for k, v in (parsed.items() if isinstance(parsed, dict) else []):
                    lines.append(f"  {k}: {v}")
            except Exception:
                lines.append(f"  Additional context: {additional_answers[:500]}")

        file_note = []
        if has_cv:
            file_note.append("CV/resume (file='cv')")
        if has_letter:
            file_note.append("cover letter (file='letter')")
        if file_note:
            lines += ["", f"Files available to upload: {', '.join(file_note)}"]

        lines += [
            "",
            "Return ONLY valid JSON — no markdown, no explanation — with this exact structure:",
            '{',
            '  "fields": [{"selector": "CSS_SELECTOR", "value": "VALUE_TO_FILL"}],',
            '  "file_inputs": [{"selector": "CSS_SELECTOR", "file": "cv_or_letter"}],',
            '  "submit_selector": "CSS_SELECTOR_FOR_SUBMIT_BUTTON"',
            '}',
            "",
            "If a field cannot be identified, omit it. Do not invent selectors.",
            "",
            "Form content:",
            form_content,
        ]
        return "\n".join(lines)

    def _parse_gemini_response(self, raw: str) -> dict:
        """Extract and parse JSON from Gemini response.

        Returns a safe default dict on any parse failure.
        """
        default: dict = {"fields": [], "file_inputs": [], "submit_selector": "button[type=submit]"}
        if not raw:
            return default
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        # Find first JSON object
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            logger.warning("[Tier 1] No JSON found in Gemini response")
            return default
        try:
            parsed = json.loads(m.group())
            if not isinstance(parsed, dict):
                return default
            parsed.setdefault("fields", [])
            parsed.setdefault("file_inputs", [])
            parsed.setdefault("submit_selector", "button[type=submit]")
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning("[Tier 1] JSON parse error: %s", exc)
            return default


__all__ = ["PlaywrightFormFiller"]
