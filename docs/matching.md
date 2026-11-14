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

The location filter splits the configured locations into places ("Paris") and countries ("France", "FR"). A place must be in the job's location as whole words. A country entry rejects only a job proven to be in *another* country. The job's country is read from:

- a country name anywhere in the location, from `backend/matching/data/countries.txt` (every ISO 3166-1 country, English and French names; the French overseas departments count as France);
- an ISO code as the last comma part ("Lyon, ARA, FR") or, in capitals, as the first or last word ("US - New York", "Remote US"). A code that is also a US state or a Canadian province stands for both countries ("Denver, CO");
- `JobDetails.country`;
- when none of those names a country, a major city from `backend/matching/data/cities.txt` (capitals and large metros: "London" is in the United Kingdom).

A town neither table knows ("Massy", "Remote") cannot be placed: it passes with `location_unverified=True`, which is stored on the match, shown in the queue and added to the notification title. A job with no location passes. Both files are `code | name | name` lines; their headers list the names left out because they are more often something else (Georgia, Jersey, York, Porto, Vienne).

**Coverage** (`scorer.coverage`) is the criticality-weighted share of the posting's skills the CV covers. Each posting skill earns `1.0` when the CV names it, `0.5` when the CV names a skill of the same `@family`, else `0`. Required skills (criticality `1.0`) and the rest are averaged separately and combined `0.65 / 0.35`; a pseudo-weight in each denominator keeps a posting that names few skills from scoring high: `1.0` for the match score (two covered skills reach 67%, not 80%), `0.5` for the fit assessment.

**Title factor `T`**: `1.0` when a search keyword is in the title, literally or through a skill it names ("Ingénieur Robotique" for the keyword `robotics`); else `0.1` for a non-technical title (sales, marketing, accounting, recruiting…; `commercial`, `legal`, `compliance`, `talent` and `analyst` only before the title's first separator, so "Software Engineer, Talent Platform" is not one); else `1.0` when the title names a CV skill; else `0.6`. A non-technical title gets no floor: its score is `100 * coverage * 0.1`, so a sales posting that lists every skill of the CV stays under 10.

**The owner's own terms always count.** A search keyword that became a skill of its own (`Gazetteer.extra_skills`, e.g. `software engineer`) is credited as covered whether or not the CV spells it out, in the score and in the fit engine's gaps. A keyword that is a curated skill (`robotics`) must still be on the CV.

**Without a CV** the skills named by the search keywords stand in for it, and only those are scored: the score is how strongly the posting asks for the owner's terms, and the posting's other skills are not missing ones.

**Provisional score.** A posting with no description (LinkedIn and SmartRecruiters cards, under 80 characters) or whose description names fewer than two known skills has no coverage worth computing: one incidental "Python" would be 67% coverage. It is scored on the title alone and flagged `provisional`: `50` with a search keyword in the title, `40` with a CV skill in the title, else `0`. The flag is stored on the match (`job_matches.provisional`); the queue lists provisional matches after described ones and the notification title says "title only".

`ScoreResult` also carries `covered`, `partial` (same-family credit) and `missing` (sorted by criticality). `JobMatch.keyword_hits` stores `covered + partial`.

**What is not in the score:** recency (applied at read time), salary and experience. `JobFilters` (`backend/matching/filters.py`) still carries `salary_min`, `experience_range`, `job_types` and `languages`; the scorer does not read them.

**Stale scores.** Each match stores `score_basis` = hash of (`SCORER_VERSION`, CV text, keywords) + `GAZETTEER_VERSION` (hash of `skills.txt`). Bump `SCORER_VERSION` in `scorer.py` when a scoring rule or the location data changes. `rescore_all` (`backend/monitor/store.py`) recomputes the `new` matches whose basis differs; it runs at startup and after a CV or settings save (`rescore_stale` in `backend/monitor/poller.py`). `rescore_stale` then scores the stored jobs that have no match and were stored in the last 30 days, so a job the old CV or keywords left out gets a match (without a notification). At startup that sweep runs only when a stored score was stale.

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

### Your own skill files (data directory)

| File | Effect |
|---|---|
| `<data dir>/my_skills.txt` | Your skills, in the format above. When it lists at least one skill, jobs are scored against it and the CV is no longer read for skills. A name the vocabulary does not know is added to it; an alias the vocabulary knows (`ros2`) resolves to the vocabulary's skill (`ros 2`). |
| `<data dir>/skills.txt` | Optional. Replaces the bundled vocabulary entirely. |

Edit the files by hand (new polls use the file at once; matches already stored are rescored at the next startup) or use `GET` / `PUT /api/settings/skills` with `{"my_skills": "<file text>"}`, which validates, writes the file and rescores stored matches at once. A line with an empty name or alias is refused with its line number and the old file is kept. An empty `my_skills` removes the file and scoring goes back to the CV. Starting point: `scripts/defaults/my_skills.example.txt`.

The fit assessment used for CV tailoring still reads the CV itself.

---

## 7. Evaluation

### Adversarial ranking test

`tests/test_scoring_ranking.py` scores the 12 synthetic postings of `tests/fixtures/scoring/adversarial.json` (keyword-stuffed sales job, negated skill, all-lowercase posting, headers without a colon, wrong city, Python only as nice-to-have, `R`/`Go` substring traps…) against `scripts/defaults/templates/example_cv.tex` and asserts that every posting labelled `good` outranks every posting labelled `bad`. The labels are fixed: a failure is fixed in the rule that owns it.

Without search keywords the lowest `good` scores 39.6 and the highest `bad` (Python only as a nice-to-have) 19.0; with the keywords `python`, `backend` they are 54.8 and 34.1. The keyword-stuffed sales job scores 7.8.

### Report on a real database

`scripts/diagnostics/score_report.py` opens a database read-only and scores every job with the stored search settings and a CV:

```bash
env -u PYTHONPATH uv run python scripts/diagnostics/score_report.py \
    --db data/jobpilot.db [--cv data/templates/cv.tex] [--top 40] [--seed 0]
```

It prints the time per job, the share each hard filter rejects, the share at or above `min_match_score`, the score histogram, the top N and 20 random mid-ranked jobs with their covered and missing skills.

Measured on 2026-11-14, after the review fixes, on a copy of a monitor database: 3,496 postings from 29 sources (Greenhouse 2,347, Ashby 585, SmartRecruiters 492, LinkedIn guest 72), scored against the example CV with keywords `robotics`, `software engineer`, `python`, locations `France`, `Paris`, threshold 30.

| Measure | Value |
| --- | --- |
| Time | 5.3 s for 3,496 jobs, 1.5 ms per job |
| Passed the hard filters | 1,863 (53.3%); 1,633 (46.7%) rejected by location |
| At or above the threshold (30) | 140 (4.0%); 179 with no CV at all |
| Provisional (title-only) | 1,553 (44.4%): 407 with no description, 1,146 whose description names fewer than two listed skills; 27 of them reach the threshold |
| Location unverified | 646 (18.5%); 10 of the 140 at the threshold |

| Score | Jobs |
| --- | --- |
| 0–10 | 1,617 |
| 10–20 | 59 |
| 20–30 | 47 |
| 30–40 | 40 |
| 40–50 | 53 |
| 50–60 | 24 |
| 60–70 | 8 |
| 70–80 | 15 |
| 80–100 | 0 |

The top 40: 14 provisional cards and one described posting at 70 (a keyword in the title), then software, cloud and data engineering postings from 68 down to 53. 2 of the 40 have an unverified location.

What the first run (before the review fixes) showed, and what was done about it:

- **A country filter let foreign cities through** (113 of the 227 jobs at the threshold). The country table now holds every ISO 3166-1 country in English and French, an ISO code is read as the first or last word of the location, and a table of major cities places "London". 10 of the 140 jobs at the threshold remain unverified ("Remote", "North America", "APAC", "Any Office", Netanya); the other 636 unverified locations are French towns below it.
- **One covered skill was enough for about 51.** A description that names fewer than two listed skills is now scored on its title alone.
- **A search keyword that is not on the CV counted as a missing required skill.** A keyword that became a runtime skill is now always covered.
- **A keyword was matched in the title literally.** It is now also matched through the gazetteer ("Robotique" for `robotics`).
- **A keyword-stuffed sales posting cleared the threshold** (36.0 on the fixture). A non-technical title no longer gets the 0.4 floor (7.8).

Still open:

- **Provisional cards scored a flat 70 in this run, above every described posting but one.** Since lowered to 50 (40 for a title skill); the match records `provisional`, the queue lists provisional matches after described ones, and the notification says "title only". 7 of the 14 name a title skill the CV does not have ("Software Engineer C++").
- **Management and intern titles are not down-weighted**, and "Data Analyst" or "Legal Tech Backend Engineer" are treated as non-technical. Only the non-technical title list changes `T`.
- **A bare two-letter code is ambiguous.** US states and Canadian provinces are handled ("Denver, CO" is the United States or Colombia); Indian and Australian state codes are not.

No held-out set built from the owner's real CV exists yet; these numbers come from the example CV.

---

## Related

- [Architecture overview](architecture.md)
- [API reference](api-reference.md)
- [File map](file-map.md)
- [User guide](user-guide.md)
- [Docs index](index.md)
- [Project README](../README.md)
