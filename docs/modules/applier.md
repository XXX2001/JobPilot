# Module: Applier

## Purpose

The `applier` module is responsible for executing job applications on behalf of the user. It sits at the end of the JobPilot pipeline — after jobs have been scraped, ranked, and tailored documents generated — and drives a real browser to fill out and submit application forms. The module implements a two-tier automation strategy: **Tier 1** uses `PlaywrightFormFiller`, a direct Playwright DOM manipulator that extracts the form structure, makes a single Gemini LLM call to map applicant data to CSS selectors, then fills and submits the form programmatically. **Tier 2** falls back to the `browser-use` LLM agent loop, which reasons over the live browser autonomously step-by-step. This tiered design mirrors the scraping architecture: fast and deterministic when possible, powerful and flexible when necessary. Three apply modes are supported — `auto` (fully automated with a mandatory pre-submit user review gate), `assisted` (form pre-filled, user clicks Submit), and `manual` (URL opened in system browser, no automation). The module enforces a configurable daily application cap and persists every outcome to the database as an `Application` record with lifecycle events.

---

## Key Components

### `engine.py`

The central entry point for all apply requests. `ApplicationEngine` owns the three strategy instances (`AutoApplyStrategy`, `AssistedApplyStrategy`, `ManualApplyStrategy`) and coordinates their lifecycle. It enforces the daily limit via `DailyLimitGuard`, guards against concurrent applications to the same job using per-job `asyncio.Event` pairs, dispatches to the correct strategy based on `ApplyMode`, and persists the outcome to the database via `_record_application`. It also exposes `signal_confirm` and `signal_cancel` for the WebSocket layer to trigger user decisions during the review pause.

### `auto_apply.py`

Implements `AutoApplyStrategy`, the fully-automated path. On invocation it first attempts Tier 1 (`PlaywrightFormFiller.fill_and_submit`) if the feature flag is enabled. If Tier 1 raises any exception, it falls back transparently to the Tier 2 `browser-use` agent loop (`_browser_use_apply`). Both tiers pause before submission: they broadcast an `apply_review` WebSocket message containing filled fields and a screenshot, then block on `confirm_event` / `cancel_event` with a 30-minute timeout. Submission only proceeds after the user confirms.

### `assisted_apply.py`

Implements `AssistedApplyStrategy`, a semi-automated path where the user retains final control. Tier 1 calls `PlaywrightFormFiller.fill_only`, which fills the form and deliberately leaves the browser open without submitting. Tier 2 runs a `browser-use` agent with an explicit instruction not to submit. In both cases the result status is `"assisted"` and the browser window remains open for the user to review and submit manually. There is no confirm/cancel gate because the user is always in control.

### `manual_apply.py`

Implements `ManualApplyStrategy`, the zero-automation fallback. It opens the job application URL in the user's default system browser via `webbrowser.open` and returns a `"manual"` result that includes the path to the tailored documents directory so the user knows where their CV and cover letter are stored. Also defines the `ApplicationResult` Pydantic model used by all strategies.

### `form_filler.py`

Implements `PlaywrightFormFiller`, the Tier 1 browser automation engine. It launches a persistent Chromium context (with saved cookies/auth per domain), optionally applies `playwright-stealth` patches, navigates to the apply URL, runs an inline CAPTCHA check, extracts and compresses the page HTML to a form-focused skeleton (`_clean_form_html`), builds a structured prompt (`_build_fill_prompt`), calls Gemini once to get a JSON field mapping, then fills each field via `page.fill` and handles file uploads via `page.set_input_files`. For `fill_and_submit` it then broadcasts the review event, waits for confirmation, and clicks the submit selector returned by Gemini. For `fill_only` it sets `context = None` in the finally block to intentionally leave the browser open.

### `captcha_handler.py`

Handles CAPTCHA and bot-detection blocks. Provides detection via CSS selector matching (`detect_captcha`) and page title / body text pattern matching (`detect_block_page`). When a block is detected, `wait_for_captcha_resolution` broadcasts a `CaptchaDetected` WS event, then polls the page every 2 seconds for up to 5 minutes until the block clears. Once resolved, the browser's storage state (cookies + localStorage) is persisted to a per-domain profile directory so future visits skip the challenge. `preflight_check_url` is a standalone probe that can check a URL before launching an agent: it first probes headlessly, then reopens as visible if blocked. `_domain_key` normalises a URL to a filesystem-safe directory name (e.g. `linkedin_com`).

### `daily_limit.py`

Enforces the configurable daily application cap. `DailyLimitGuard` queries the `Application` table for rows with `applied_at >= today` and `status IN ('applied', 'pending')`. `remaining_today` returns the number of slots left; `assert_can_apply` raises `DailyLimitExceeded` if the quota is exhausted. The daily limit is bypassed entirely for `MANUAL` mode since the user is in full control.

### `__init__.py`

Empty package marker. No public re-exports; consumers import directly from sub-modules.

---

## Public Interface

### `ApplicationResult` — `manual_apply.py`

```python
class ApplicationResult(BaseModel):
    status: str   # "applied" | "assisted" | "manual" | "cancelled"
    method: str   # "auto" | "assisted" | "manual"
    message: str = ""
```

Returned by every strategy's `apply` method and by `ApplicationEngine.apply`. `status` describes the outcome; `method` records which strategy was used; `message` carries human-readable detail (error text, document paths, etc.).

---

### `ApplicantInfo` — `engine.py`

```python
class ApplicantInfo(BaseModel):
    full_name: str = Field("", max_length=200)
    email: str = Field("", max_length=254)
    phone: str = Field("", max_length=30)
    location: str = Field("", max_length=200)
    additional_answers_json: str = Field("", max_length=5000)
```

Carries all applicant personal data passed into the engine. `additional_answers_json` is a JSON-serialised dict of custom question/answer pairs supplied by the user.

---

### `ApplyMode` — `engine.py`

```python
class ApplyMode(str, Enum):
    AUTO = "auto"
    ASSISTED = "assisted"
    MANUAL = "manual"
```

Enum controlling which strategy the engine dispatches to.

---

### `ApplicationEngine` — `engine.py`

```python
class ApplicationEngine:
    def __init__(
        self,
        api_key: str,
        model: str = None,       # defaults to settings.GOOGLE_MODEL
        daily_limit: int = 10,
    ) -> None: ...
```

The top-level coordinator. Instantiated once at startup in `main.py`.

#### `apply`

```python
async def apply(
    self,
    job_match_id: int,
    mode: ApplyMode,
    db: AsyncSession,
    apply_url: str = "",
    applicant: Optional[ApplicantInfo] = None,
    cv_pdf: Optional[Path] = None,
    letter_pdf: Optional[Path] = None,
) -> ApplicationResult:
```

Main entry point. Enforces the daily limit (for AUTO and ASSISTED), prevents concurrent applications to the same job, dispatches to the appropriate strategy, persists the result to the database, and returns the `ApplicationResult`. `cv_pdf` and `letter_pdf` must be resolved to absolute `Path` objects before calling (resolution from the `tailored_documents` DB record is done in the API layer).

#### `signal_confirm`

```python
def signal_confirm(self, job_id: int) -> None:
```

Called by `ws.py` when a `confirm_submit` WebSocket message arrives. Sets the `asyncio.Event` that unblocks the strategy's pre-submit pause for the given job.

#### `signal_cancel`

```python
def signal_cancel(self, job_id: int) -> None:
```

Called by `ws.py` when a `cancel_apply` WebSocket message arrives. Sets the cancel event, causing the strategy to abort and return `status="cancelled"`.

---

### `AutoApplyStrategy` — `auto_apply.py`

```python
class AutoApplyStrategy:
    def __init__(self, api_key: str, model: str | None = None) -> None: ...
```

#### `apply`

```python
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
```

Orchestrates the Tier 1 → Tier 2 fallback. Sanitises the URL first. If `settings.APPLY_TIER1_ENABLED` and the form filler initialised successfully, tries `PlaywrightFormFiller.fill_and_submit`; on any exception falls back to `_browser_use_apply`. Returns `status="applied"` on success, `status="cancelled"` on user cancel or timeout.

---

### `AssistedApplyStrategy` — `assisted_apply.py`

```python
class AssistedApplyStrategy:
    def __init__(self, api_key: str, model: str | None = None) -> None: ...
```

#### `apply`

```python
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
```

Attempts `PlaywrightFormFiller.fill_only` (Tier 1); falls back to a `browser-use` agent instructed not to submit (Tier 2). Always returns `status="assisted"`. No `job_id` or confirm/cancel events — the user submits manually.

---

### `ManualApplyStrategy` — `manual_apply.py`

```python
class ManualApplyStrategy:
    async def apply(
        self,
        apply_url: str,
        cv_pdf: Path | None = None,
        letter_pdf: Path | None = None,
    ) -> ApplicationResult:
```

Opens `apply_url` in the system default browser and returns `status="manual"`. The result message includes the directory where tailored documents are stored. No LLM or Playwright involvement.

---

### `PlaywrightFormFiller` — `form_filler.py`

```python
class PlaywrightFormFiller:
    def __init__(self, gemini_client: GeminiClient) -> None: ...
```

#### `fill_and_submit`

```python
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
```

Eight-phase pipeline: navigate → CAPTCHA check → clean HTML → Gemini call → fill fields → file uploads → screenshot + broadcast review → wait confirm/cancel → click submit. Returns `{"status": "applied"|"cancelled", "filled_fields": {...}, "screenshot_b64": str|None}`. **Raises** on unrecoverable error so the caller can fall back to Tier 2.

#### `fill_only`

```python
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
```

Same pipeline as `fill_and_submit` but stops after field/file filling. Sets `context = None` before the `finally` block to intentionally leave the browser open. Returns `{"status": "assisted", "filled_fields": {...}}`. **Raises** on failure so `AssistedApplyStrategy` can fall back to Tier 2.

#### `_clean_form_html` (internal)

```python
def _clean_form_html(self, html: str) -> str:
```

Strips the full page HTML to a form-focused skeleton: removes `script`, `style`, `nav`, `footer`, `header`, `noscript`, `svg`, `iframe` tags; drops all non-essential attributes (keeps `id`, `name`, `type`, `placeholder`, `required`, `for`, `class`, `action`, `method`); converts to markdown via `markdownify`; collapses whitespace; truncates to `_MAX_FORM_CHARS` (15,000 characters). Falls back to raw truncation if `lxml` or `markdownify` are not installed.

#### `_build_fill_prompt` (internal)

```python
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
```

Builds the single Gemini prompt requesting a JSON object with three keys: `fields` (list of `{selector, value}`), `file_inputs` (list of `{selector, file}`), and `submit_selector`.

#### `_parse_gemini_response` (internal)

```python
def _parse_gemini_response(self, raw: str) -> dict:
```

Strips markdown code fences, extracts the first JSON object via regex, and parses it. Returns a safe default (`{"fields": [], "file_inputs": [], "submit_selector": "button[type=submit]"}`) on any parse failure.

---

### `CaptchaHandler` functions — `captcha_handler.py`

There is no class; the module exposes a collection of module-level async functions.

#### `detect_captcha`

```python
async def detect_captcha(page) -> bool:
```

Iterates `_CAPTCHA_SELECTORS` (reCAPTCHA, hCaptcha, Cloudflare Turnstile, generic markers) and returns `True` if any visible element is found.

#### `detect_block_page`

```python
async def detect_block_page(page) -> bool:
```

Checks the page title and first 500 characters of body text against `_BLOCK_TITLE_FRAGMENTS` (e.g. "just a moment", "access denied", "verify you are human"). Returns `True` on a match.

#### `detect_any_block`

```python
async def detect_any_block(page) -> bool:
```

Returns `detect_captcha(page) or detect_block_page(page)`.

#### `check_and_handle_captcha`

```python
async def check_and_handle_captcha(page, job_id: int | None = None) -> bool:
```

Convenience wrapper: calls `detect_any_block`, and if `True` calls `wait_for_captcha_resolution`. Returns `True` if a block was found and handled.

#### `wait_for_captcha_resolution`

```python
async def wait_for_captcha_resolution(
    page,
    job_id: int | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> bool:
```

Broadcasts a `CaptchaDetected` WS event, then polls `detect_any_block` every `poll_interval` seconds until cleared or `timeout` (300 s default) expires. On resolution persists the browser storage state via `save_session`. Returns `True` if resolved before timeout.

#### `preflight_check_url`

```python
async def preflight_check_url(
    url: str,
    *,
    headless: bool = True,
    job_id: int | None = None,
    timeout: float = 300.0,
) -> bool:
```

Two-phase probe: (1) launches a headless persistent Chromium context; if no block is found, saves storage state and returns `True`. (2) If blocked, relaunches as a visible browser for the user to solve, then calls `wait_for_captcha_resolution`. Returns `True` if accessible (immediately or after solving).

#### `get_session_path`

```python
def get_session_path(url: str) -> Path:
```

Returns the storage-state JSON path for a domain. Prefers `data/browser_profiles/{site}/state.json`; falls back to the legacy `data/browser_sessions/{site}_state.json` path for backward compatibility.

#### `save_session`

```python
async def save_session(page) -> None:
```

Persists the browser context's cookies and localStorage for the page's domain to the canonical profile path.

#### `_domain_key`

```python
def _domain_key(url: str) -> str:
```

Extracts a sanitised domain key from a URL (strips `www.`, replaces `.` with `_`). Used as the directory name for per-domain browser profiles.

---

### `DailyLimitGuard` — `daily_limit.py`

```python
class DailyLimitGuard:
    def __init__(self, db: AsyncSession, limit: int = 10) -> None: ...
```

#### `remaining_today`

```python
async def remaining_today(self) -> int:
```

Queries `Application` for rows with `applied_at >= today` and `status IN ('applied', 'pending')`. Returns `max(0, limit - count)`.

#### `can_apply`

```python
async def can_apply(self) -> bool:
```

Returns `True` if `remaining_today() > 0`.

#### `assert_can_apply`

```python
async def assert_can_apply(self) -> None:
```

Raises `DailyLimitExceeded` if `can_apply()` is `False`.

### `DailyLimitExceeded` — `daily_limit.py`

Plain `Exception` subclass. Message format: `"Daily application limit of {limit} has been reached for today."`.

---

## Data Flow

The full apply pipeline from API call to database record:

```
API / WebSocket layer (queue.py / ws.py)
  │
  │  Resolves cv_pdf and letter_pdf from tailored_documents DB record
  │
  ▼
ApplicationEngine.apply(job_match_id, mode, db, apply_url, applicant, cv_pdf, letter_pdf)
  │
  ├─ [AUTO/ASSISTED] DailyLimitGuard.assert_can_apply()
  │     └─ raises DailyLimitExceeded → returns ApplicationResult(status="cancelled")
  │
  ├─ Guard against concurrent apply for same job_match_id
  │     └─ returns ApplicationResult(status="cancelled") if already in-flight
  │
  ├─ Register asyncio.Event pairs: _confirm_events[job_match_id], _cancel_events[job_match_id]
  │
  ▼
ApplicationEngine._dispatch(mode, ...)
  │
  ├─ AUTO ──► AutoApplyStrategy.apply(...)
  │               │
  │               ├─ sanitize_url()
  │               │
  │               ├─ [APPLY_TIER1_ENABLED=True] PlaywrightFormFiller.fill_and_submit(...)
  │               │     1. launch_persistent_context (per-domain profile dir)
  │               │     2. playwright-stealth (optional)
  │               │     3. page.goto(apply_url)
  │               │     4. check_and_handle_captcha()
  │               │          └─ if blocked: broadcast CaptchaDetected WS, poll until clear,
  │               │               save_session(), broadcast CaptchaResolved WS
  │               │     5. _clean_form_html() → form skeleton ≤15 KB
  │               │     6. _build_fill_prompt() → structured Gemini prompt
  │               │     7. GeminiClient.generate_text() → JSON field mapping
  │               │     8. page.fill() for each field; page.set_input_files() for CV/letter
  │               │     9. page.screenshot() → base64
  │               │    10. broadcast apply_review WS (filled_fields + screenshot)
  │               │    11. asyncio.wait([confirm_event, cancel_event], timeout=1800s)
  │               │    12. page.click(submit_selector)  ← only if confirmed
  │               │     └─ returns {"status": "applied"|"cancelled", ...}
  │               │
  │               ├─ [Tier 1 raises] → fall through to Tier 2
  │               │
  │               └─ [Tier 2] _browser_use_apply(...)
  │                     1. Build fill_task prompt string
  │                     2. Browser(headless=False) + ChatGoogleGenerativeAI
  │                     3. Agent(task=fill_task).run() → pauses before submit
  │                     4. Parse filled_fields from agent final_result()
  │                     5. broadcast apply_review WS
  │                     6. asyncio.wait([confirm_event, cancel_event], timeout=1800s)
  │                     7. Agent(task=submit_task).run()  ← only if confirmed
  │                     8. browser.stop()
  │
  ├─ ASSISTED ──► AssistedApplyStrategy.apply(...)
  │               │
  │               ├─ [APPLY_TIER1_ENABLED=True] PlaywrightFormFiller.fill_only(...)
  │               │     Steps 1-8 of fill_and_submit, then stops (browser left open)
  │               │     returns ApplicationResult(status="assisted")
  │               │
  │               └─ [Tier 1 raises] → browser-use agent (no submit instruction)
  │                     browser remains open for user
  │
  └─ MANUAL ──► ManualApplyStrategy.apply(...)
                    webbrowser.open(apply_url)
                    returns ApplicationResult(status="manual")
  │
  ▼
ApplicationEngine._record_application(db, job_match_id, result)
  ├─ INSERT Application(job_match_id, method, status, applied_at, notes)
  ├─ INSERT ApplicationEvent(application_id, event_type, details)
  └─ db.commit()
  │
  ▼
ApplicationResult returned to caller
```

WebSocket signals flow in the reverse direction during the review pause:

```
Browser client → WS message "confirm_submit" / "cancel_apply"
  → ws.py → ApplicationEngine.signal_confirm(job_id) / signal_cancel(job_id)
    → asyncio.Event.set()
      → unblocks asyncio.wait() in fill_and_submit or _browser_use_apply
```

---

## Configuration

All settings are loaded from environment variables (or `.env` file) via `backend/config.py` using `pydantic-settings`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `APPLY_TIER1_ENABLED` | `bool` | `True` | Feature flag for `PlaywrightFormFiller`. When `False`, both `AutoApplyStrategy` and `AssistedApplyStrategy` skip Tier 1 and go directly to the browser-use agent. |
| `GOOGLE_API_KEY` | `str` | — (required) | Gemini API key passed to both `GeminiClient` (Tier 1) and `ChatGoogleGenerativeAI` (Tier 2). |
| `GOOGLE_MODEL` | `str` | `"gemini-3-flash-preview"` | Primary model used for both the Tier 1 Gemini form-analysis call and the Tier 2 browser-use agent. |
| `GOOGLE_MODEL_FALLBACKS` | `str` | `""` | Comma-separated fallback model names for `GeminiClient`. Not directly used by the applier but affects LLM reliability. |
| `JOBPILOT_DATA_DIR` | `str` | `"./data"` | Root data directory. Browser profiles are stored at `{data_dir}/browser_profiles/{domain_key}/`, legacy sessions at `{data_dir}/browser_sessions/`. |

**Database-stored settings** (in `UserSettings` / `SiteSettings` model):

| Field | Default | Description |
|---|---|---|
| `daily_limit` | `10` | Maximum applications per day. Loaded by the morning batch scheduler and passed to `ApplicationEngine(daily_limit=...)`. The API settings endpoint allows updating this value. |

**Runtime constants** (hardcoded, not configurable):

| Constant | Location | Value | Description |
|---|---|---|---|
| `_MAX_FORM_CHARS` | `form_filler.py` | `15_000` | Maximum characters of cleaned form HTML sent to Gemini. |
| confirm/cancel timeout | `form_filler.py`, `auto_apply.py` | `1800` seconds | How long the review gate waits before auto-cancelling. |
| CAPTCHA poll interval | `captcha_handler.py` | `2.0` seconds | Frequency of block-resolution polling. |
| CAPTCHA timeout | `captcha_handler.py` | `300.0` seconds | Maximum wait time for user to solve a CAPTCHA. |

---

## Known Limitations / TODOs

**Hardcoded values:**

- The daily limit defaults to `10` in three separate places: `ApplicationEngine.__init__`, `main.py`, and `morning_batch.py` line 264 (batch scheduler). These are not coordinated from a single source of truth.
- The confirm/cancel review timeout is hardcoded at 1800 seconds (30 minutes) in both `form_filler.py` and `auto_apply.py`. There is no setting to adjust it.
- The CAPTCHA polling interval (2 s) and timeout (300 s) in `captcha_handler.py` are module-level constants with no env var override.
- `_MAX_FORM_CHARS` (15,000) in `form_filler.py` is a fixed limit. Large forms may be truncated, causing Gemini to miss fields.
- The submit selector fallback in `_parse_gemini_response` defaults to `"button[type=submit]"`, which may not match all job board submit patterns.

**Missing features:**

- `AssistedApplyStrategy.apply` does not accept `additional_answers` — it silently ignores any custom question/answer pairs the user may have supplied. The `fill_only` call drops this parameter.
- `PlaywrightFormFiller.fill_only` does not perform an inline CAPTCHA check (unlike `fill_and_submit`), so assisted apply on CAPTCHA-protected pages will fail silently at Tier 1 and fall back to Tier 2.
- The `preflight_check_url` function in `captcha_handler.py` is defined but not called by `AutoApplyStrategy` or `AssistedApplyStrategy` — it is currently unused in the main apply pipeline.
- Screenshot size validation in the Tier 2 `_browser_use_apply` checks `len(ss) < 5_000_000` but only if the agent result has a `screenshot_base64` attribute, which is not part of the `browser-use` public API and may never be set.
- There is no retry logic at either tier. A transient network error during `page.goto` or a partial form fill will immediately trigger the Tier 2 fallback (or fail outright in Tier 2).
- Browser profiles accumulate on disk indefinitely; there is no cleanup or rotation mechanism.
- The `browser-use` agent in both AUTO and ASSISTED Tier 2 paths does not load the persisted storage state (cookies saved by `captcha_handler`). Only `PlaywrightFormFiller` uses `launch_persistent_context` with the profile directory.
- Multi-page application forms (wizards with multiple steps) are not explicitly handled by Tier 1; Gemini only sees the first page's form HTML.
- No mechanism to surface which specific fields failed to fill — only successful fills are included in `filled_fields`.
