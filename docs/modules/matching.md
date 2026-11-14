# Module: Matching

Cross-references: [file-map.md](../file-map.md) | [architecture.md](../architecture.md) | [index.md](../index.md)

Files in this module:

| File | Brief |
|---|---|
| `backend/matching/matcher.py` | `JobMatcher` — weighted multi-factor job relevance scoring |
| `backend/matching/fit_engine.py` | `FitEngine` — gazetteer-coverage skill-gap assessment |
| `backend/matching/cv_parser.py` | `CVParser` — the gazetteer skills of a LaTeX CV, with context weights |
| `backend/matching/gazetteer.py` | `Gazetteer` — curated skill list (`data/skills.txt`), word-boundary extraction |
| `backend/matching/job_skills.py` | `extract_job_skills` — a posting's skills with a criticality each |
| `backend/matching/scorer.py` | `score_job` — hard filters, coverage, title factor |
| `backend/matching/filters.py` | `JobFilters` dataclass for search parameter passing |

---

## JobMatcher

**File:** `backend/matching/matcher.py`

Computes a composite relevance score [0, 100] for each scraped job against the user's search settings. Called by `BatchRunner` after scraping and before CV pre-generation.

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

Decides whether a job warrants a tailored CV, from the gazetteer coverage of the posting's skills by the CV. No embeddings, no LLM. Called by `BatchRunner._assess_one`; the result guides `CVModifier.modify_from_assessment`.

```python
FitEngine().assess(job_skills, cv_profile, gaz, sensitivity="balanced") -> FitAssessment
```

`job_skills` is `extract_job_skills(title, description, gaz)` (`job_skills.py`): canonical skill → criticality. Details in [matching.md](../matching.md).

### Sensitivity Modes

`SearchSettings.cv_modification_sensitivity` maps to a gap-severity threshold; the CV is modified when `severity >= threshold`:

| Mode | Threshold |
|---|---|
| `conservative` | 0.3 |
| `balanced` | 0.5 |
| `aggressive` | 0.7 |

### `FitAssessment`

```python
@dataclass
class FitAssessment:
    severity: float               # 1 - coverage
    should_modify: bool
    simulated_ats_score: float    # 100 * coverage
    covered_skills: list[str]     # on the CV, then same-family
    partial_matches: list[str]    # the same-family ones
    critical_gaps: list[SkillGap]
    preferred_gaps: list[SkillGap]  # nice-to-have only
```

`FitAssessment.to_dict()` serialises to a JSON-compatible dict stored in `JobMatch.fit_assessment_json`.

### `SkillGap`

```python
@dataclass
class SkillGap:
    skill: str          # canonical gazetteer name, never posting text
    criticality: float  # 1.0 required, 0.6 neutral, 0.4 blurb, 0.3 nice-to-have
```

---

## CVParser

**File:** `backend/matching/cv_parser.py`

Lists the gazetteer skills a LaTeX CV names, each once, tagged with the strongest context naming it. Produces the `CVProfile` used by `FitEngine`.

### Context Weights

```python
CONTEXT_WEIGHTS = {
    "experience_recent": 1.0,   # first role of the experience section
    "skills_section":    0.6,   # any other text (skills table, unknown template)
    "profile":           0.5,   # Profile / Summary section
    "experience_older":  0.4,   # later roles
}
```

### `build_profile(cv_tex, gaz)`

Calls `parse()` and wraps the result in a `CVProfile`:

```python
@dataclass
class CVProfile:
    skills: list[SkillEntry]   # SkillEntry(text, context, weight)
    raw_text_hash: str         # SHA-256 of the original LaTeX source
```

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
