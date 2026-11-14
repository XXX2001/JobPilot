# Matching & fit scoring

How JobPilot turns a scraped job + your CV into a numeric fit score, using a curated skill gazetteer and criticality-weighted coverage. No embeddings, no LLM, no network.

The `backend/matching/` package contains two distinct scoring layers:

| Layer | Entry point | Inputs | Output | Used for |
| --- | --- | --- | --- |
| **Relevance ranking** | `JobMatcher.score` (`backend/matching/matcher.py:17`) | `JobDetails` + `JobFilters` | `0–100` heuristic score | Filtering/ranking scraped jobs before deeper analysis |
| **Fit / gap analysis** | `FitEngine.assess` (`backend/matching/fit_engine.py:82`) | posting skills + `CVProfile` | `FitAssessment` (severity, ATS score, gaps) | Deciding whether to tailor the CV per job |

Both layers read skills through the same gazetteer and are orchestrated by the scheduler (`backend/scheduler/batch_runner.py`).

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

> Note: these helpers are heuristic. `_experience_match` regex-scrapes `"5+ years"` mentions from the description; `_location_match` does substring checks.

---

## 2. CV parsing → `CVProfile` (`cv_parser.py`)

`CVParser.build_profile(cv_tex, gaz)` takes the raw LaTeX CV source and a `Gazetteer` and returns a `CVProfile`: a list of `SkillEntry` plus a `raw_text_hash` (SHA-256 of the source).

The skills are exactly `Gazetteer.skills(cv_tex)` — canonical names from the curated list (`backend/matching/data/skills.txt`) and the owner's search keywords — each listed once. `parse` also tags every skill with the strongest context that names it (`CONTEXT_WEIGHTS`):

| Context | Weight | Source |
| --- | --- | --- |
| `experience_recent` | 1.0 | First role of the experience section |
| `skills_section` | 0.6 | Any other text (skills table, education, unknown template) |
| `profile` | 0.5 | Profile/Summary section |
| `experience_older` | 0.4 | Later roles, or an experience section with no role header |

The context is informational: coverage only asks whether the CV names a skill.

---

## 3. Posting skills (`job_skills.py`)

`extract_job_skills(title, description, gaz)` returns `{canonical skill: criticality}`, the strongest non-negated mention winning: required `1.0`, neutral `0.6`, company/stack blurb `0.4`, nice-to-have `0.3`. A skill named in the title is required. Free text never becomes a skill, so nothing a posting writes can reach the CV-tailoring prompt as a "gap".

---

## 4. Fit scoring (`fit_engine.py`)

`FitEngine.assess(job_skills, cv_profile, gaz, sensitivity)` returns a `FitAssessment`. No embeddings, no LLM, no network.

Each posting skill gets a credit: `1.0` when the CV names it, `0.5` when the CV names a skill of the same `@family` (Django for FastAPI), else `0.0` (`scorer.skill_credit`). **Coverage** is the criticality-weighted share of credited skills, required skills counting for 65% (`scorer.coverage`) — the same number the match score is built on.

Outputs on `FitAssessment`:

- `simulated_ats_score = 100 * coverage`.
- `severity = 1 - coverage` — 0 (everything covered) to 1 (everything missing).
- `should_modify = severity >= threshold`, the threshold coming from `sensitivity` via `THRESHOLDS`: `conservative`=0.3, `balanced`=0.5 (default), `aggressive`=0.7 (`backend/defaults.py`). Lower threshold = more eager to rewrite the CV.
- `covered_skills` — skills on the CV, then the same-family ones; `partial_matches` — the same-family ones alone.
- `critical_gaps` / `preferred_gaps` — lists of `SkillGap(skill, criticality)` for skills with no credit, sorted by criticality; a nice-to-have (`0.3`) gap is "preferred", anything above is "critical".

A posting with no recognised skill gives `simulated_ats_score=0`, `should_modify=False` (nothing to close, and no evidence of a fit). `FitAssessment.to_dict` serializes the result for JSON storage in the DB.

---

## 5. End-to-end: how a job gets a fit score

Orchestration lives in `backend/scheduler/batch_runner.py` (step 3.5 of the batch):

1. **CV side (once per run)** — `Gazetteer.load(keywords)` then `CVParser.build_profile` on the base CV. Skipped when no base CV exists.
2. **Job side (per stored match)** — `_assess_one` runs `extract_job_skills(title, description)`. With fewer than `MIN_JOB_SKILLS_FOR_FIT_ENGINE` (2) skills it returns `None` (too little signal; the job is tailored through the original LLM pipeline). Otherwise `FitEngine.assess` produces the `FitAssessment`.
3. The assessment is stored on the `JobMatch` (`ats_score`, `gap_severity`, `fit_assessment_json`) and broadcast as a `job_progress` WebSocket frame. The whole step is synchronous CPU work of a few milliseconds per job.

So the match score (`JobMatcher`) gates *which* jobs proceed, and the fit assessment (`FitEngine`) decides *whether each surviving job warrants a tailored CV*.

---

## Related

- [Architecture overview](architecture.md)
- [API reference](api-reference.md)
- [File map](file-map.md)
- [User guide](user-guide.md)
- [Docs index](index.md)
- [Project README](../README.md)
