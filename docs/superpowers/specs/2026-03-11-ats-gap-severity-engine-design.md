# ATS-Simulated Gap Severity Engine — Design Spec

**Date:** 2026-03-11
**Status:** Approved
**Goal:** Rework the matching algorithm and CV modification decision using a deterministic, embedding-boosted ATS simulation that computes gap severity to decide when the base CV is enough vs when modification is needed.

## Problem

The current system always runs the full LLM pipeline (JobAnalyzer + CVModifier) for every matched job. This causes:

1. **Wasted LLM costs** — many jobs are already a strong fit for the base CV
2. **Over-tailoring** — modifications sometimes make the CV worse for jobs that already match well
3. **No graduated intelligence** — binary "always attempt 3 replacements" regardless of fit

The matching score (0-100) only uses search filter keywords (not the CV content) and only decides *which* jobs to process, not *whether* to modify the CV.

## Solution Overview

Build a local ATS simulator that:

1. Parses the CV once on upload → extracts skills with context weights
2. Extracts skills from job postings using NLP (no LLM) with criticality scoring
3. Embeds both via Gemini `text-embedding-004` for semantic matching
4. Computes a **gap severity index** — a single number encoding how critical the missing skills are
5. Uses gap severity to make a binary decision: **base CV** or **modify CV**
6. When modifying, feeds a surgical gap report to CVModifier instead of a generic JobAnalyzer output

**Result:** ~75% reduction in LLM calls. Better CV quality. Explainable decisions.

## Architecture

### New Files

All under `backend/matching/`:

| File | Purpose |
|------|---------|
| `cv_parser.py` | LaTeX skill extraction with context tagging |
| `job_skill_extractor.py` | Job description NLP extraction + criticality scoring |
| `embedder.py` | Gemini embedding wrapper + caching logic |
| `fit_engine.py` | ATS simulation + gap severity scoring + decision |
| `skill_patterns.py` | Shared regex patterns, linguistic modifiers |

### Modified Files

| File | Change |
|------|--------|
| `backend/llm/gemini_client.py` | Add `embed()` method |
| `backend/matching/matcher.py` | Integrate FitEngine after rank_and_filter |
| `backend/scheduler/morning_batch.py` | Route to base CV or LLM pipeline based on FitEngine |
| `backend/llm/cv_modifier.py` | Accept FitAssessment instead of JobContext |
| `backend/llm/prompts.py` | New targeted CVModifier prompt using gap report |
| `backend/models/user.py` | Add `cv_modification_sensitivity` setting |
| `backend/api/settings.py` | Expose new setting |
| `frontend/src/routes/settings/+page.svelte` | 3-option sensitivity selector |
| `backend/defaults.py` | Add threshold constants |

### No New Dependencies

`google-generativeai>=0.8` (already installed) includes embedding support via `genai.Client.models.embed_content()`.

## Component Design

### 1. CV Parser (`cv_parser.py`)

Runs once on CV upload/update. Parses the LaTeX `.tex` file and extracts skills with context.

```python
@dataclass
class SkillEntry:
    text: str              # "Python", "CI/CD pipelines"
    context: str           # "experience_recent", "skills_section", "experience_older", "profile"
    weight: float          # context-derived: 1.0, 0.6, 0.5, 0.4
    embedding: list[float] # 768-dim vector from Gemini

@dataclass
class CVProfile:
    skills: list[SkillEntry]
    raw_text_hash: str     # SHA-256 of .tex content, used to detect changes
```

**Context weights** (mimic ATS section weighting):

| Context | Weight | Rationale |
|---------|--------|-----------|
| `experience_recent` (most recent role) | 1.0 | ATS weights recent experience highest |
| `skills_section` | 0.6 | Listed skills without demonstrated context |
| `profile` (summary paragraph) | 0.5 | Stated strengths |
| `experience_older` (earlier roles) | 0.4 | Recency decay |

**Extraction approach:**
- Regex patterns targeting common LaTeX CV structures: `\cvskill{}`, `\skill{}`, skills rows/tables, comma-separated lists in skills sections
- Experience bullet content scanned for tech/tool mentions
- Profile/summary paragraph text scanned for skill phrases

**Caching:** `CVProfile` serialized as JSON and stored in DB alongside the user record. On batch run, compare `raw_text_hash` with current `.tex` file — skip re-parsing if unchanged.

### 2. Job Skill Extractor (`job_skill_extractor.py`)

Runs per job during matching phase. No LLM call.

```python
@dataclass
class JobSkill:
    text: str           # "Docker", "3+ years Python"
    criticality: float  # 0.0-1.0
    section: str        # "required", "preferred", "neutral"
    embedding: list[float]

@dataclass
class JobProfile:
    skills: list[JobSkill]
    knockout_filters: list[str]  # "5+ years", "MSc required", etc.
```

**Step 1 — Section detection:** Regex patterns identify semantic sections:
- **Critical sections:** `required`, `must have`, `essential`, `you bring`, `qualifications`, `requirements`
- **Preferred sections:** `nice to have`, `bonus`, `preferred`, `ideally`, `plus`, `advantageous`
- **Neutral:** intro, company description, benefits (everything else)

**Step 2 — Skill phrase extraction** within each section:
- Bullet point items (most postings use bullets)
- Phrases following patterns: "experience with/in X", "knowledge of X", "proficiency in X"
- Known tech patterns: capitalized words, compound terms with `/` or `-` (e.g., "CI/CD", "Node.js")

**Step 3 — Criticality scoring** from two signals:
- **Section position:** critical section → 1.0, preferred → 0.3, neutral → 0.6
- **Linguistic modifiers:** "must"/"essential"/"required" near skill → boost to 1.0; "exposure to"/"familiar with"/"bonus" → drop to 0.3
- Final criticality = max(section_signal, linguistic_signal)

**Step 4 — Embed:** Batch-embed all extracted skill phrases in a single Gemini API call.

### 3. Embedder (`embedder.py`)

Wraps Gemini `text-embedding-004` using the existing `GeminiClient`.

**New method on `GeminiClient`:**

```python
async def embed(self, texts: list[str]) -> list[list[float]]:
    """Batch embed texts via text-embedding-004. Returns list of 768-dim vectors."""
    await self._wait_for_rate_limit()
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: self._client.models.embed_content(
            model="text-embedding-004",
            contents=texts,
        ),
    )
    return [e.values for e in result.embeddings]
```

**Caching strategy:**
- **CV embeddings:** cached on upload in DB. Re-embedded only when `raw_text_hash` changes.
- **Job embeddings:** cached per `JobMatch` record. Same job (by dedup hash) won't be re-embedded.

**Cost:** Gemini embeddings free tier = 1500 requests/day. Typical batch of 50 jobs = ~50 calls. Well within limits.

### 4. Fit Engine (`fit_engine.py`)

The core algorithm. Computes gap severity and makes the modification decision.

```python
@dataclass
class SkillGap:
    skill: str              # "Docker"
    criticality: float      # 0.9
    best_cv_match: str      # "CI/CD pipelines" (closest thing on CV)
    similarity: float       # 0.58 (not close enough)

@dataclass
class FitAssessment:
    severity: float                 # 0.0-1.0 (0 = perfect fit)
    should_modify: bool             # the decision
    simulated_ats_score: float      # 0-100
    covered_skills: list[str]       # skills on CV that match job requirements
    partial_matches: list[str]      # "DevOps ~ CI/CD"
    critical_gaps: list[SkillGap]   # gaps in required skills
    preferred_gaps: list[SkillGap]  # gaps in nice-to-have skills (informational)
```

**Algorithm:**

**Step 1 — Semantic coverage per job skill:**

For each `JobSkill`, find its best match in `CVProfile`:

```python
def best_match(job_skill: JobSkill, cv_profile: CVProfile) -> float:
    best = 0.0
    for cv_skill in cv_profile.skills:
        sim = cosine_similarity(job_skill.embedding, cv_skill.embedding)
        effective = sim * cv_skill.weight
        best = max(best, effective)

    if best >= 0.82:
        return 1.0   # covered
    elif best >= 0.60:
        return 0.5   # partially covered
    else:
        return 0.0   # gap
```

**Step 2 — Gap severity index:**

```python
def gap_severity(job_profile: JobProfile, cv_profile: CVProfile) -> float:
    total_weight = 0.0
    weighted_gaps = 0.0

    for skill in job_profile.skills:
        coverage = best_match(skill, cv_profile)
        gap = 1.0 - coverage
        weighted_gaps += gap * skill.criticality
        total_weight += skill.criticality

    if total_weight == 0:
        return 0.0

    return weighted_gaps / total_weight
```

**Step 3 — Decision:**

```python
THRESHOLDS = {
    "conservative": 0.3,
    "balanced": 0.5,
    "aggressive": 0.7,
}

def should_modify(severity: float, sensitivity: str = "balanced") -> bool:
    return severity >= THRESHOLDS[sensitivity]
```

**Simulated ATS score:** `ats_score = (1.0 - severity) * 100` — a 0-100 number representing estimated ATS pass likelihood.

**Practical examples:**

| Scenario | Gap Severity | ATS Score | Decision (balanced) |
|----------|-------------|-----------|---------------------|
| Python dev CV → Python job, missing only "Jira" (preferred) | ~0.08 | 92 | Base CV |
| Python dev CV → Python job, missing "AWS" (required) | ~0.35 | 65 | Base CV |
| Python dev CV → Python job, missing "AWS" + "Docker" (both critical) | ~0.55 | 45 | Modify |
| Python dev CV → Java job, missing "Java", "Spring" (both critical) | ~0.75 | 25 | Modify |
| Full stack CV → full stack job, all skills present | ~0.05 | 95 | Base CV |

### 5. Modified CVModifier Integration

**Current flow:** `JobAnalyzer` (LLM) → generic `JobContext` → `CVModifier` (LLM)
**New flow:** `FitEngine` (deterministic) → surgical `FitAssessment` → `CVModifier` (LLM)

When modification is needed, the CVModifier prompt receives targeted instructions derived from `FitAssessment`:

```
You MUST address these critical gaps (ranked by severity):
1. "Docker" (criticality: 0.9) — closest CV skill: "CI/CD pipelines" (sim: 0.58)
2. "Terraform" (criticality: 0.8) — no match on CV

You MUST NOT touch these (already covered):
- "Python", "FastAPI", "PostgreSQL"

Budget: only fix gaps where candidate has related experience.
```

**JobAnalyzer disposition:**
- Eliminated for most jobs (FitEngine provides the structured analysis)
- Kept as optional fallback for unparseable job descriptions (<5% of jobs)
- Fallback trigger: FitEngine extracts fewer than 2 skills from the job description

**LLM call savings:**

| Scenario | Current (LLM calls) | New (LLM calls) |
|----------|---------------------|------------------|
| Job fits well (base CV enough) | 2 (Analyzer + Modifier) | 0 |
| Job needs modification | 2 (Analyzer + Modifier) | 1 (Modifier only) |
| Unparseable job description | 2 (Analyzer + Modifier) | 2 (fallback) |
| **Typical batch of 10 jobs (60% fit)** | **20 calls** | **~4-5 calls** |

### 6. Pipeline Flow (Updated)

```
morning_batch step 2 (MATCH & RANK):
  └─ JobMatcher.rank_and_filter() → scored + filtered jobs (unchanged)

morning_batch step 3.5 (NEW — FIT ASSESSMENT):
  └─ Load cached CVProfile (or parse + embed if stale)
  └─ For each matched job:
      ├─ JobSkillExtractor.extract(job.description) → JobProfile
      ├─ Embed job skills (batched Gemini call)
      ├─ FitEngine.assess(job_profile, cv_profile, sensitivity) → FitAssessment
      └─ Store FitAssessment on JobMatch record

morning_batch step 4 (PRE-GENERATE CVs — MODIFIED):
  └─ For each match:
      ├─ IF assessment.should_modify == False:
      │    └─ Copy base CV → compile PDF (no LLM calls)
      ├─ IF assessment.should_modify == True:
      │    └─ CVModifier(fit_assessment) → replacements → compile PDF
      └─ IF FitEngine couldn't parse job (fallback):
           └─ JobAnalyzer → CVModifier → compile PDF (current flow)
```

### 7. User Settings & Observability

**New setting:**

```python
cv_modification_sensitivity: Literal["conservative", "balanced", "aggressive"] = "balanced"
```

- Added to user model and settings API
- Frontend: 3-option selector in settings page with descriptions:
  - **Conservative** — "Modify CV for most jobs" (threshold 0.3)
  - **Balanced** — "Only modify when meaningful gaps exist" (threshold 0.5, default)
  - **Aggressive** — "Trust my base CV, rarely modify" (threshold 0.7)

**Per-job observability in dashboard:**

Each job match displays:
- Simulated ATS score (0-100)
- Gap severity (0.0-1.0)
- Decision taken (base CV / modified)
- Covered skills list
- Gap list with criticality

**WebSocket broadcast enrichment:**

```json
{
  "type": "job_progress",
  "job_id": 42,
  "ats_score": 78,
  "gap_severity": 0.18,
  "decision": "base_cv",
  "covered": ["Python", "FastAPI"],
  "gaps": [{"skill": "Kubernetes", "criticality": 0.3}]
}
```

### 8. Performance Budget

| Operation | Latency | API Cost |
|-----------|---------|----------|
| CV parse + embed (once) | ~500ms | 1 embedding call |
| Job skill extraction (NLP) | ~5ms | 0 |
| Job skill embedding | ~200ms | 1 embedding call |
| Cosine similarity (300 pairs) | <1ms | 0 |
| **Total per job (FitEngine)** | **~210ms** | **1 embedding call** |
| Current per job (JobAnalyzer + CVModifier) | ~3-5s | 2 LLM calls |

**Batch of 50 jobs:** ~10.5s (FitEngine) vs ~150-250s (current). **~15-25x faster.**

### 9. Defaults (`backend/defaults.py` additions)

```python
# ATS Gap Severity Engine
GAP_SEVERITY_THRESHOLD_CONSERVATIVE: float = 0.3
GAP_SEVERITY_THRESHOLD_BALANCED: float = 0.5
GAP_SEVERITY_THRESHOLD_AGGRESSIVE: float = 0.7
EMBEDDING_MODEL: str = "text-embedding-004"
SIMILARITY_FULL_MATCH: float = 0.82
SIMILARITY_PARTIAL_MATCH: float = 0.60
MIN_JOB_SKILLS_FOR_FIT_ENGINE: int = 2  # below this, fallback to JobAnalyzer
```
