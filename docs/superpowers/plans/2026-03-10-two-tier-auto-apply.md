# Two-Tier Auto-Apply Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-agent browser-use apply loop with a direct Playwright + single Gemini call (Tier 1), keeping browser-use as Tier 2 fallback — mirroring the Scrapling two-tier scraping architecture.

**Architecture:** `PlaywrightFormFiller` (new module) does: preflight CAPTCHA check → load browser profile → extract form HTML → single Gemini call for field mapping → Playwright fill + file upload → broadcast review → wait confirm/cancel → Playwright submit. `AutoApplyStrategy` tries Tier 1 first; any exception falls through to the existing browser-use agent (Tier 2, unchanged). A feature flag `APPLY_TIER1_ENABLED` mirrors `SCRAPLING_ENABLED` for instant rollback.

**Tech Stack:** Python 3.11+, Playwright (async_api), `playwright_stealth` (optional), Pydantic, FastAPI, SQLAlchemy async, `backend.llm.gemini_client.GeminiClient`, `backend.applier.captcha_handler` (existing)

---

## Chunk 1: Feature flag + pure helper functions

### Task 1: Add `APPLY_TIER1_ENABLED` to config

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1.1: Add the flag**

Open `backend/config.py` and add after the `SCRAPLING_ENABLED` line:

```python
    # Feature flag: enable Tier 1 Playwright direct filler (mirrors SCRAPLING_ENABLED)
    APPLY_TIER1_ENABLED: bool = Field(True, env="APPLY_TIER1_ENABLED")
```

- [ ] **Step 1.2: Verify import works**

```bash
cd /home/mouad/Web-automation
python -c "from backend.config import settings; print(settings.APPLY_TIER1_ENABLED)"
```

Expected: `True`

- [ ] **Step 1.3: Commit**

```bash
git add backend/config.py
git commit -m "feat(apply): add APPLY_TIER1_ENABLED feature flag"
```

---

### Task 2: Create `form_filler.py` with pure helper functions (TDD)

**Files:**
- Create: `backend/applier/form_filler.py`
- Create: `tests/test_form_filler.py`

The pure functions `_clean_form_html()` and `_build_fill_prompt()` are fully testable without a browser or LLM.

- [ ] **Step 2.1: Write failing tests for `_clean_form_html`**

Create `tests/test_form_filler.py`:

```python
"""Tests for PlaywrightFormFiller pure helper functions."""
from __future__ import annotations

import pytest
from backend.applier.form_filler import PlaywrightFormFiller


def _filler() -> PlaywrightFormFiller:
    """Create a filler instance without any live clients."""
    from unittest.mock import MagicMock
    return PlaywrightFormFiller(gemini_client=MagicMock())


# ── _clean_form_html ──────────────────────────────────────────────────────────


def test_clean_form_html_removes_scripts():
    filler = _filler()
    html = "<html><body><form><input name='x'/></form><script>evil()</script></body></html>"
    result = filler._clean_form_html(html)
    assert "evil()" not in result
    assert "input" in result.lower() or "x" in result


def test_clean_form_html_removes_nav_and_footer():
    filler = _filler()
    html = (
        "<html><body>"
        "<nav>Menu</nav>"
        "<form><input name='email' placeholder='Email'/></form>"
        "<footer>Footer</footer>"
        "</body></html>"
    )
    result = filler._clean_form_html(html)
    assert "Menu" not in result
    assert "Footer" not in result
    assert "email" in result.lower()


def test_clean_form_html_keeps_form_attributes():
    filler = _filler()
    html = (
        "<form>"
        "<label for='name'>Name</label>"
        "<input id='name' name='full_name' type='text' placeholder='Your name' required/>"
        "<input type='file' name='resume'/>"
        "<button type='submit'>Apply</button>"
        "</form>"
    )
    result = filler._clean_form_html(html)
    # Essential form structure should be preserved
    assert "name" in result.lower()
    assert "submit" in result.lower() or "apply" in result.lower()


def test_clean_form_html_truncates_to_max():
    filler = _filler()
    big_html = "<form>" + "<input name='x'/>" * 5000 + "</form>"
    result = filler._clean_form_html(big_html)
    assert len(result) <= 15_100  # 15_000 + small markdown overhead


# ── _build_fill_prompt ────────────────────────────────────────────────────────


def test_build_fill_prompt_contains_applicant_fields():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form><input name='email'/></form>",
        full_name="Alice Dupont",
        email="alice@example.com",
        phone="+33 6 00 00 00 00",
        location="Paris, France",
        additional_answers=None,
        has_cv=True,
        has_letter=True,
    )
    assert "Alice Dupont" in prompt
    assert "alice@example.com" in prompt
    assert "+33 6 00 00 00 00" in prompt
    assert "Paris, France" in prompt


def test_build_fill_prompt_mentions_file_upload_when_files_present():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form><input type='file'/></form>",
        full_name="Bob",
        email="bob@x.com",
        phone="",
        location="",
        additional_answers=None,
        has_cv=True,
        has_letter=False,
    )
    assert "cv" in prompt.lower() or "resume" in prompt.lower()


def test_build_fill_prompt_includes_additional_answers():
    filler = _filler()
    import json
    answers = json.dumps({"years_experience": "3", "visa_required": "no"})
    prompt = filler._build_fill_prompt(
        form_content="<form/>",
        full_name="X",
        email="x@x.com",
        phone="",
        location="",
        additional_answers=answers,
        has_cv=False,
        has_letter=False,
    )
    assert "years_experience" in prompt
    assert "visa_required" in prompt


def test_build_fill_prompt_returns_json_schema_instructions():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form/>",
        full_name="X",
        email="x@x.com",
        phone="",
        location="",
        additional_answers=None,
        has_cv=False,
        has_letter=False,
    )
    # Must instruct the LLM to return JSON with expected keys
    assert "fields" in prompt
    assert "submit_selector" in prompt
    assert "JSON" in prompt
```

- [ ] **Step 2.2: Run tests — verify they fail**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/test_form_filler.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'backend.applier.form_filler'`

- [ ] **Step 2.3: Create `backend/applier/form_filler.py` with pure helpers only**

```python
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
        )
        from backend.applier.captcha_handler import _domain_key  # noqa: PLC2701

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

        from backend.applier.captcha_handler import _domain_key  # noqa: PLC2701

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
            # (context intentionally NOT closed here so window stays visible)
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
            md = root.text_content() if hasattr(root, "text_content") else html[:_MAX_FORM_CHARS]

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
            # Ensure expected keys exist
            parsed.setdefault("fields", [])
            parsed.setdefault("file_inputs", [])
            parsed.setdefault("submit_selector", "button[type=submit]")
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning("[Tier 1] JSON parse error: %s", exc)
            return default


__all__ = ["PlaywrightFormFiller"]
```

- [ ] **Step 2.4: Run tests — verify they pass**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/test_form_filler.py -v
```

Expected: All tests PASS (no browser, no LLM — only pure functions exercised)

- [ ] **Step 2.5: Commit**

```bash
git add backend/applier/form_filler.py tests/test_form_filler.py
git commit -m "feat(apply): add PlaywrightFormFiller Tier 1 filler with pure helper tests"
```

---

## Chunk 2: Tier routing in `auto_apply.py` + fix browser leak

### Task 3: Update `AutoApplyStrategy` with tier routing

**Files:**
- Modify: `backend/applier/auto_apply.py`
- Modify: `tests/test_apply_engine.py`

- [ ] **Step 3.1: Write failing test for tier routing**

Add to `tests/test_apply_engine.py`:

```python
# ── Tier routing ──────────────────────────────────────────────────────────────

_SANITIZE = "backend.security.sanitizer.sanitize_url"

@pytest.mark.asyncio
async def test_auto_apply_tier1_success_no_tier2():
    """If Tier 1 succeeds, Tier 2 (browser-use) should NOT be called."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")
    fake_result = {"status": "applied", "filled_fields": {}, "screenshot_b64": None}

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(return_value=fake_result)) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock()) as mock_t2:
        result = await strategy.apply(job_id=1, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_not_awaited()
    assert result.status == "applied"


@pytest.mark.asyncio
async def test_auto_apply_tier1_failure_falls_back_to_tier2():
    """If Tier 1 raises, Tier 2 (browser-use) should be called."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from backend.applier.manual_apply import ApplicationResult
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(side_effect=RuntimeError("preflight failed"))) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock(return_value=ApplicationResult(status="applied", method="auto"))) as mock_t2:
        result = await strategy.apply(job_id=2, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_awaited_once()
    assert result.status == "applied"


@pytest.mark.asyncio
async def test_auto_apply_tier1_cancelled_does_not_fall_back():
    """If Tier 1 returns cancelled (user cancelled), do NOT fall back to Tier 2."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")
    fake_result = {"status": "cancelled", "filled_fields": {}, "screenshot_b64": None}

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(return_value=fake_result)) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock()) as mock_t2:
        result = await strategy.apply(job_id=3, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_not_awaited()
    assert result.status == "cancelled"


@pytest.mark.asyncio
async def test_auto_apply_tier1_disabled_goes_straight_to_tier2(monkeypatch):
    """When APPLY_TIER1_ENABLED=False, skip Tier 1 entirely."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from backend.applier.manual_apply import ApplicationResult
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr("backend.config.settings.APPLY_TIER1_ENABLED", False)
    strategy = AutoApplyStrategy(api_key="key")

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock()) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock(return_value=ApplicationResult(status="applied", method="auto"))) as mock_t2:
        result = await strategy.apply(job_id=4, apply_url="https://example.com/job")

    mock_t1.assert_not_awaited()
    mock_t2.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_use_apply_parses_additional_answers_json(monkeypatch):
    """Tier 2 _browser_use_apply formats additional_answers as key-value pairs, not raw JSON."""
    import backend.applier.auto_apply as mod
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, MagicMock, patch
    import json

    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(mod, "ChatGoogleGenerativeAI", MagicMock())
    monkeypatch.setattr(mod, "Browser", MagicMock())

    captured_task: list[str] = []

    def fake_agent(task, llm, browser):
        captured_task.append(task)
        m = MagicMock()
        m.run = AsyncMock(return_value=MagicMock(final_result=MagicMock(return_value="")))
        return m

    monkeypatch.setattr(mod, "Agent", fake_agent)

    strategy = AutoApplyStrategy(api_key="key")
    answers = json.dumps({"years_experience": "3", "visa_required": "no"})

    # We'll cancel immediately to avoid hanging on the confirm/cancel wait
    cancel = asyncio.Event()
    cancel.set()

    await strategy._browser_use_apply(
        job_id=99,
        apply_url="https://example.com/job",
        additional_answers=answers,
        cancel_event=cancel,
        confirm_event=asyncio.Event(),
    )

    assert captured_task, "Agent was never called"
    task_str = captured_task[0]
    # Should contain parsed key-value pairs, NOT a raw JSON blob
    assert "years_experience" in task_str
    assert "visa_required" in task_str
    assert '{"years_experience"' not in task_str  # raw JSON must not appear
```

- [ ] **Step 3.2: Run new tests — verify they fail**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/test_apply_engine.py::test_auto_apply_tier1_success_no_tier2 -v
```

Expected: `AttributeError` — `_form_filler` doesn't exist yet on `AutoApplyStrategy`

- [ ] **Step 3.3: Rewrite `backend/applier/auto_apply.py`**

Replace the entire file with:

```python
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

        # Tier 1: lazy import to avoid hard dep if playwright not installed
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
                    # Definitive result — do NOT fall through to Tier 2
                    return ApplicationResult(
                        status=status,
                        method="auto",
                        message="Applied via Tier 1 (Playwright direct)"
                        if status == "applied"
                        else "Cancelled by user.",
                    )
                # status == something unexpected → fall through
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
        """Tier 2: original browser-use agent loop (unchanged logic)."""

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
                import json as _json
                parsed_answers = _json.loads(additional_answers)
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
```

- [ ] **Step 3.4: Run all apply engine tests**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/test_apply_engine.py -v
```

Expected: All existing tests + all 4 new tier routing tests PASS

- [ ] **Step 3.5: Commit**

```bash
git add backend/applier/auto_apply.py tests/test_apply_engine.py
git commit -m "feat(apply): two-tier routing in AutoApplyStrategy, fix browser leak on timeout"
```

---

## Chunk 3: Update `assisted_apply.py` + resolve CV/letter in API

### Task 4: Update `AssistedApplyStrategy` to use `fill_only()`

**Files:**
- Modify: `backend/applier/assisted_apply.py`
- Modify: `tests/test_apply_engine.py`

- [ ] **Step 4.1: Write failing test**

Add to `tests/test_apply_engine.py`:

```python
@pytest.mark.asyncio
async def test_assisted_apply_tier1_success():
    """AssistedApplyStrategy uses fill_only() when Tier 1 available."""
    from backend.applier.assisted_apply import AssistedApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AssistedApplyStrategy(api_key="key")
    fake_result = {"status": "assisted", "filled_fields": {"#name": "Alice"}}

    with patch.object(strategy._form_filler, "fill_only", new=AsyncMock(return_value=fake_result)) as mock_t1:
        result = await strategy.apply(apply_url="https://example.com/job", full_name="Alice")

    mock_t1.assert_awaited_once()
    assert result.status == "assisted"
    assert "pre-filled" in result.message.lower()


@pytest.mark.asyncio
async def test_assisted_apply_tier1_failure_falls_back():
    """AssistedApplyStrategy falls back to browser-use when fill_only() raises."""
    from backend.applier.assisted_apply import AssistedApplyStrategy
    import backend.applier.assisted_apply as mod
    from unittest.mock import AsyncMock, MagicMock, patch

    strategy = AssistedApplyStrategy(api_key="key")

    with patch.object(strategy._form_filler, "fill_only", new=AsyncMock(side_effect=RuntimeError("page crash"))), \
         patch.object(mod, "_BROWSER_USE_AVAILABLE", True), \
         patch.object(mod, "Agent", MagicMock()), \
         patch.object(mod, "ChatGoogleGenerativeAI", MagicMock()), \
         patch.object(mod, "Browser", MagicMock()):
        # patch the inner browser-use call to return immediately
        async def fake_agent_run():
            return MagicMock()
        mod.Agent.return_value.run = fake_agent_run
        result = await strategy.apply(apply_url="https://example.com/job")

    assert result.status == "assisted"
```

- [ ] **Step 4.2: Run tests — verify they fail**

```bash
python -m pytest tests/test_apply_engine.py::test_assisted_apply_tier1_success -v
```

Expected: `AttributeError: 'AssistedApplyStrategy' object has no attribute '_form_filler'`

- [ ] **Step 4.3: Rewrite `backend/applier/assisted_apply.py`**

```python
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
```

- [ ] **Step 4.4: Run all apply engine tests**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/test_apply_engine.py -v
```

Expected: All tests PASS

- [ ] **Step 4.5: Commit**

```bash
git add backend/applier/assisted_apply.py tests/test_apply_engine.py
git commit -m "feat(apply): two-tier AssistedApplyStrategy, fix browser-use never closed"
```

---

### Task 5: Resolve CV/letter from DB in `applications.py`

**Files:**
- Modify: `backend/api/applications.py`
- Modify: `tests/test_apply_engine.py`

- [ ] **Step 5.1: Write failing test for CV/letter resolution**

Add to `tests/test_apply_engine.py`:

```python
@pytest.mark.asyncio
async def test_apply_to_job_resolves_cv_and_letter_from_db():
    """apply_to_job() passes cv_pdf and letter_pdf from tailored_documents to engine."""
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock, MagicMock, patch
    from pathlib import Path
    from backend.main import app

    # We test the resolution logic in isolation — mock the DB query
    from backend.api.applications import _resolve_documents
    from backend.models.document import TailoredDocument

    cv_doc = MagicMock(spec=TailoredDocument)
    cv_doc.pdf_path = "/data/cvs/cv.pdf"
    letter_doc = MagicMock(spec=TailoredDocument)
    letter_doc.pdf_path = "/data/letters/letter.pdf"

    db = AsyncMock()
    # First call → cv, second call → letter
    db.execute.return_value.scalar_one_or_none = MagicMock(side_effect=[cv_doc, letter_doc])

    cv_path, letter_path = await _resolve_documents(match_id=42, db=db)
    assert cv_path == Path("/data/cvs/cv.pdf")
    assert letter_path == Path("/data/letters/letter.pdf")
```

- [ ] **Step 5.2: Run test — verify it fails**

```bash
python -m pytest tests/test_apply_engine.py::test_apply_to_job_resolves_cv_and_letter_from_db -v
```

Expected: `ImportError: cannot import name '_resolve_documents'`

- [ ] **Step 5.3: Add `_resolve_documents` helper + wire into `apply_to_job`**

In `backend/api/applications.py`, add the helper function just before the `apply_to_job` endpoint (around line 300), and update the endpoint to call it:

```python
# Add this import at the top of the file with other imports:
from backend.models.document import TailoredDocument


async def _resolve_documents(
    match_id: int, db
) -> tuple["Path | None", "Path | None"]:
    """Return (cv_pdf, letter_pdf) Paths for the latest tailored docs for match_id.

    Returns (None, None) if no documents have been generated yet.
    """
    from pathlib import Path
    from sqlalchemy import select

    cv_path: "Path | None" = None
    letter_path: "Path | None" = None

    cv_stmt = (
        select(TailoredDocument)
        .where(
            TailoredDocument.job_match_id == match_id,
            TailoredDocument.doc_type == "cv",
        )
        .order_by(TailoredDocument.created_at.desc())
        .limit(1)
    )
    cv_row = (await db.execute(cv_stmt)).scalar_one_or_none()
    if cv_row and cv_row.pdf_path:
        cv_path = Path(cv_row.pdf_path)

    letter_stmt = (
        select(TailoredDocument)
        .where(
            TailoredDocument.job_match_id == match_id,
            TailoredDocument.doc_type == "letter",
        )
        .order_by(TailoredDocument.created_at.desc())
        .limit(1)
    )
    letter_row = (await db.execute(letter_stmt)).scalar_one_or_none()
    if letter_row and letter_row.pdf_path:
        letter_path = Path(letter_row.pdf_path)

    return cv_path, letter_path
```

Then in `apply_to_job()`, add document resolution before the `engine.apply()` call. Find the block starting with `applicant = ApplicantInfo(...)` and add after it:

```python
    # Resolve tailored CV and cover letter for this job match
    cv_pdf, letter_pdf = await _resolve_documents(match_id=match_id, db=db)
    if cv_pdf:
        logger.info("Resolved cv_pdf=%s for match_id=%d", cv_pdf, match_id)
    if letter_pdf:
        logger.info("Resolved letter_pdf=%s for match_id=%d", letter_pdf, match_id)
```

And update the `engine.apply()` call to pass the resolved paths. Note: `engine.apply()` already accepts `cv_pdf` and `letter_pdf` parameters — **no changes to `engine.py` are needed**:

```python
    result = await engine.apply(
        job_match_id=match_id,
        mode=mode,
        db=db,
        apply_url=apply_url,
        applicant=applicant,
        cv_pdf=cv_pdf,        # was always None before
        letter_pdf=letter_pdf,  # was always None before
    )
```

- [ ] **Step 5.4: Run test**

```bash
python -m pytest tests/test_apply_engine.py::test_apply_to_job_resolves_cv_and_letter_from_db -v
```

Expected: PASS

- [ ] **Step 5.5: Run full test suite to check for regressions**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/ -v --ignore=tests/integration -x 2>&1 | tail -30
```

Expected: All tests PASS (or pre-existing failures only)

- [ ] **Step 5.6: Commit**

```bash
git add backend/api/applications.py tests/test_apply_engine.py
git commit -m "fix(apply): resolve cv_pdf/letter_pdf from tailored_documents DB before engine.apply()"
```

---

## Final Verification

- [ ] **Step 6.1: Run the complete test suite**

```bash
cd /home/mouad/Web-automation
python -m pytest tests/ -v --ignore=tests/integration 2>&1 | tail -40
```

Expected: All tests pass. Note any pre-existing failures.

- [ ] **Step 6.2: Verify imports are clean**

```bash
python -c "
from backend.applier.form_filler import PlaywrightFormFiller
from backend.applier.auto_apply import AutoApplyStrategy
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.api.applications import _resolve_documents
from backend.config import settings
print('APPLY_TIER1_ENABLED:', settings.APPLY_TIER1_ENABLED)
print('All imports OK')
"
```

Expected:
```
APPLY_TIER1_ENABLED: True
All imports OK
```

- [ ] **Step 6.3: Final commit**

```bash
git add -u
git commit -m "feat(apply): two-tier auto-apply complete — Playwright Tier 1 + browser-use Tier 2 fallback"
```
