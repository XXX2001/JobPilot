# Module: Matching

Cross-references: [matching.md](../matching.md) (rules, worked examples, evaluation) | [monitor.md](../monitor.md) | [file-map.md](../file-map.md) | [architecture.md](../architecture.md) | [index.md](../index.md)

Deterministic scoring on a curated skill gazetteer. No embeddings, no LLM, no network. The weighted keyword/location/recency matcher was replaced in `12ac325`. `skill_patterns.py`, `job_skill_extractor.py`, `embedder.py` and the cosine code were deleted in `1b289a8` (spec D10, Phase 2).

| File | Brief |
|---|---|
| `backend/matching/gazetteer.py` | `Gazetteer`: alias lookup over accent-folded tokens, `@family` relations, owner and keyword extensions |
| `backend/matching/job_skills.py` | `extract_job_skills`: a posting's skills, each with a criticality |
| `backend/matching/scorer.py` | `score_job`: hard filters, coverage, title factor, provisional score |
| `backend/matching/matcher.py` | `JobMatcher`, `load_matcher`, `skill_files` |
| `backend/matching/fit_engine.py` | `FitEngine`: gap severity, used for CV tailoring |
| `backend/matching/cv_parser.py` | `latex_to_text`, `CVParser`: the CV's skills, with context |
| `backend/matching/filters.py` | `JobFilters` dataclass |
| `backend/matching/data/` | `skills.txt` (bundled vocabulary), `countries.txt`, `cities.txt` (location filter) |

---

## Gazetteer

`Gazetteer.load(extra_terms=(), *, path=SKILLS_PATH, my_skills="")` (`gazetteer.py:156`):

1. Parses the vocabulary at `path` (`canonical | alias | …`, `!alias` case-sensitive, `@family`, `@family!` for no credit), then the owner's `my_skills` text in the same format. The first claim on an alias wins, so the vocabulary's skill takes an alias both files list.
2. `extra_terms` (the owner's search keywords): a keyword in which the gazetteer finds nothing becomes a skill of its own (`extra_skills`). A keyword that contains a listed skill is a job title and adds nothing.
3. `owner_skills` = the canonical skills the `my_skills` lines resolve to.

`extract(text)` tokenises accent-stripped text, blanks noise (`_NOISE`: French elisions, `R&D`, "go-to-market", "Series C", …), and matches n-grams longest first. A case-sensitive one-token hit glued by `-` or `&` is dropped ("C-level"). `related(a, b)` is true when both skills share a family that does not end in `!`.

`file_version(path)` = the first 12 hex characters of the file's sha256. `GAZETTEER_VERSION` is that of the bundled file. `check_skill_lines(text)` (`gazetteer.py:120`) validates an owner file and raises `ValueError` naming the first bad line.

## Posting skills

`extract_job_skills(title, description, gaz) -> {canonical: criticality}` (`job_skills.py:186`). Criticality is `REQUIRED 1.0`, `NEUTRAL 0.6`, `BLURB 0.4` or `NICE 0.3`. It comes from the section header above the mention, overridden by an inline marker in the same clause. A negated mention does not count. The strongest mention wins, and a skill in the title is `REQUIRED`.

## Score

`score_job(job, cv, filters, gaz, *, claimed_only=False) -> ScoreResult` (`scorer.py:286`):

1. **Hard filters** (`_reject_reason`): excluded keyword in the title, excluded company, `remote_only`, location. A rejection scores 0 with `passed_filters=False` and is never matched. The location filter places a job by country name, ISO code (a two-letter US state or Canadian province stands for both countries), `JobDetails.country`, then a major city. An unplaceable location passes a country filter with `location_unverified=True`.
2. **Weights**: `extract_job_skills("", description)` when the description is at least `PROVISIONAL_MIN_CHARS = 80` characters, plus the title's skills as `REQUIRED`.
3. **Provisional**: fewer than `PROVISIONAL_MIN_SKILLS = 2` skills in the body. The score is title-only: `PROVISIONAL_KEYWORD_SCORE = 50` with a search keyword in the title (literally or through a skill it names), `PROVISIONAL_CV_SKILL_SCORE = 40` with a CV skill in the title, else 0. Lowered from 70 and 45 in `2ad7fc3`.
4. **Otherwise**: `100 × coverage(weights, credit, MATCH_SHRINK) × multiplier`. Multiplier = `0.4 + 0.6 × T`. T is 1.0 for a keyword in the title, 0.1 for a non-technical title (and then the multiplier is 0.1, with no floor), 1.0 for a CV skill in the title, else 0.6.

`coverage(weights, credit, shrink=SHRINK)` (`scorer.py:269`): per tier, `Σ w·credit / (Σ w + shrink)`. Required and other skills combine as `0.65 / 0.35` when both tiers exist. Credit (`skill_credit`) is 1.0 for a skill on the CV or one of the owner's `extra_skills`, `FAMILY_CREDIT = 0.5` for a same-family CV skill, else 0.

| Constant | Value | Used by |
|---|---|---|
| `SHRINK` | 0.5 | `coverage` default: the fit assessment (`FitEngine`) |
| `MATCH_SHRINK` | 1.0 | the match score. Two covered skills reach 67, not 80 (`8a2e557`) |

`ScoreResult(score, passed_filters, reject_reason, covered, partial, missing, provisional, location_unverified)` (`scorer.py:119`).

`SCORER_VERSION = 3`: bump it when a scoring rule or the location data changes, and every stored score goes stale.

## JobMatcher

`JobMatcher(cv_text="", extra_terms=(), *, my_skills="", vocab_path=SKILLS_PATH)` (`matcher.py:31`):

- The CV skill set is `owner_skills` when `my_skills` lists any. Otherwise it is the gazetteer skills of `latex_to_text(cv_text)`. Without either, `explain` scores with `claimed_only=True` against the skills the search keywords name.
- `score_basis` = `sha256(SCORER_VERSION, CV text or "my_skills\0"+file, sorted keywords)[:12] + ":" + file_version(vocab_path)` (`matcher.py:56`). A stored match with another basis is stale ([monitor.md](../monitor.md#rescoring)).
- `explain(job, filters) -> ScoreResult`; `score` returns its `.score`; `matched_keywords` returns `covered + partial`; `rank_and_filter` sorts by score.

`load_matcher(db)` (`matcher.py:102`) builds the owner's matcher. It takes the base CV from `UserProfile.base_cv_path`, else the first `templates/*.tex` (`resolve_base_source`), the search keywords, and the skill files. `_matcher` is an `lru_cache(maxsize=4)` keyed on the CV text, the keywords, the `my_skills` text, the vocabulary path and its version. Both files are read on every call, so a hand edit applies to the next score without a restart. Stored scores catch up at the next rescore. Callers: `match_new` and `rescore_stale` (monitor), `BatchRunner._run_batch_inner` (batch, when no matcher is injected).

### Owner skill files

`skill_files() -> (my_skills_path, vocabulary_path)` (`matcher.py:95`):

| File | Effect |
|---|---|
| `$DATA/my_skills.txt` | The owner's skills, in the gazetteer format. When it lists any, it replaces the CV as the skill set. Names the vocabulary does not know are added to it |
| `$DATA/skills.txt` | Replaces the bundled `backend/matching/data/skills.txt` when present |

`GET /api/settings/skills` returns `{my_skills, vocabulary_overridden, skill_count}`, plus `error` for a file edited by hand into a bad state. `PUT /api/settings/skills {"my_skills": text}` validates with `check_skill_lines` (422 with the line number), writes atomically (`.tmp` then rename), removes the file when the text is blank, and rescores (`backend/api/settings.py:361`). There is no frontend page for these routes. Template: `scripts/defaults/my_skills.example.txt`.

## FitEngine

`FitEngine().assess(job_skills, cv_profile, gaz, sensitivity="balanced") -> FitAssessment` (`fit_engine.py:48`). `severity = 1 − coverage` (with `SHRINK`), `simulated_ats_score = 100 × coverage`. `should_modify` is `severity >= threshold` (conservative 0.3, balanced 0.5, aggressive 0.7). `critical_gaps` are uncovered skills above `NICE`, and `preferred_gaps` the rest. A posting with no recognised skill returns severity 1.0 and `should_modify=False`.

The batch calls it through `BatchRunner._assess_one` only when the posting names at least `MIN_JOB_SKILLS_FOR_FIT_ENGINE = 2` skills. It builds its own `Gazetteer.load(filters.keywords)` and a `CVParser` profile of the CV (`batch_runner.py:252`). It therefore uses the bundled vocabulary and the CV, never `$DATA/skills.txt` or `my_skills.txt`.

## CVParser

`latex_to_text(cv_tex)` (`cv_parser.py:44`) de-escapes what a CV uses (`\&` → `&`, `\#` → `#`, accent macros → the bare letter, `~` and `\,` → space, table column specs dropped), so "R\&D" is not the skill R. Commands and environments stay, because the parser reads sections from them. `CVParser.parse` tags each CV skill with its strongest context (`CONTEXT_WEIGHTS`: `experience_recent` 1.0, `skills_section` 0.6, `profile` 0.5, `experience_older` 0.4). `build_profile` wraps the result as `CVProfile(skills, raw_text_hash)`.

## JobFilters

```python
@dataclass
class JobFilters:
    keywords: list[str]
    excluded_keywords: list[str]
    locations: list[str]
    salary_min: Optional[int] = None
    experience_range: Optional[tuple[int, int]] = None
    remote_only: bool = False
    job_types: list[str]
    languages: list[str]
    excluded_companies: list[str]
    min_score: float = MIN_MATCH_SCORE  # 30.0
```

The scorer reads `keywords`, `excluded_keywords`, `excluded_companies`, `locations` and `remote_only`. `min_score` is the caller's threshold. `salary_min`, `experience_range`, `job_types` and `languages` are carried but not scored.

## Known shortcuts

| Where | Shortcut | Upgrade path |
|---|---|---|
| `gazetteer.py:65` `_NOISE` | A phrase list, not grammar: "Go" as a capitalised verb in another phrase still matches | Require a second skill of the same family in the clause |
| `job_skills.py:78` | A bare "plus" is not a nice-to-have marker; only "a plus" / "un plus" | — |
| `scorer.py:63` `_REMOTE_NOISE` | Remote detection by phrase list | Parse the workplace type per source |
| `scorer.py:106` `_SUBDIVISIONS` | US and Canadian two-letter codes only | Add Indian and Australian states if they show up |
| `cv_parser.py:51` `latex_to_text` | The escapes a CV uses, not a LaTeX reader | A real detex |
