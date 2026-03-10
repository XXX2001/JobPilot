# Two-Tier Auto-Apply Design

**Date:** 2026-03-10
**Scope:** Replace browser-use agent loop in auto-apply with Playwright direct + single Gemini call (Tier 1), keeping browser-use as Tier 2 fallback
**Goal:** ~90% reduction in Gemini API calls, reliable file uploads, session reuse, CAPTCHA handling

---

## Background

The current auto-apply strategy (`AutoApplyStrategy`) runs two separate browser-use agents (fill phase + submit phase), each consuming ~10-20 Gemini API calls. This mirrors the pre-Scrapling scraping approach that was replaced in the two-tier scraping plan. The same pattern applies here:

- **Before scraping fix:** browser-use agent loop (~20 calls) for every site
- **After scraping fix:** Scrapling HTTP fetch + single Gemini call (Tier 1), browser-use as fallback (Tier 2)
- **Before apply fix:** two browser-use agents (~20-40 calls), no file uploads, no session reuse
- **After apply fix:** Playwright direct + single Gemini call (Tier 1), browser-use as fallback (Tier 2)

---

## Known Bugs Fixed by This Design

| Bug | Location | Fix |
|-----|----------|-----|
| Browser leak on 30-min timeout | `auto_apply.py:168-174` | `finally` block in Tier 1 filler |
| CV/letter files always `None` | `applications.py:apply_to_job()` | Resolve from `tailored_documents` DB table |
| `preflight_check_url()` never called | `auto_apply.py` | Called at start of Tier 1 filler |
| Browser sessions never loaded | `auto_apply.py:97` | `launch_persistent_context()` loads profile |
| Assisted apply browser never closed | `assisted_apply.py` | Apply same Tier 1 approach |
| `additional_answers` dumped as raw JSON | `auto_apply.py:88` | Parsed into structured instructions |
| Two agents with no shared memory | `auto_apply.py` | Single Playwright context throughout |

---

## Architecture

```
AutoApplyStrategy.apply()
  │
  ├── Tier 1: PlaywrightFormFiller.fill_and_submit()
  │     ├── 1. preflight_check_url()              [existing captcha_handler — finally used]
  │     ├── 2. launch_persistent_context()         [load browser_profiles/{domain}/state.json]
  │     ├── 3. page.goto(apply_url)
  │     ├── 4. extract form HTML → _clean_form_html()
  │     ├── 5. single Gemini call → field-mapping JSON
  │     ├── 6. page.fill() / page.set_input_files()  [cv_pdf, letter_pdf attached]
  │     ├── 7. broadcast apply_review WS + screenshot
  │     ├── 8. wait for confirm/cancel event
  │     └── 9. page.click(submit_selector)
  │
  └── Tier 2 fallback: existing browser-use agent (unchanged)
        └── runs only when Tier 1 raises an exception
```

---

## Tier 1 — `PlaywrightFormFiller`

### Module: `backend/applier/form_filler.py`

**Single Gemini call output** (structured JSON):
```json
{
  "fields": [
    {"selector": "#name", "value": "John Doe"},
    {"selector": "#email", "value": "john@example.com"},
    {"selector": "#phone", "value": "+33 6 12 34 56 78"},
    {"selector": "textarea[name=cover]", "value": "..."}
  ],
  "file_inputs": [
    {"selector": "input[type=file][name=resume]", "file": "cv"},
    {"selector": "input[type=file][name=cover_letter]", "file": "letter"}
  ],
  "submit_selector": "button[type=submit]",
  "notes": "Multi-step form: click Next after step 1"
}
```

**Form HTML cleaning** (mirrors `ScraplingFetcher._clean_html`):
- Remove: `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<noscript>`, `<svg>`
- Keep: `<form>`, `<input>`, `<select>`, `<textarea>`, `<label>`, `<button>`
- Keep attributes: `id`, `name`, `type`, `placeholder`, `required`, `for`, `class`
- Convert to markdown via markdownify (already a dependency)
- Truncate to 15,000 chars max

**Session loading:**
- Uses `captcha_handler.get_session_path(url)` to resolve profile path
- `launch_persistent_context(user_data_dir=profile_dir, ...)` — same approach as `preflight_check_url()`
- Stealth patches applied via `playwright_stealth` if available

**Error handling:**
- Any exception in Tier 1 → log warning → caller falls back to Tier 2
- Browser always closed in `finally` block (fixes current leak)

---

## Tier 2 — Existing `AutoApplyStrategy` (fallback)

The existing browser-use agent code is kept intact and renamed to `_browser_use_apply()`. It is only invoked when `PlaywrightFormFiller` raises an exception.

**No behavioral changes to Tier 2.**

---

## Routing in `auto_apply.py`

```python
async def apply(self, ...) -> ApplicationResult:
    if self._tier1_enabled:
        try:
            return await self._form_filler.fill_and_submit(...)
        except Exception as exc:
            logger.warning("Tier 1 apply failed — falling back to browser-use: %s", exc)

    # Tier 2: browser-use agent (existing logic)
    return await self._browser_use_apply(...)
```

---

## CV/Letter Resolution in `applications.py`

The `apply_to_job()` endpoint currently never resolves `cv_pdf`/`letter_pdf`. Fix: query `tailored_documents` table for the latest CV and letter PDFs for this `match_id` before calling the engine.

```python
# Resolve tailored documents from DB
from backend.models.document import TailoredDocument
cv_doc = await db.execute(
    select(TailoredDocument)
    .where(TailoredDocument.job_match_id == match_id, TailoredDocument.doc_type == "cv")
    .order_by(TailoredDocument.created_at.desc())
    .limit(1)
)
cv_pdf = Path(row.pdf_path) if (row := cv_doc.scalar_one_or_none()) and row.pdf_path else None
# same for letter
```

---

## `AssistedApplyStrategy` Update

Apply the same Tier 1 approach: `PlaywrightFormFiller.fill_only()` (no submit, no confirm/cancel wait). Falls back to current browser-use agent if Tier 1 fails. Fixes the browser-never-closed leak.

---

## Feature Flag

```python
# backend/config.py
APPLY_TIER1_ENABLED: bool = True  # mirrors SCRAPLING_ENABLED
```

Setting `APPLY_TIER1_ENABLED=False` reverts to current behavior. No DB changes.

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `backend/applier/form_filler.py` | **NEW** | Tier 1: Playwright direct + single Gemini call |
| `backend/applier/auto_apply.py` | MODIFY | Tier routing, fix browser leak on timeout |
| `backend/applier/assisted_apply.py` | MODIFY | Use Tier 1 `fill_only()`, fix browser leak |
| `backend/api/applications.py` | MODIFY | Resolve `cv_pdf`/`letter_pdf` from `tailored_documents` |
| `backend/config.py` | MODIFY | Add `APPLY_TIER1_ENABLED` flag |

**No changes:** `engine.py`, `manual_apply.py`, `captcha_handler.py`, `daily_limit.py`, models, DB schema

---

## Rollback

- Set `APPLY_TIER1_ENABLED=False` → full revert to current behavior
- No schema migrations, no data format changes
- `PlaywrightFormFiller` is purely additive
