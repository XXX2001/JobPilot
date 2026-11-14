# Matching & fit scoring

How JobPilot turns a scraped job + your CV into a numeric fit score, using a curated skill gazetteer and criticality-weighted coverage. No embeddings, no LLM, no network.

The `backend/matching/` package contains two distinct scoring layers:

| Layer | Entry point | Inputs | Output | Used for |
| --- | --- | --- | --- | --- |
| **Match score** | `JobMatcher.score` / `explain` (`backend/matching/matcher.py`) | `JobDetails` + `JobFilters` | `0–100` score, or a `ScoreResult` with the reasons | Deciding which discovered jobs become matches, and their order |
| **Fit / gap analysis** | `FitEngine.assess` (`backend/matching/fit_engine.py`) | posting skills + `CVProfile` | `FitAssessment` (severity, ATS score, gaps) | Deciding whether to tailor the CV per job |

Both layers read skills through the same gazetteer. The monitor (`backend/monitor/store.py`) and the batch (`backend/scheduler/batch_runner.py`) score through `load_matcher`, which builds one `JobMatcher` from the owner's base CV and search keywords.

---

## 1. Match score (`scorer.py`, `matcher.py`, `filters.py`)

`score_job(job, cv_skills, filters, gazetteer)` returns a `ScoreResult`; `JobMatcher.score` is its `score` field.

```
score = 100 * coverage * (0.4 + 0.6 * T)
```

**Hard filters first.** A job that fails one scores `0` with `passed_filters=False` and a `reject_reason`; it is never matched, whatever the threshold.

| `reject_reason` | Rule |
| --- | --- |
| `excluded_keyword` | An excluded keyword is in the title, as whole words. |
| `excluded_company` | The company equals an excluded company (accent- and case-folded). |
| `location` | `remote_only` and the job is not remote; or the location names none of the configured places. |

The location filter splits the configured locations into places ("Paris") and countries ("France", "FR"). A place must be in the job's location as whole words. A country entry rejects only a job that names *another* country: there is no city table, so "Marseille" or "London" alone cannot be placed and passes with `location_unverified=True`. A job with no location passes.

**Coverage** (`scorer.coverage`) is the criticality-weighted share of the posting's skills the CV covers. Each posting skill earns `1.0` when the CV names it, `0.5` when the CV names a skill of the same `@family`, else `0`. Required skills (criticality `1.0`) and the rest are averaged separately and combined `0.65 / 0.35`; a pseudo-weight of `0.5` in each denominator keeps a one-skill posting from reaching 100.

**Title factor `T`**: `1.0` when a search keyword is in the title; else `0.1` for a non-technical title (sales, marketing, accounting, legal, recruiting, analyst…); else `1.0` when the title names a CV skill; else `0.6`.

**Provisional score.** A posting with no description (LinkedIn and SmartRecruiters cards, under 80 characters) or whose description names no known skill has no coverage to compute. It is scored on the title alone and flagged `provisional`: `70` with a search keyword in the title, `45` with a CV skill in the title, else `0`.

`ScoreResult` also carries `covered`, `partial` (same-family credit) and `missing` (sorted by criticality). `JobMatch.keyword_hits` stores `covered + partial`.

**What is not in the score:** recency (applied at read time), salary and experience. `JobFilters` (`backend/matching/filters.py`) still carries `salary_min`, `experience_range`, `job_types` and `languages`; the scorer does not read them.

**Stale scores.** Each match stores `score_basis` = hash of (CV text, keywords) + `GAZETTEER_VERSION` (hash of `skills.txt`). `rescore_all` (`backend/monitor/store.py`) recomputes the `new` matches whose basis differs; it runs at startup and after a CV or settings save (`rescore_stale` in `backend/monitor/poller.py`).

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

## 6. Extending the skill list (`backend/matching/data/skills.txt`)

One skill per line: `canonical | alias | alias …`. Lines starting with `#` are comments.

```
@pyweb
fastapi | fast api
django | django rest framework | drf
@lang!
!Go | golang
```

- Matching is on accent-folded, lowercased whole tokens, longest alias first. `Intégration Continue` matches `integration continue`; `Go` never matches inside `going`.
- `!alias` is case-sensitive. Use it for names that are also ordinary words or letters (`!Go`, `!R`, `!C`, `!Swift`).
- `@family` starts a family: the lines below it earn half credit for each other (Django on the CV, FastAPI in the posting). `@family!` groups lines for reading only, with no credit between members.
- Do not add a bare ordinary word as an alias (`vue`, `node`, `shell`): it matches prose.
- The first line to claim an alias keeps it.

Any edit changes `GAZETTEER_VERSION`, so stored `new` matches are rescored at the next startup. Add a unit test in `tests/test_gazetteer.py` for a name that could match prose.

**Search keywords extend the list at runtime.** A keyword in which the list finds no skill becomes a skill of its own (`Gazetteer.load(extra_terms)`), so a profession the list does not cover still counts. A keyword that contains a listed skill ("Python Developer") is treated as a job title and adds nothing.

---

## 7. Evaluation

### Adversarial ranking test

`tests/test_scoring_ranking.py` scores the 12 synthetic postings of `tests/fixtures/scoring/adversarial.json` (keyword-stuffed sales job, negated skill, all-lowercase posting, headers without a colon, wrong city, Python only as nice-to-have, `R`/`Go` substring traps…) against `scripts/defaults/templates/example_cv.tex` and asserts that every posting labelled `good` outranks every posting labelled `bad`. The labels are fixed: a failure is fixed in the rule that owns it.

The margin is narrow. Without search keywords the lowest `good` scores 39.6 and the highest `bad` (the keyword-stuffed sales job) 36.0, which is above the default threshold of 30.

### Report on a real database

`scripts/diagnostics/score_report.py` opens a database read-only and scores every job with the stored search settings and a CV:

```bash
env -u PYTHONPATH uv run python scripts/diagnostics/score_report.py \
    --db data/jobpilot.db [--cv data/templates/cv.tex] [--top 40] [--seed 0]
```

It prints the time per job, the share each hard filter rejects, the share at or above `min_match_score`, the score histogram, the top N and 20 random mid-ranked jobs with their covered and missing skills.

Measured on 2026-11-14 on a copy of a monitor database: 3,496 postings from 29 sources (Greenhouse 2,347, Ashby 585, SmartRecruiters 492, LinkedIn guest 72), scored against the example CV with keywords `robotics`, `software engineer`, `python`, locations `France`, `Paris`, threshold 30.

| Measure | Value |
| --- | --- |
| Time | 7.0 s for 3,496 jobs, 2.0 ms per job |
| Passed the hard filters | 2,491 (71.3%); 1,005 (28.7%) rejected by location |
| At or above the threshold (30) | 227 (6.5%) |
| Provisional (title-only) | 1,510 (43.2%): 416 with no description, 1,094 whose description names no listed skill; 25 of them reach the threshold |
| Location unverified | 1,457 (41.7%) |

| Score | Jobs |
| --- | --- |
| 0–10 | 1,943 |
| 10–20 | 192 |
| 20–30 | 129 |
| 30–40 | 125 |
| 40–50 | 60 |
| 50–60 | 21 |
| 60–70 | 9 |
| 70–80 | 12 |
| 80–100 | 0 |

The top 40: 12 provisional cards at 70 (a keyword in the title, no description), then Python, cloud and data engineering postings from 67 down to 51. 19 of the 40 have an unverified location.

What the run shows, none of it tuned away:

- **A country filter lets foreign cities through.** With `France` configured, half of the jobs at or above the threshold (113 of 227) have an unverified location, mostly abroad (London, Hong Kong, Sydney, Dubai). A posting is rejected only when its location names another country from the scorer's table. Configuring cities only ("Paris") removes them, and with them French jobs outside those cities.
- **One covered skill is enough for about 51.** A posting that names a single listed skill the CV has scores `100 × 1/1.5 × 0.76 = 50.7` whatever the job is ("Intern – Chief of Staff", one mention of data modelling).
- **A search keyword that is not on the CV counts as a missing required skill.** `software engineer` became a runtime skill; a "Software Engineer" posting with a description loses coverage for it, while the same title as a bare card scores a provisional 70.
- **A keyword is matched in the title literally.** `robotics` does not match "Robotique" for the title factor or the provisional score, though the gazetteer knows the alias.
- **Management and intern titles are not down-weighted.** Only the non-technical title list lowers `T`.

No held-out set built from the owner's real CV exists yet; these numbers come from the example CV.

---

## Related

- [Architecture overview](architecture.md)
- [API reference](api-reference.md)
- [File map](file-map.md)
- [User guide](user-guide.md)
- [Docs index](index.md)
- [Project README](../README.md)
