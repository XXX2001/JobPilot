# Module: LaTeX

## Purpose

The `backend/latex` module is JobPilot's CV tailoring pipeline. It takes a user's base LaTeX CV (or motivation letter) template, coordinates LLM-generated edits, applies those edits safely back into the LaTeX source, compiles the result to PDF using Tectonic, and returns structured artifacts (file paths, diff entries) for storage and display. The module exists to fully automate the production of job-specific, personalized application documents without ever mutating the user's canonical base template. Its role within JobPilot sits between the LLM layer (which produces edit suggestions) and the apply layer (which submits the final PDF to job platforms).

---

## Key Components

### `pipeline.py`

The top-level orchestrator. Exposes `CVPipeline` and `LetterPipeline`, two classes that wire together every other piece of the module and the LLM layer into a single async call per document type. Also defines the result dataclasses (`TailoredCV`, `TailoredLetter`, `DiffEntry`) and the `generate_diff` helper for constructing structured change records from legacy editor outputs.

### `applicator.py`

Houses `CVApplicator`, the safety gate that validates and applies LLM-proposed text replacements. It enforces three per-replacement checks (confidence threshold, verbatim match in the current source, no new LaTeX commands introduced) and a hard cap of three applied replacements per run, selected by highest confidence.

### `compiler.py`

Wraps the external Tectonic binary in an async subprocess interface. `LaTeXCompiler` discovers the Tectonic binary (PATH first, then a project-local `bin/` directory), runs it with `asyncio.create_subprocess_exec`, and returns the path to the output PDF. Raises `LaTeXCompilationError` on any failure.

### `injector.py`

Provides `LaTeXInjector`, the marker-based text replacement engine used by `LetterPipeline`. It finds content delimited by `% --- JOBPILOT:<MARKER>:START ---` / `% --- JOBPILOT:<MARKER>:END ---` comment pairs and swaps the content between them. Used for the legacy marker-based letter editing flow; the CV now uses the marker-free `CVApplicator` approach instead.

### `parser.py`

Contains `LaTeXParser`, which extracts structured, editable sections from a `.tex` file. It first tries the JOBPILOT comment-marker convention; if no markers are found it falls back to a TexSoup-based heuristic that attempts to locate a `\section{Summary}` block. Also exposes `validate_markers` for detecting mismatched START/END pairs.

### `validator.py`

`LaTeXValidator` provides two-tier validation of a compiled `.tex` file: a full Tectonic dry-run compilation into a temp directory (most reliable), and a regex-based heuristic fallback (checks `\begin`/`\end` environment balance, presence of `\documentclass`, presence of `\begin{document}`) used when Tectonic is not installed.

### `__init__.py`

Empty package marker (one comment line). No public re-exports.

---

## Public Interface

### `pipeline.py`

#### `class TailoredCV`

```python
@dataclass
class TailoredCV:
    job_id: int | None
    tex_path: Path
    pdf_path: Path
    diff: list[DiffEntry]
    cv_tailored: bool = True
```

Result object returned by `CVPipeline.generate_tailored_cv`. `cv_tailored` is `False` when the LLM editing step failed and the unmodified base CV was compiled instead.

#### `class TailoredLetter`

```python
@dataclass
class TailoredLetter:
    job_id: int | None
    tex_path: Path
    pdf_path: Path
```

Result object returned by `LetterPipeline.generate_tailored_letter`.

#### `class DiffEntry`

```python
@dataclass
class DiffEntry:
    section: str
    original_text: str
    edited_text: str
    change_description: str
```

One record of a change made to a CV or letter. `section` is a free-form string (e.g. `"summary"`, `"experience"`, `"letter"`).

#### `class CVPipeline`

```python
def __init__(
    self,
    compiler: LaTeXCompiler | None = None,
    job_analyzer=None,   # backend.llm.job_analyzer.JobAnalyzer
    cv_modifier=None,    # backend.llm.cv_modifier.CVModifier
    cv_applicator=None,  # backend.latex.applicator.CVApplicator
) -> None
```

All constructor parameters are optional; `compiler` defaults to a freshly constructed `LaTeXCompiler`. When `job_analyzer`, `cv_modifier`, or `cv_applicator` is `None`, the LLM editing step is skipped entirely and the base CV is compiled as-is.

```python
async def generate_tailored_cv(
    self,
    base_cv_path: Path,
    job: JobDetails,
    output_dir: Path,
) -> TailoredCV
```

- Copies `base_cv_path` (and any `.cls`, `.sty`, `.jpg`, `.jpeg`, `.png`, `.pdf`, `.eps` sibling files) into `output_dir`.
- Runs `job_analyzer.analyze(job)` (result cached per `job.id` with a 1-hour TTL, max 100 entries).
- Runs `cv_modifier.modify(job, cv_tex, context)` to get a `CVModifierOutput`.
- Runs `cv_applicator.apply(cv_tex, replacements)` to apply validated replacements.
- Compiles the resulting `.tex` with Tectonic.
- Returns a `TailoredCV`.

On `GeminiRateLimitError`, `GeminiJSONError`, or any unexpected exception from the LLM step, logs a warning/error and falls back to compiling the unmodified base CV.

#### `class LetterPipeline`

```python
def __init__(
    self,
    compiler: LaTeXCompiler | None = None,
    parser: LaTeXParser | None = None,
    injector: LaTeXInjector | None = None,
    cv_editor=None,
) -> None
```

```python
async def generate_tailored_letter(
    self,
    base_letter_path: Path,
    job: JobDetails,
    output_dir: Path,
) -> TailoredLetter
```

- Copies `base_letter_path` and its support files into `output_dir`.
- Parses the `.tex` with `LaTeXParser.extract_sections`.
- If `cv_editor` is provided and the template has JOBPILOT markers, calls `cv_editor.edit_letter(job, sections)` and injects the result via `LaTeXInjector.inject_letter_edit`.
- Compiles to PDF with Tectonic.
- Returns a `TailoredLetter`.

On LLM errors, silently falls back to the unmodified base letter.

#### `generate_diff`

```python
def generate_diff(
    original_sections,   # LaTeXSections
    edits,               # tuple/list of (CVSummaryEdit | None, CVExperienceEdit | None, LetterEdit | None)
) -> list[DiffEntry]
```

Legacy helper. Produces `DiffEntry` records from the older three-part edit tuple format (summary edit, experience edit, letter edit). Currently not called by `CVPipeline` (which builds its diff directly from `CVReplacement` objects), but retained for the letter flow.

---

### `applicator.py`

#### `class CVApplicator`

```python
class CVApplicator:
    MAX_REPLACEMENTS: int = 3
```

```python
def apply(
    self,
    cv_tex: str,
    replacements: list[CVReplacement],
) -> tuple[str, list[CVReplacement]]
```

- Sorts `replacements` by `confidence` descending, keeps the top `MAX_REPLACEMENTS`.
- For each candidate, skips if:
  - `r.confidence < 0.7` (i.e. `r.is_applicable()` is `False`)
  - `r.original_text` is not a verbatim substring of the current working text
  - `r.replacement_text` introduces LaTeX commands not present in `r.original_text`
- Applies surviving replacements with `str.replace(..., 1)` (first occurrence only).
- Returns `(modified_tex, applied_replacements)`. If nothing is applied, `modified_tex` equals the input unchanged.

#### `CVReplacement` (from `backend.llm.validators`)

```python
class CVReplacement(BaseModel):
    section: Literal["Profile", "Experience", "Skills", "Additional Information"]
    original_text: str
    replacement_text: str
    reason: str
    job_requirement_matched: str
    confidence: float   # 0.0–1.0; clamped by validator

    def is_applicable(self) -> bool: ...  # returns confidence >= 0.7
```

---

### `compiler.py`

#### `class LaTeXCompilationError`

```python
class LaTeXCompilationError(Exception): ...
```

Raised when Tectonic is not found on the system or when compilation exits with a non-zero return code.

#### `class LaTeXCompiler`

```python
def _find_tectonic(self) -> str | None
```

Searches for the `tectonic` binary. Priority order:
1. System `PATH` via `shutil.which`.
2. `<project_root>/bin/tectonic[.exe]`, found by walking up the directory tree from `compiler.py` until a parent containing `pyproject.toml` is reached.

Returns `None` if not found.

```python
async def compile(self, tex_path: Path, output_dir: Path | None = None) -> Path
```

- Parameters:
  - `tex_path`: Path to the `.tex` source file.
  - `output_dir`: Destination directory for the PDF. Defaults to `tex_path.parent`.
- Returns: `Path` to the compiled `<stem>.pdf` file.
- Raises: `LaTeXCompilationError` if Tectonic is missing, exits non-zero, or the expected PDF is absent after a nominally successful run.

---

### `injector.py`

#### `class LaTeXInjector`

```python
def inject_summary_edit(self, original_tex: str, new_summary: str) -> str
```

Replaces the content between `% --- JOBPILOT:SUMMARY:START ---` and `% --- JOBPILOT:SUMMARY:END ---` with `new_summary`. Returns a modified copy; never mutates the input. Raises `ValueError` if the marker is absent.

```python
def inject_experience_edits(self, original_tex: str, edits: list) -> str
```

Iterates `edits` (objects with `.index` and `.edited` attributes). For each, re-scans the current `\item` list via regex and replaces the bullet at position `edit.index` with `\item {edit.edited}`. Returns modified copy.

```python
def inject_letter_edit(self, original_tex: str, new_paragraph: str, company_name: str) -> str
```

1. Replaces content between `% --- JOBPILOT:LETTER:PARA:START ---` / `END` markers with `new_paragraph`.
2. Substitutes all literal `{company_name}` occurrences in the result.

Returns the modified string. Raises `ValueError` if the `LETTER:PARA` marker is absent.

---

### `parser.py`

#### `LaTeXSections`

```python
@dataclass
class LaTeXSections:
    summary: Optional[str] = None
    experience_block: Optional[str] = None
    experience_bullets: list[str] = field(default_factory=list)
    letter_paragraph: Optional[str] = None
    has_markers: bool = False
```

Structured representation of the editable regions found in a `.tex` file.

#### `class LaTeXParser`

```python
def extract_sections(self, tex_content: str) -> LaTeXSections
```

- Searches for all `JOBPILOT:<NAME>:START` / `END` marker pairs.
- If found, populates `summary`, `experience_block`, `experience_bullets`, and `letter_paragraph` from the `SUMMARY`, `EXPERIENCE`, and `LETTER:PARA` markers respectively. Sets `has_markers = True`.
- If no markers found, logs a warning and attempts a TexSoup fallback to locate `\section{Summary}` content. Sets `has_markers = False`.

```python
def extract_bullets(self, block: str) -> list[str]
```

Parses `\item` lines from a LaTeX itemize block using regex. Returns a list of stripped bullet text strings (the `\item` prefix is excluded).

```python
def validate_markers(self, tex_content: str) -> list[str]
```

Finds all START and END marker names independently and returns warning strings for any name that appears in one set but not the other (mismatched or orphaned markers). Returns an empty list if all markers are balanced.

---

### `validator.py`

#### `class LaTeXValidator`

```python
def __init__(self, compiler: LaTeXCompiler | None = None) -> None
```

`compiler` defaults to a freshly constructed `LaTeXCompiler`.

```python
async def validate(self, tex_path: Path) -> list[str]
```

Returns a list of warning/error strings. An empty list means the file passed validation. Strategy:
1. Attempts `_validate_via_tectonic` (full compilation into a temp directory with `--keep-logs`).
2. If Tectonic is not found, falls back to `_heuristic_validate`.
3. If Tectonic is found but compilation fails, returns the stderr output as a single-item list.

```python
async def _validate_via_tectonic(self, tex_path: Path) -> list[str]
```

Runs Tectonic with `--outdir <tempdir> --keep-logs`. Returns `[]` on success, or a list containing the decoded stderr on failure.

```python
def _heuristic_validate(self, tex_path: Path) -> list[str]
```

Regex-based checks (no Tectonic required):
- Counts `\begin{env}` vs `\end{env}` occurrences for each environment name; warns on mismatches.
- Warns if `\documentclass` is missing.
- Warns if `\begin{document}` is missing.

---

## Data Flow

The CV tailoring pipeline proceeds as follows:

```
base_cv.tex  ──copy──►  output_dir/cv.tex
                                │
                       [support files .cls/.sty/images also copied]
                                │
                    ┌───────────▼───────────┐
                    │  JobAnalyzer.analyze  │  → JobContext (cached 1 hr per job_id)
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │  CVModifier.modify    │  → CVModifierOutput
                    │  (LLM: Gemini)        │    .replacements: list[CVReplacement]
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │  CVApplicator.apply   │  Validates each CVReplacement:
                    │                       │  1. confidence ≥ 0.7
                    │                       │  2. original_text verbatim in tex
                    │                       │  3. no new LaTeX commands
                    │                       │  4. cap at 3 replacements (highest confidence)
                    └───────────┬───────────┘
                                │ (modified tex string)
                    ┌───────────▼───────────┐
                    │  dest_tex.write_text  │  Write modified source back to output_dir/cv.tex
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │  LaTeXCompiler        │  tectonic --outdir <output_dir> cv.tex
                    │  (Tectonic)           │
                    └───────────┬───────────┘
                                │
                         TailoredCV
                    ┌─────────────────────┐
                    │ job_id              │
                    │ tex_path            │
                    │ pdf_path            │
                    │ diff: [DiffEntry]   │
                    │ cv_tailored: bool   │
                    └─────────────────────┘
```

The letter pipeline follows a parallel but simpler path using the marker-based `LaTeXParser` + `LaTeXInjector` pair (instead of `CVApplicator`), and returns a `TailoredLetter`.

On any LLM failure (`GeminiRateLimitError`, `GeminiJSONError`, or unexpected exception) at the analyze/modify/apply steps, the pipeline logs the error and falls back to compiling the unmodified base file. `cv_tailored` is set to `False` in the returned `TailoredCV`.

`LaTeXValidator` is not called inline by the pipeline classes; it is available as a standalone utility for pre-flight checks.

---

## Configuration

This module reads no environment variables directly. It depends on the following runtime conditions and conventions:

| Item | Details |
|---|---|
| `tectonic` binary | Must be on `PATH` or at `<project_root>/bin/tectonic[.exe]`. Install via `python scripts/install.sh`. |
| Base CV path | Supplied by the caller (e.g. from `Settings.cv_path` in `backend/api/settings.py`). |
| Output directory | Supplied by the caller per job run; typically a job-scoped subdirectory under a configured output root. |
| `TexSoup` package | Optional Python dependency. Used only as a fallback in `LaTeXParser.extract_sections` when no JOBPILOT markers are present. Silently skipped if not installed. |
| LLM components | `JobAnalyzer`, `CVModifier`, and `CVApplicator` are injected into `CVPipeline` by the caller. When any are absent, the LLM editing step is bypassed entirely. |

---

## Known Limitations / TODOs

- **`CVApplicator.MAX_REPLACEMENTS` is hardcoded to 3.** There is no configuration knob to raise or lower this cap at runtime.

- **Confidence threshold is hardcoded at 0.7** in both `CVReplacement.is_applicable()` and the `CVApplicator.apply` log message. It is not configurable via settings.

- **`inject_experience_edits` rescans `\item` lines from scratch for each edit**, making it O(n × edits) and sensitive to regex ordering. If two edits target adjacent bullets, the index lookup may shift after the first replacement.

- **`LaTeXParser` TexSoup fallback silently swallows all exceptions** (`except Exception: pass`), so parse failures in non-marker templates produce no diagnostic output.

- **`generate_diff`** (the legacy three-tuple helper) is only used by the letter flow and is untested against the newer `CVReplacement`-based diff path. It will silently produce no diff if `edits` has a length other than 3.

- **`LaTeXInjector` replaces only the first occurrence** of `{company_name}` in the letter template implicitly via `str.replace` (which replaces all occurrences). If `{company_name}` appears in multiple places, all instances are replaced — this may or may not be intentional.

- **`LaTeXCompiler._find_tectonic` walks up the full directory tree** looking for `pyproject.toml` but does not actually check for `pyproject.toml`; it walks `here.parents` unconditionally, stopping only when it finds a `bin/tectonic` candidate or exhausts all parents.

- **No timeout on Tectonic compilation.** A runaway or deadlocked Tectonic process will block the event loop's subprocess indefinitely.

- **`LaTeXValidator` is not integrated into the `CVPipeline`** flow. Validation must be triggered separately by callers; there is no automatic post-edit validation before PDF delivery.

- **Job context cache in `CVPipeline`** uses a simple insertion-order eviction strategy (deletes the first key via `next(iter(...))`) when the 100-entry cap is hit. This is not true LRU eviction; frequently used entries near the front of insertion order will be evicted before stale entries added later.
