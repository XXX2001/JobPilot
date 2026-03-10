# Module: LLM

## Purpose

The `backend/llm/` module is JobPilot's interface to Google Gemini. It wraps the `google-genai` SDK to provide three coordinated capabilities: job description analysis (extracting structured skill and keyword data from raw postings), CV tailoring (producing surgical LaTeX text replacements to improve job fit without fabricating content), and motivation-letter customization (adapting a single customizable paragraph to the target role). The module exists to isolate all LLM concerns — API key management, rate limiting, model fallback, prompt templating, JSON parsing, and output validation — behind clean, typed Python interfaces. The rest of JobPilot (the scheduler, the apply engine, and the CV pipeline) calls into this module without knowing anything about Gemini internals.

---

## Key Components

### `gemini_client.py`

Core async HTTP wrapper around the `google-genai` SDK. Implements a sliding-window rate limiter capped at 15 requests per minute, a primary-plus-fallback model chain, exponential back-off on HTTP 429 responses, and a self-healing JSON retry (if the model returns markdown-fenced output, a second prompt asks it to re-emit plain JSON). All network I/O runs via `asyncio.get_event_loop().run_in_executor` so the sync `genai` SDK does not block the async event loop. Two custom exceptions signal failures to callers: `GeminiRateLimitError` (all retries and model candidates exhausted) and `GeminiJSONError` (JSON cannot be parsed even after a retry).

### `job_context.py`

Pydantic data model (`JobContext`) that is the structured output of a job-analysis LLM call. Holds seven typed lists: required skills, nice-to-have skills, domain keywords, candidate matches (required skills already on the CV), candidate gaps (required skills absent from the CV), locked fields that must not be edited (`do_not_touch`), and up to three suggested edit targets (`top_changes_hint`). Also contains `to_markdown()`, which serializes the model into a labelled markdown document used as the first half of the CV-modifier prompt.

### `job_analyzer.py`

Single-responsibility class (`JobAnalyzer`) that takes a `JobDetails` object, sanitizes its fields against prompt injection, formats the `JOB_ANALYZER_PROMPT` template, calls `GeminiClient.generate_json`, and returns a validated `JobContext`. This is always the first LLM call in a CV-tailoring pipeline run.

### `cv_modifier.py`

Single-responsibility class (`CVModifier`) that accepts a `JobDetails`, the full CV LaTeX source, and a pre-built `JobContext`. It serializes the context to markdown via `JobContext.to_markdown()`, formats the `CV_MODIFIER_SKILL` prompt with both the context and the raw LaTeX, calls `GeminiClient.generate_json`, and enforces the cap of at most three high-confidence replacements by calling `CVModifierOutput.top_three()` before returning. If the CV text exceeds 50,000 characters it is silently truncated with a warning log before the prompt is built.

### `cv_editor.py`

Higher-level editor class (`CVEditor`) that handles the motivation-letter side of tailoring. It inspects a `LaTeXSections` object for a marker-delimited customizable paragraph, formats the `MOTIVATION_LETTER_PROMPT` template, calls `GeminiClient.generate_json`, and applies a post-generation safety check: if the returned text introduces any LaTeX commands not already present in the original paragraph, the edit is discarded and the original paragraph is returned unchanged. Job description input is capped at 500 characters before prompt formatting.

### `prompts.py`

Defines the three raw prompt template strings as module-level constants. No classes, no logic — pure text. Each template uses Python `.format()` placeholders and wraps untrusted external data (job titles, descriptions, company names) in `<untrusted_data>` XML tags with an explicit instruction to treat the content as data rather than instructions.

### `validators.py`

Pydantic models that represent the expected JSON output shape for each LLM call:
- `LetterEdit` — output of the letter prompt.
- `CVReplacement` — a single surgical edit: section label, exact original substring, replacement text, reason, matched job requirement, and a confidence float. A `field_validator` rejects confidence values outside `[0.0, 1.0]`; `is_applicable()` returns `True` when confidence >= 0.7.
- `CVModifierOutput` — wrapper around a list of `CVReplacement`; `top_three()` filters to applicable replacements and returns the top three sorted by confidence descending.

### `__init__.py`

Empty marker file; exports nothing. Callers import directly from submodules.

---

## Public Interface

### `GeminiClient` (`gemini_client.py`)

```python
class GeminiClient:
    RPM_LIMIT: int = 15

    def __init__(self) -> None
    async def generate_text(self, prompt: str) -> str
    async def generate_json(self, prompt: str, schema: Type[T]) -> T
```

**`generate_text(prompt)`**
- Parameters: `prompt` — plain string sent directly to the model.
- Returns: raw text string from the model response.
- Raises: `GeminiRateLimitError` if all model candidates and retries are exhausted.

**`generate_json(prompt, schema)`**
- Parameters: `prompt` — string; `schema` — a Pydantic `BaseModel` subclass used for validation.
- Returns: a validated instance of `schema`.
- Raises: `GeminiJSONError` after one self-healing retry if the response cannot be parsed; `GeminiRateLimitError` if the underlying `generate_text` call fails.

---

### `JobAnalyzer` (`job_analyzer.py`)

```python
class JobAnalyzer:
    def __init__(self, client: GeminiClient | None = None) -> None
    async def analyze(self, job: JobDetails) -> JobContext
```

**`analyze(job)`**
- Parameters: `job: JobDetails` — scraped job record with `title`, `company`, and `description` fields.
- Sanitizes title to 300 chars, company to 200 chars, description to 2,000 chars before prompt insertion.
- Returns: `JobContext` (validated Pydantic model).

---

### `CVModifier` (`cv_modifier.py`)

```python
class CVModifier:
    def __init__(self, client: GeminiClient | None = None) -> None
    async def modify(
        self,
        job: JobDetails,
        cv_tex: str,
        context: JobContext,
    ) -> CVModifierOutput
```

**`modify(job, cv_tex, context)`**
- Parameters: `job: JobDetails`; `cv_tex: str` — full LaTeX source of the CV; `context: JobContext` — output of `JobAnalyzer.analyze`.
- Truncates `cv_tex` to 50,000 characters if needed.
- Returns: `CVModifierOutput` containing at most 3 `CVReplacement` entries with confidence >= 0.7, sorted by confidence descending.

---

### `CVEditor` (`cv_editor.py`)

```python
class CVEditor:
    MAX_DESCRIPTION_CHARS: int = 500

    def __init__(self, client: GeminiClient | None = None) -> None
    async def edit_letter(
        self,
        job: JobDetails,
        sections: LaTeXSections,
    ) -> Optional[LetterEdit]
```

**`edit_letter(job, sections)`**
- Parameters: `job: JobDetails`; `sections: LaTeXSections` — parsed LaTeX sections object that may contain a `letter_paragraph` field.
- Returns `None` if `sections.letter_paragraph` is absent or empty.
- Returns: `LetterEdit` with the customized paragraph text and the resolved company name, or the original paragraph wrapped in a `LetterEdit` if the safety check rejects the edit.

---

### `JobContext` (`job_context.py`)

```python
class JobContext(BaseModel):
    required_skills: list[str]
    nice_to_have_skills: list[str]
    keywords: list[str]
    candidate_matches: list[str]
    candidate_gaps: list[str]
    do_not_touch: list[str]
    top_changes_hint: list[str]

    def to_markdown(self, job_title: str, company: str) -> str
```

**`to_markdown(job_title, company)`**
- Returns a structured markdown string with labelled sections, used verbatim as the `{job_context_md}` placeholder in `CV_MODIFIER_SKILL`.

---

### Validators (`validators.py`)

```python
class LetterEdit(BaseModel):
    edited_paragraph: str
    company_name: str

class CVReplacement(BaseModel):
    section: Literal["Profile", "Experience", "Skills", "Additional Information"]
    original_text: str
    replacement_text: str
    reason: str
    job_requirement_matched: str
    confidence: float          # validated: must be in [0.0, 1.0]

    def is_applicable(self) -> bool   # True when confidence >= 0.7

class CVModifierOutput(BaseModel):
    replacements: list[CVReplacement] = []

    def top_three(self) -> list[CVReplacement]
    # Filters to applicable replacements, sorts by confidence desc, returns first 3
```

---

### Prompt Templates (`prompts.py`)

**`MOTIVATION_LETTER_PROMPT`**
- Purpose: instruct Gemini to edit only the marker-delimited customizable paragraph of a motivation letter, replacing the `{{company_name}}` placeholder with the real company and referencing 1–2 specific aspects of the role.
- Key inputs: `{job_title}`, `{company}`, `{job_description_excerpt}` (max 500 chars, wrapped in `<untrusted_data>`), `{letter_content}` (the full letter skeleton with markers).
- Output shape: `{"edited_paragraph": "...", "company_name": "..."}` — maps to `LetterEdit`.

**`JOB_ANALYZER_PROMPT`**
- Purpose: extract structured skill and keyword data from a job posting and cross-reference it against a hardcoded list of the candidate's known skills to produce match/gap lists and suggested edit targets.
- Key inputs: `{job_title}`, `{company}`, `{job_description}` (max 2,000 chars, wrapped in `<untrusted_data>`).
- Output shape: seven-key JSON object — maps to `JobContext`.

**`CV_MODIFIER_SKILL`**
- Purpose: perform at most three surgical text replacements in a LaTeX CV to improve fit for a specific job, guided by a pre-analyzed job context document. Includes explicit fabrication and injection-resistance rules.
- Key inputs: `{job_context_md}` (markdown from `JobContext.to_markdown()`), `{cv_tex}` (full LaTeX source, max 50,000 chars).
- Output shape: `{"replacements": [...]}` — maps to `CVModifierOutput`.

---

## Data Flow

```
JobDetails (title, company, description)
        │
        ▼
  JobAnalyzer.analyze()
    └─ sanitize fields
    └─ format JOB_ANALYZER_PROMPT
    └─ GeminiClient.generate_json(prompt, JobContext)
        │
        ▼
  JobContext (skills, keywords, matches, gaps, hints)
        │
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
  CVModifier.modify(job, cv_tex, context)        CVEditor.edit_letter(job, sections)
    └─ JobContext.to_markdown()                    └─ format MOTIVATION_LETTER_PROMPT
    └─ format CV_MODIFIER_SKILL                    └─ GeminiClient.generate_json(prompt, LetterEdit)
    └─ GeminiClient.generate_json(...)             └─ safety check (no new LaTeX commands)
    └─ CVModifierOutput.top_three()                     │
        │                                              ▼
        ▼                                        LetterEdit (edited_paragraph, company_name)
  CVModifierOutput
    └─ list[CVReplacement] (≤3, confidence ≥ 0.7)
         each: section, original_text → replacement_text
```

The CV pipeline always runs `JobAnalyzer` first so the resulting `JobContext` can be shared between `CVModifier` (CV body edits) and optionally `CVEditor` (letter paragraph). Both downstream calls receive the same `JobDetails` but operate on different source artefacts (`cv_tex` vs `LaTeXSections`).

---

## Configuration

All configuration is sourced from environment variables or a `.env` file, loaded via `backend/config.py` (Pydantic `BaseSettings`).

| Variable | Type | Default | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | `str` | _(required, no default)_ | Google AI Studio / Vertex API key. Passed to `genai.Client`. |
| `GOOGLE_MODEL` | `str` | `"gemini-3-flash-preview"` | Primary model name sent on every inference call. |
| `GOOGLE_MODEL_FALLBACKS` | `str` | `""` | Comma-separated list of fallback model names tried in order when the primary returns a 404/NOT_FOUND error. Empty string disables fallbacks. |

**Rate limiting:** `GeminiClient` enforces a 15 RPM sliding window in-process using a `collections.deque(maxlen=15)` of call timestamps. The window is 60 seconds. If the window is full, the client sleeps for the remaining window time, capped at 120 seconds per sleep to avoid unbounded blocking. The limit is hardcoded as `GeminiClient.RPM_LIMIT = 15` and matches the free-tier quota for Gemini Flash models.

**Retry behaviour:** On HTTP 429 (rate limit from the API itself), `generate_text` retries up to 2 additional times with exponential back-off: 5 s, then 10 s. On 404/NOT_FOUND the current model candidate is skipped immediately and the next fallback is tried. Other exceptions are wrapped in `GeminiRateLimitError` and re-raised without retry.

**JSON self-healing:** `generate_json` attempts one automatic retry if the initial response is not valid JSON, asking the model to reformat its previous output as plain JSON. Failure after the retry raises `GeminiJSONError`.

---

## Known Limitations / TODOs

**Hardcoded candidate profile in `JOB_ANALYZER_PROMPT`.** The prompt contains a fixed description of the candidate's domain (Food Science / Laboratory) and an explicit enumeration of their known skills (`cell culture techniques, XTT assays, HACCP, GMP, ...`). This is baked into the prompt string in `prompts.py` and must be manually edited to match a different user's CV. There is no runtime substitution from the database.

**CV input truncation is lossy and silent.** `CVModifier.modify` truncates the CV LaTeX source to 50,000 characters with only a `logger.warning`. If a CV is larger than this, sections toward the end are silently dropped and cannot receive edit suggestions.

**Job description truncation in `CVEditor`.** `CVEditor._excerpt` hard-caps the job description at 500 characters before it reaches the letter prompt. Very long or detail-rich job descriptions are silently truncated; there is no smarter summarization step.

**In-process rate limiter is per-process only.** The 15 RPM window is tracked in memory on a single `GeminiClient` instance. If multiple worker processes or multiple `GeminiClient` instances coexist, they do not share the counter and can collectively exceed the API quota.

**Model name mismatch between client and config default.** `config.py` defaults `GOOGLE_MODEL` to `"gemini-3-flash-preview"` while `GeminiClient.__init__` falls back to `"gemini-3.0-flash"` if the settings value is falsy. These two strings refer to different model slugs; the discrepancy means the actual model used can differ from what the config documents if the env var is unset or empty.

**No token-count tracking.** There is no accounting for prompt or response token usage. Costs and quota consumption against the API are invisible at the application level.

**`CVReplacement.section` enum is partially checked.** The `Literal` type in `validators.py` restricts section names to four allowed values, but the prompt (`CV_MODIFIER_SKILL`) does not explicitly enumerate those same four values. A model response using a different section label will fail Pydantic validation and surface as a `GeminiJSONError` rather than a more informative error.

**No persistent retry queue.** All retries are in-memory and within a single request lifecycle. A Gemini outage during a scheduled batch run will cause the entire batch item to fail with no deferred-retry mechanism.
