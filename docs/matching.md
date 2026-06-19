# Matching & fit scoring

How JobPilot turns a scraped job + your CV into a numeric fit score, using regex skill extraction, provider-agnostic embeddings, and weighted cosine-similarity gap analysis.

The `backend/matching/` package contains two distinct scoring layers:

| Layer | Entry point | Inputs | Output | Used for |
| --- | --- | --- | --- |
| **Relevance ranking** | `JobMatcher.score` (`backend/matching/matcher.py:17`) | `JobDetails` + `JobFilters` | `0–100` heuristic score | Filtering/ranking scraped jobs before deeper analysis |
| **Fit / gap analysis** | `FitEngine.assess` (`backend/matching/fit_engine.py:82`) | `JobProfile` + `CVProfile` | `FitAssessment` (severity, ATS score, gaps) | Deciding whether to tailor the CV per job |

The relevance layer is cheap string matching; the fit layer is semantic and requires embeddings. Both are orchestrated by the scheduler (`backend/scheduler/batch_runner.py`).

---

## 1. Relevance ranking (`matcher.py` + `filters.py`)

`JobMatcher.score` (`backend/matching/matcher.py:17`) returns a `0–100` relevance score against a `JobFilters` config. Two conditions are instant disqualifiers (return `0.0`): any excluded keyword found in title/description (`_has_excluded_terms`, line 90), or a company on the blacklist (line 33). Otherwise a weighted sum is computed:

| Signal | Weight | Helper |
| --- | --- | --- |
| Keyword overlap on description | 40% | `_keyword_match` (line 82) |
| Location match | 20% | `_location_match` (line 95) |
| Experience level | 15% | `_experience_match` (line 106) |
| Salary | 10% | `_salary_match` (line 123) |
| Recency (linear decay to 0 at 30 days) | 10% | `_recency_score` (line 135) |

`rank_and_filter` (line 56) scores a list of jobs, drops anything below `min_score` (default `MIN_MATCH_SCORE = 30.0`, `backend/defaults.py:21`), and returns them sorted descending. `matched_keywords` (line 67) is a read-only mirror of the keyword overlap for display.

`JobFilters` (`backend/matching/filters.py:9`) is a plain dataclass holding `keywords`, `excluded_keywords`, `locations`, `salary_min`, `experience_range`, `remote_only`, `excluded_companies`, etc.

> Note: these helpers are heuristic. `_experience_match` regex-scrapes `"5+ years"` mentions from the description; `_location_match` does substring checks. No embeddings are involved here.

---

## 2. CV parsing → `CVProfile` (`cv_parser.py`)

`CVParser.build_profile` (`backend/matching/cv_parser.py:116`) takes raw LaTeX CV source and returns a `CVProfile` (line 72), which is a list of `SkillEntry` plus a `raw_text_hash` (SHA-256 of the source, used for caching).

`parse` (line 81) splits the LaTeX into sections via `_SECTION_RE` and extracts skills from three context buckets, each tagged with an ATS-style **context weight** (`CONTEXT_WEIGHTS`, line 15):

| Context | Weight | Source |
| --- | --- | --- |
| `experience_recent` | 1.0 | First role / role marked "Present" |
| `skills_section` | 0.6 | `\cvskill{}{}` or table rows |
| `profile` | 0.5 | Profile/Summary section |
| `experience_older` | 0.4 | Earlier roles |

Each `SkillEntry` (line 64) carries `text`, `context`, `weight`, and an initially empty `embedding`. Extraction uses `TECH_PATTERN` (from `skill_patterns.py`) plus a hardcoded `_MULTI_WORD_SKILLS` set ("machine learning", "ci/cd", …) and a `_STOP_WORDS` filter. If fewer than 3 skills are found, `parse` logs a warning and falls back to `_fallback_extract` (line 211), a full-text scan.

---

## 3. Job parsing → `JobProfile` (`job_skill_extractor.py`)

`JobSkillExtractor.extract` (`backend/matching/job_skill_extractor.py:55`) turns a job description string into a `JobProfile` (line 46): a list of `JobSkill` plus `knockout_filters`.

The pipeline:

1. **Knockouts** — `KNOCKOUT_PATTERN` (`skill_patterns.py:55`) captures hard requirements like `"5+ years experience"` or `"PhD in"`.
2. **Section split** — `_split_sections` (line 76) detects headers and classifies each block as `required` / `preferred` / `neutral` via `classify_section` (`skill_patterns.py:61`).
3. **Per-block extraction** — `_extract_from_block` (line 99) assigns a base **criticality** from the section type (`required`=1.0, `preferred`=0.5, `neutral`=0.3), then raises it per-line if a linguistic modifier is present (`extract_linguistic_modifier`: "must/essential" → 1.0, "bonus/nice to have" → 0.3). Skills are pulled from `SKILL_PHRASE_PATTERNS` ("experience with X") and `TECH_PATTERN`, de-duplicated via a `seen` set and validated by `_is_valid_skill` (line 141).

Each `JobSkill` (line 38) holds `text`, `criticality` (the weight that matters for scoring), `section`, and an empty `embedding`.

---

## 4. Embedding both profiles (`embedder.py`)

`Embedder` (`backend/matching/embedder.py:13`) is a thin wrapper over **any** object implementing the `EmbeddingClient` protocol (`backend/llm/base.py:48`). It does not know or care which provider is behind it.

- `embed_cv_profile` (line 19) and `embed_job_profile` (line 39) collect skills whose `embedding` is still empty, send their `text` values as one batch to `client.embed(texts)`, and write the returned vectors back onto each `SkillEntry`/`JobSkill` in order (`zip`). Skills that already have an embedding are skipped — so a `CVProfile` is embedded once and reused across many jobs in a run.

The concrete client comes from `make_embedding_client()` (`backend/llm/factory.py:25`), selected by the `EMBEDDING_PROVIDER` setting.

### Provider choice determines vector dimension

The `EmbeddingClient` protocol exposes a `dimension` property (`backend/llm/base.py:55`). Each provider returns a different fixed size:

| `EMBEDDING_PROVIDER` | Model | `dimension` | Source |
| --- | --- | --- | --- |
| `openai` (default) | `text-embedding-3-small` | **1536** | `openai_compat.py:19,87` |
| `openai` | `text-embedding-3-large` | **3072** | `openai_compat.py:19` |
| `anthropic` | — | n/a (rejected) | `factory.py:35` |

Anthropic has no embeddings API, so `make_embedding_client()` raises `ValueError` for `EMBEDDING_PROVIDER=anthropic`.

**Critical consequence:** the CV profile and the job profile **must be embedded by the same provider**. A 768-dim CV vector and a 1536-dim job vector cannot be compared — see the dimension guard below. Switching providers mid-stream (or comparing cached vectors from an old provider) silently produces zero similarity rather than crashing.

---

## 5. Fit scoring (`fit_engine.py`)

### `cosine_similarity` and the dimension guard

`cosine_similarity(a, b)` (`backend/matching/fit_engine.py:67`) is a pure-Python cosine. Its first line is the **dimension guard**:

```python
if len(a) != len(b) or not a:
    return 0.0
```

Mismatched-length vectors (e.g. a 768-dim CV vs a 1536-dim OpenAI job from different providers, or an un-embedded empty list) return `0.0` instead of raising. Zero-norm vectors also short-circuit to `0.0`. This makes provider mismatches fail safe (everything looks like a gap) rather than throwing.

### `FitEngine.assess`

`assess` (line 82) compares every `JobSkill` against the whole `CVProfile` and returns a `FitAssessment` (line 36). For each job skill it calls `_best_match` (line 147):

1. Compute `cosine_similarity` against every CV skill's embedding; keep the highest (ties broken by the CV skill's context `weight`).
2. Convert raw similarity to a **coverage** score using thresholds from `backend/defaults.py`:
   - `sim >= SIMILARITY_FULL_MATCH` (0.82) → coverage `1.0` (full)
   - `sim >= SIMILARITY_PARTIAL_MATCH` (0.60) → coverage `0.5 + 0.5 * weight` (partial, scaled by CV context weight)
   - otherwise → coverage `0.0` (gap)

Per skill, `gap = 1.0 - coverage`, accumulated as `weighted_gaps += gap * job_skill.criticality`. The final **severity** is `weighted_gaps / total_weight` (criticality-weighted average gap), so missing a `required` skill (criticality 1.0) hurts far more than a `neutral` one (0.3).

Outputs on `FitAssessment`:

- `severity` — 0 (perfect) to 1 (everything missing).
- `simulated_ats_score = (1.0 - severity) * 100`.
- `should_modify = severity >= threshold`, where the threshold comes from the `sensitivity` arg via `THRESHOLDS` (line 21): `conservative`=0.3, `balanced`=0.5 (default), `aggressive`=0.7 (`backend/defaults.py:24-26`). Lower threshold = more eager to rewrite the CV.
- `covered_skills`, `partial_matches`, and `critical_gaps` / `preferred_gaps` (lists of `SkillGap`, split by the job skill's `section` and sorted by criticality).

An empty `JobProfile` (no skills) short-circuits to a perfect score (`severity=0.0`, `ats_score=100.0`). `FitAssessment.to_dict` (line 46) serializes the result for JSON storage in the DB.

---

## 6. End-to-end: how a job gets a fit score

Orchestration lives in `backend/scheduler/batch_runner.py`:

1. **CV side (once per run)** — `CVParser.build_profile` parses the LaTeX CV, then `Embedder.embed_cv_profile` embeds it (`batch_runner.py:262-263`). The hash on `CVProfile` enables caching.
2. **Job side (per match, concurrently)** — `_assess_one` (`batch_runner.py:429`) runs:
   - `JobSkillExtractor.extract(jd.description)` → `JobProfile`.
   - If `len(skills) < MIN_JOB_SKILLS_FOR_FIT_ENGINE` (2, `defaults.py:30`), it returns `None` (too little signal; caller records a fallback).
   - Otherwise `Embedder.embed_job_profile` embeds the job skills, then `FitEngine.assess(job_profile, cv_profile, sensitivity)` produces the `FitAssessment`.
3. **Concurrency** — assessments run under an `asyncio.Semaphore(CONCURRENCY_LLM)` (`batch_runner.py:279`, default 3). DB writes and WebSocket broadcasts happen sequentially *after* the `gather`, because the `AsyncSession` is not concurrency-safe.

So the relevance score (`JobMatcher`) gates *which* jobs proceed, and the fit score (`FitEngine`) decides *whether each surviving job warrants a tailored CV*.

---

## Related

- [Architecture overview](architecture.md)
- [API reference](api-reference.md)
- [File map](file-map.md)
- [User guide](user-guide.md)
- [Docs index](index.md)
- [Project README](../README.md)
