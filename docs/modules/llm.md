# Module: LLM

## Purpose

The `backend/llm/` module is JobPilot's provider-agnostic interface to the configured LLM provider. A small factory (`factory.py`) selects a concrete provider adapter at runtime — `openai` (the default, via `OpenAICompatClient`/`OpenAICompatEmbeddingClient`) or `anthropic` (via `AnthropicClient`) — and exposes it behind the `LLMClient` and `EmbeddingClient` protocols defined in `base.py`. On top of this it provides three coordinated capabilities: job description analysis (extracting structured skill and keyword data from raw postings), CV tailoring (producing surgical LaTeX text replacements to improve job fit without fabricating content), and motivation-letter customization (adapting a single customizable paragraph to the target role). The module exists to isolate all LLM concerns — API key management, prompt templating, JSON parsing, and output validation — behind clean, typed Python interfaces. The rest of JobPilot (the scheduler, the apply engine, and the CV pipeline) calls into this module through those protocols without knowing which provider is configured.

---

## Key Components

### `base.py`

Defines the shared, provider-neutral contract for the whole module: the `LLMClient` and `EmbeddingClient` protocols (the typed interfaces every adapter implements) and the provider-neutral exceptions `LLMRateLimitError`, `LLMCallFailed`, and `LLMJSONError`. Nothing in `base.py` is tied to a specific vendor SDK; callers depend only on these protocols and exceptions.

### `factory.py`

Selects and constructs the concrete provider adapter based on configuration. The default generation provider is `openai` (valid generation providers: `openai | anthropic`); embeddings always use the `openai` provider. The factory returns objects typed as `LLMClient` / `EmbeddingClient`, so call sites never reference a concrete provider class.

### `providers/openai_compat.py`

The default adapter. `OpenAICompatClient` wraps the OpenAI-compatible SDK for text and JSON generation, and `OpenAICompatEmbeddingClient` wraps it for embeddings. Because it speaks the OpenAI-compatible wire protocol, it can target the OpenAI API directly or any compatible endpoint reachable via a custom `LLM_BASE_URL` (a local model server, or any other deployment exposed through an OpenAI-compatible base URL). The adapter maps HTTP 429 responses to `LLMRateLimitError` and any other failure to `LLMCallFailed`. `generate_json` retries a malformed parse once, then raises `LLMJSONError`.

### `providers/anthropic_client.py`

`AnthropicClient` is the Anthropic adapter for text and JSON generation. Like the OpenAI-compatible adapter it maps 429 responses to `LLMRateLimitError`, other failures to `LLMCallFailed`, and retries a malformed JSON parse once before raising `LLMJSONError`.

### `job_context.py`

Pydantic data model (`JobContext`) that is the structured output of a job-analysis LLM call. Holds seven typed lists: required skills, nice-to-have skills, domain keywords, candidate matches (required skills already on the CV), candidate gaps (required skills absent from the CV), locked fields that must not be edited (`do_not_touch`), and up to three suggested edit targets (`top_changes_hint`). Also contains `to_markdown()`, which serializes the model into a labelled markdown document used as the first half of the CV-modifier prompt.

### `job_analyzer.py`

Single-responsibility class (`JobAnalyzer`) that takes a `JobDetails` object, sanitizes its fields against prompt injection, formats the `JOB_ANALYZER_PROMPT` template, calls the LLM client's `generate_json`, and returns a validated `JobContext`. This is always the first LLM call in a CV-tailoring pipeline run.

### `cv_modifier.py`

Single-responsibility class (`CVModifier`) that accepts a `JobDetails`, the full CV LaTeX source, and a pre-built `JobContext`. It serializes the context to markdown via `JobContext.to_markdown()`, formats the `CV_MODIFIER_SKILL` prompt with both the context and the raw LaTeX, calls the LLM client's `generate_json`, and enforces the cap of at most three high-confidence replacements by calling `CVModifierOutput.top_three()` before returning. If the CV text exceeds 50,000 characters it is silently truncated with a warning log before the prompt is built.

### `cv_editor.py`

Higher-level editor class (`CVEditor`) that handles the motivation-letter side of tailoring. It inspects a `LaTeXSections` object for a marker-delimited customizable paragraph, formats the `MOTIVATION_LETTER_PROMPT` template, calls the LLM client's `generate_json`, and applies a post-generation safety check: if the returned text introduces any LaTeX commands not already present in the original paragraph, the edit is discarded and the original paragraph is returned unchanged. Job description input is capped at 500 characters before prompt formatting.

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

### `LLMClient` protocol (`base.py`)

The interface every generation adapter implements (`OpenAICompatClient`, `AnthropicClient`). Obtain a concrete instance from `factory.py`; never instantiate a provider class directly.

```python
class LLMClient(Protocol):
    async def generate_text(self, prompt: str) -> str
    async def generate_json(self, prompt: str, schema: Type[T]) -> T
```

**`generate_text(prompt)`**
- Parameters: `prompt` — plain string sent directly to the model.
- Returns: raw text string from the model response.
- Raises: `LLMRateLimitError` on a 429 from the provider; `LLMCallFailed` on any other failure.

**`generate_json(prompt, schema)`**
- Parameters: `prompt` — string; `schema` — a Pydantic `BaseModel` subclass used for validation.
- Returns: a validated instance of `schema`.
- Raises: `LLMJSONError` after one retry if the response cannot be parsed; `LLMRateLimitError` / `LLMCallFailed` if the underlying generation call fails.

---

### `JobAnalyzer` (`job_analyzer.py`)

```python
class JobAnalyzer:
    def __init__(self, client: LLMClient | None = None) -> None
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
    def __init__(self, client: LLMClient | None = None) -> None
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

    def __init__(self, client: LLMClient | None = None) -> None
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
- Purpose: instruct the LLM to edit only the marker-delimited customizable paragraph of a motivation letter, replacing the `{{company_name}}` placeholder with the real company and referencing 1–2 specific aspects of the role.
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
    └─ the LLM client's generate_json(prompt, JobContext)
        │
        ▼
  JobContext (skills, keywords, matches, gaps, hints)
        │
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
  CVModifier.modify(job, cv_tex, context)        CVEditor.edit_letter(job, sections)
    └─ JobContext.to_markdown()                    └─ format MOTIVATION_LETTER_PROMPT
    └─ format CV_MODIFIER_SKILL                    └─ the LLM client's generate_json(prompt, LetterEdit)
    └─ the LLM client's generate_json(...)         └─ safety check (no new LaTeX commands)
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
| `LLM_PROVIDER` | `str` | `"openai"` | Generation provider selected by `factory.py`. Valid values: `openai`, `anthropic`. |
| `LLM_API_KEY` | `str` | _(required, no default)_ | API key for the configured provider. Provider-specific keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) are also honoured. Not required when `LLM_BASE_URL` points at an endpoint that needs no key. |
| `LLM_BASE_URL` | `str` | `""` | Optional override for the OpenAI-compatible base URL. Lets the `openai` adapter target a local model server or any OpenAI-compatible endpoint (including a third-party model fronted by an OpenAI-compatible gateway). |
| `LLM_MODEL` | `str` | _(provider default)_ | Model name sent on every generation call. |
| `EMBEDDING_MODEL` | `str` | `"text-embedding-3-small"` | Embedding model used by `OpenAICompatEmbeddingClient`. Embeddings always use the `openai` provider. |
| `LLM_TIMEOUT_SECONDS` | `int` | _(adapter default)_ | Per-call request timeout applied by the provider adapters. |

**Error mapping:** the provider adapters translate transport failures into the module's neutral exceptions. An HTTP 429 from the provider becomes `LLMRateLimitError`; any other failure becomes `LLMCallFailed`. There is no in-process request-per-minute limiter and no multi-model fallback chain — both were provider-specific features that no longer exist.

**JSON retry:** `generate_json` attempts one automatic retry if the initial response is not valid JSON, asking the model to reformat its previous output as plain JSON. Failure after the retry raises `LLMJSONError`.

---

## Known Limitations / TODOs

**Hardcoded candidate profile in `JOB_ANALYZER_PROMPT`.** The prompt contains a fixed description of the candidate's domain (Food Science / Laboratory) and an explicit enumeration of their known skills (`cell culture techniques, XTT assays, HACCP, GMP, ...`). This is baked into the prompt string in `prompts.py` and must be manually edited to match a different user's CV. There is no runtime substitution from the database.

**CV input truncation is lossy and silent.** `CVModifier.modify` truncates the CV LaTeX source to 50,000 characters with only a `logger.warning`. If a CV is larger than this, sections toward the end are silently dropped and cannot receive edit suggestions.

**Job description truncation in `CVEditor`.** `CVEditor._excerpt` hard-caps the job description at 500 characters before it reaches the letter prompt. Very long or detail-rich job descriptions are silently truncated; there is no smarter summarization step.

**No cross-process rate limiting.** The adapters rely on the provider's own server-side quota and surface 429s as `LLMRateLimitError`; there is no application-level request budget shared across worker processes.

**No token-count tracking.** There is no accounting for prompt or response token usage. Costs and quota consumption against the API are invisible at the application level.

**`CVReplacement.section` enum is partially checked.** The `Literal` type in `validators.py` restricts section names to four allowed values, but the prompt (`CV_MODIFIER_SKILL`) does not explicitly enumerate those same four values. A model response using a different section label will fail Pydantic validation and surface as a `LLMJSONError` rather than a more informative error.

**No persistent retry queue.** All retries are in-memory and within a single request lifecycle. An LLM provider outage during a scheduled batch run will cause the entire batch item to fail with no deferred-retry mechanism.
