# Module: Matching

Cross-references: [file-map.md](../file-map.md) | [architecture.md](../architecture.md) | [index.md](../index.md)

Files in this module:

| File | Brief |
|---|---|
| `backend/matching/matcher.py` | `JobMatcher` — weighted multi-factor job relevance scoring |
| `backend/matching/fit_engine.py` | `FitEngine` — embedding-based skill-gap assessment |
| `backend/matching/cv_parser.py` | `CVParser` — LaTeX CV skill extraction with context weights |
| `backend/matching/embedder.py` | `Embedder` — populates `SkillEntry.embedding` vectors |
| `backend/matching/skill_patterns.py` | `TECH_PATTERN` — compiled regex for tech skill extraction |
| `backend/matching/filters.py` | `JobFilters` dataclass for search parameter passing |

---

## JobMatcher

**File:** `backend/matching/matcher.py`

Computes a composite relevance score [0, 100] for each scraped job against the user's search settings. Called by `MorningBatchRunner` after scraping and before CV pre-generation.

### Weighted Sub-Scores

| Component | Weight | Description |
|---|---|---|
| Keyword match | 40% | Fraction of `SearchSettings.keywords.include` found in title + description |
| Location match | 20% | Jaccard similarity between job location and `SearchSettings.locations` |
| Experience match | 15% | Whether the job's stated experience falls within `experience_min`/`experience_max` |
| Salary match | 10% | Whether `job.salary_min >= SearchSettings.salary_min` |
| Recency | 10% | Linear decay: 100 → 0 over `0 → 30 days` since `posted_at` |

### Instant Zero

A job scores `0.0` immediately (bypassing all sub-score computation) if:

- Any term from `SearchSettings.excluded_keywords` appears in the title or description.
- The company name appears in `SearchSettings.excluded_companies`.

### `_recency_score(posted_at)`

```
score = max(0, 1 - (days_old / 30)) * 100
```

Jobs older than 30 days score 0. Jobs posted today score 100.

### Persistence

Matched jobs are inserted as `JobMatch` rows with `status="new"`. Existing matches for the same `job_id` are updated in-place to avoid duplicating the queue.

---

## FitEngine

**File:** `backend/matching/fit_engine.py`

Performs a deeper, embedding-based assessment of how well the user's CV matches a specific job's requirements. Used inside `CVPipeline` to decide whether to tailor the CV and to guide the `CVModifier`.

### Flow

1. Parse the user's CV into `SkillEntry` list via `CVParser.build_profile()`.
2. Embed all skill texts in batch via `GeminiClient.embed()` (using `text-embedding-004`).
3. Embed the job's `required_skills` and `nice_to_have_skills` from `JobContext`.
4. Compute cosine similarity between each job requirement and all CV skills.
5. For each requirement, take the max cosine similarity across all CV skills.
6. Compute `ats_score` as the weighted average (required skills weight more than nice-to-have).

### Sensitivity Modes

`SearchSettings.cv_modification_sensitivity` maps to thresholds:

| Mode | Apply threshold | Description |
|---|---|---|
| `conservative` | 0.75 | Only apply when there is a strong fit signal |
| `balanced` | 0.60 | Default; apply when fit is reasonable |
| `aggressive` | 0.45 | Apply even on weaker signals |

### `FitAssessment`

```python
@dataclass
class FitAssessment:
    ats_score: float          # 0–100 compatibility score
    gap_severity: float       # 0.0–1.0; higher means more missing skills
    gaps: list[SkillGap]      # per-requirement gap analysis
    covered: list[str]        # requirement strings the CV already covers
    decision: str             # "apply" | "skip" | "apply_with_tailoring"
```

`FitAssessment.to_dict()` serialises to a JSON-compatible dict stored in `JobMatch.fit_assessment_json` and `TailoredDocument.fit_assessment_json`.

### `SkillGap`

```python
@dataclass
class SkillGap:
    requirement: str    # the job requirement text
    best_match: str     # closest CV skill found
    similarity: float   # cosine similarity score [0, 1]
    severity: float     # 1 - similarity; how significant the gap is
```

---

## CVParser

**File:** `backend/matching/cv_parser.py`

Extracts skills from a LaTeX CV source string with context-based weighting. Produces a `CVProfile` used by `FitEngine` and `Embedder`.

### Context Weights

```python
CONTEXT_WEIGHTS = {
    "experience_recent": 1.0,   # from most recent / current role bullets
    "skills_section":    0.6,   # from the Skills or Technical Skills section
    "profile":           0.5,   # from the Profile / Summary section
    "experience_older":  0.4,   # from older role bullets
}
```

These weights mirror the signal strength used by real Applicant Tracking Systems (ATS).

### Three-Pass Extraction

1. **Profile section:** Scans for `\begin{rSection}{Profile}` (and equivalent names). Checks for multi-word skills first, then runs `TECH_PATTERN` across the section text.
2. **Skills section:** Tries `\cvskill{}{skills}` pattern first, then falls back to table-row pattern (`& skill1, skill2 \\`).
3. **Experience section:** Detects role headers with `_ROLE_RE` to distinguish recent (first / "Present") from older roles. Extracts skill mentions from `\item` bullets in each role.

### Fallback

If fewer than 3 skills are extracted via the section passes, a full-text scan is performed using `TECH_PATTERN` and `_MULTI_WORD_SKILLS`. All fallback skills receive `skills_section` context weight.

### `build_profile(cv_tex)`

Calls `parse()` and wraps the result in a `CVProfile`:

```python
@dataclass
class CVProfile:
    skills: list[SkillEntry]
    raw_text_hash: str  # SHA-256 of the original LaTeX source (for cache invalidation)
```

---

## Embedder

**File:** `backend/matching/embedder.py`

Post-processes a `CVProfile` by populating the `embedding` field of each `SkillEntry`. Called lazily by `FitEngine` before cosine-similarity computation.

```python
async def embed_profile(profile: CVProfile, gemini_client: GeminiClient) -> CVProfile:
    texts = [s.text for s in profile.skills]
    vectors = await gemini_client.embed(texts)
    for skill, vector in zip(profile.skills, vectors):
        skill.embedding = vector
    return profile
```

---

## skill_patterns.py

**File:** `backend/matching/skill_patterns.py`

Defines `TECH_PATTERN`: a compiled regex that matches technology skill mentions in plain or LaTeX text. The pattern covers:

- Exact-match keywords (Python, Go, Rust, Java, etc.)
- Framework/library names (FastAPI, React, PyTorch, etc.)
- Cloud and DevOps terms (AWS, GCP, Docker, Kubernetes, etc.)
- Database names (PostgreSQL, MongoDB, Redis, etc.)
- Methodology terms (Agile, Scrum, CI/CD, etc.)

The pattern uses word boundaries (`\b`) and case-insensitive matching. It returns `match.group(1)` for the extracted skill text.

---

## JobFilters

**File:** `backend/matching/filters.py`

Simple dataclass passed between `SearchSettings` parsing and Adzuna/scraper queries:

```python
@dataclass
class JobFilters:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    salary_min: Optional[int] = None
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    remote_only: bool = False
    job_types: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    min_score: float = 30.0
```
