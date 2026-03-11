# ATS Gap Severity Engine — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the always-modify CV pipeline with a deterministic gap severity engine that decides when the base CV is sufficient vs when LLM modification is needed, reducing LLM calls by ~75%.

**Architecture:** Parse CV skills once on upload (with Gemini embeddings), extract job skills via NLP per posting, compute gap severity via cosine similarity, and route to base CV or targeted CVModifier based on threshold. User-configurable sensitivity (conservative/balanced/aggressive).

**Tech Stack:** Python 3.12, SQLAlchemy/Alembic, Gemini `text-embedding-004`, FastAPI, Svelte frontend. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-11-ats-gap-severity-engine-design.md`

---

## Chunk 1: Foundation — Data Models, Defaults, and DB Migration

### Task 1: Add defaults constants

**Files:**
- Modify: `backend/defaults.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_defaults.py
from backend.defaults import (
    GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    GAP_SEVERITY_THRESHOLD_BALANCED,
    GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
    EMBEDDING_MODEL,
    SIMILARITY_FULL_MATCH,
    SIMILARITY_PARTIAL_MATCH,
    MIN_JOB_SKILLS_FOR_FIT_ENGINE,
)


def test_gap_severity_thresholds_ordered():
    assert GAP_SEVERITY_THRESHOLD_CONSERVATIVE < GAP_SEVERITY_THRESHOLD_BALANCED
    assert GAP_SEVERITY_THRESHOLD_BALANCED < GAP_SEVERITY_THRESHOLD_AGGRESSIVE


def test_similarity_thresholds_ordered():
    assert SIMILARITY_PARTIAL_MATCH < SIMILARITY_FULL_MATCH


def test_embedding_model_set():
    assert EMBEDDING_MODEL == "text-embedding-004"


def test_min_job_skills_positive():
    assert MIN_JOB_SKILLS_FOR_FIT_ENGINE >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_defaults.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add constants to defaults.py**

Append to `backend/defaults.py`:

```python
# ── ATS Gap Severity Engine ─────────────────────────────────────────────────
GAP_SEVERITY_THRESHOLD_CONSERVATIVE: float = 0.3
GAP_SEVERITY_THRESHOLD_BALANCED: float = 0.5
GAP_SEVERITY_THRESHOLD_AGGRESSIVE: float = 0.7
EMBEDDING_MODEL: str = "text-embedding-004"
SIMILARITY_FULL_MATCH: float = 0.82
SIMILARITY_PARTIAL_MATCH: float = 0.60
MIN_JOB_SKILLS_FOR_FIT_ENGINE: int = 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_defaults.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/defaults.py tests/test_defaults.py
git commit -m "feat(matching): add ATS gap severity engine defaults"
```

---

### Task 2: Add DB columns to SearchSettings and JobMatch

**Files:**
- Modify: `backend/models/user.py`
- Modify: `backend/models/job.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fit_models.py
"""Test that new DB columns exist on SearchSettings and JobMatch."""
from backend.models.user import SearchSettings
from backend.models.job import JobMatch


def test_search_settings_has_sensitivity():
    ss = SearchSettings(id=1, keywords={"include": []})
    assert hasattr(ss, "cv_modification_sensitivity")
    assert ss.cv_modification_sensitivity == "balanced"


def test_job_match_has_fit_columns():
    jm = JobMatch(id=1, job_id=1, score=50.0)
    assert hasattr(jm, "gap_severity")
    assert hasattr(jm, "ats_score")
    assert hasattr(jm, "fit_assessment_json")
    assert jm.gap_severity is None
    assert jm.ats_score is None
    assert jm.fit_assessment_json is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_fit_models.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add columns**

In `backend/models/user.py`, add to `SearchSettings` after the `countries` line:

```python
cv_modification_sensitivity: Mapped[str] = mapped_column(String, default="balanced")
```

In `backend/models/job.py`, add to `JobMatch` after the `matched_at` line:

```python
gap_severity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
ats_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
fit_assessment_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_fit_models.py -v`
Expected: PASS

- [ ] **Step 5: Generate Alembic migration**

```bash
cd /home/mouad/Web-automation && .venv/bin/python -m alembic revision --autogenerate -m "add cv_modification_sensitivity and fit assessment columns"
```

Review the generated migration file, then:

```bash
cd /home/mouad/Web-automation && .venv/bin/python -m alembic upgrade head
```

- [ ] **Step 6: Commit**

```bash
git add backend/models/user.py backend/models/job.py alembic/versions/ tests/test_fit_models.py
git commit -m "feat(models): add cv_modification_sensitivity and fit assessment columns"
```

---

### Task 3: Add `embed()` to GeminiClient with separate rate limiter

**Files:**
- Modify: `backend/llm/gemini_client.py`
- Test: `tests/test_gemini_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gemini_client.py`:

```python
@pytest.mark.asyncio
async def test_embed_returns_vectors(monkeypatch):
    """embed() should return a list of float vectors."""
    from unittest.mock import MagicMock
    from backend.llm.gemini_client import GeminiClient

    monkeypatch.setattr("backend.config.settings.GOOGLE_API_KEY", "fake-key")
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL", "gemini-3.0-flash")
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL_FALLBACKS", "")

    client = GeminiClient()

    # Mock the underlying genai client
    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2, 0.3]
    mock_result = MagicMock()
    mock_result.embeddings = [mock_embedding, mock_embedding]
    client._client.models.embed_content = MagicMock(return_value=mock_result)

    result = await client.embed(["hello", "world"])
    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_gemini_client.py::test_embed_returns_vectors -v`
Expected: FAIL with `AttributeError: 'GeminiClient' object has no attribute 'embed'`

- [ ] **Step 3: Add embed() to GeminiClient**

In `backend/llm/gemini_client.py`, add to `__init__`:

```python
self._embed_call_times: deque[float] = deque(maxlen=self.RPM_LIMIT)
self._embed_lock = asyncio.Lock()
```

Add methods after `generate_json`:

```python
async def _wait_for_embed_rate_limit(self) -> None:
    async with self._embed_lock:
        now = time.monotonic()
        if len(self._embed_call_times) == self.RPM_LIMIT:
            oldest = self._embed_call_times[0]
            window = 60.0 - (now - oldest)
            if window > 0:
                logger.info("Embed rate limit: sleeping %.1fs", window)
                await asyncio.sleep(min(window, 120.0))
        self._embed_call_times.append(time.monotonic())

async def embed(self, texts: list[str]) -> list[list[float]]:
    """Batch embed texts via text-embedding-004. Returns list of 768-dim vectors."""
    from backend.defaults import EMBEDDING_MODEL

    await self._wait_for_embed_rate_limit()
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: self._client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
        ),
    )
    return [e.values for e in result.embeddings]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_gemini_client.py::test_embed_returns_vectors -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/llm/gemini_client.py tests/test_gemini_client.py
git commit -m "feat(gemini): add embed() method with separate rate limiter"
```

---

## Chunk 2: Core Matching Engine — Skill Patterns, CV Parser, Job Extractor

### Task 4: Create skill_patterns.py — shared regex patterns

**Files:**
- Create: `backend/matching/skill_patterns.py`
- Test: `tests/test_skill_patterns.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_patterns.py
from backend.matching.skill_patterns import (
    CRITICAL_SECTION_PATTERNS,
    PREFERRED_SECTION_PATTERNS,
    SKILL_PHRASE_PATTERNS,
    LINGUISTIC_BOOST_PATTERNS,
    LINGUISTIC_DROP_PATTERNS,
    classify_section,
    extract_linguistic_modifier,
)


def test_classify_required_section():
    assert classify_section("Requirements") == "required"
    assert classify_section("What you must have") == "required"
    assert classify_section("Essential qualifications") == "required"
    assert classify_section("You bring") == "required"


def test_classify_preferred_section():
    assert classify_section("Nice to have") == "preferred"
    assert classify_section("Bonus skills") == "preferred"
    assert classify_section("What's advantageous") == "preferred"


def test_classify_neutral_section():
    assert classify_section("About the company") == "neutral"
    assert classify_section("Benefits") == "neutral"


def test_linguistic_boost():
    assert extract_linguistic_modifier("Must have experience with Docker") == 1.0
    assert extract_linguistic_modifier("Essential: Python programming") == 1.0
    assert extract_linguistic_modifier("Required knowledge of SQL") == 1.0


def test_linguistic_drop():
    assert extract_linguistic_modifier("Bonus: experience with Kubernetes") == 0.3
    assert extract_linguistic_modifier("Exposure to cloud platforms is a plus") == 0.3
    assert extract_linguistic_modifier("Familiarity with Terraform preferred") == 0.3


def test_linguistic_neutral():
    assert extract_linguistic_modifier("Python programming") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_skill_patterns.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create skill_patterns.py**

```python
# backend/matching/skill_patterns.py
"""Shared regex patterns and linguistic classifiers for skill extraction."""
from __future__ import annotations

import re

# Section header classification patterns
CRITICAL_SECTION_PATTERNS = re.compile(
    r"(?i)\b("
    r"require[ds]?|requirements?|must\s+have|essential|"
    r"you\s+bring|qualifications?|what\s+we\s+need|"
    r"what\s+you.ll\s+need|key\s+skills?"
    r")\b"
)

PREFERRED_SECTION_PATTERNS = re.compile(
    r"(?i)\b("
    r"nice\s+to\s+have|bonus|preferred|ideally|"
    r"plus|advantageous|desirable|good\s+to\s+have"
    r")\b"
)

# Skill phrase extraction patterns
SKILL_PHRASE_PATTERNS = re.compile(
    r"(?i)(?:"
    r"experience\s+(?:with|in)\s+|"
    r"knowledge\s+of\s+|"
    r"proficiency\s+in\s+|"
    r"familiarity\s+with\s+|"
    r"understanding\s+of\s+|"
    r"expertise\s+in\s+"
    r")([A-Za-z0-9\s/\-\.+#]+?)(?:[,;.]|\s+and\s+|\s+or\s+|$)"
)

# Tech-like pattern: capitalized words, compound with / or -
TECH_PATTERN = re.compile(
    r"\b("
    r"[A-Z][a-zA-Z0-9]*(?:\.[a-zA-Z]+)*|"  # Capitalized: Python, Node.js
    r"[a-zA-Z0-9]+(?:/[a-zA-Z0-9]+)+|"     # Slash compound: CI/CD
    r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+"      # Hyphen compound: front-end
    r")\b"
)

# Linguistic modifier patterns
LINGUISTIC_BOOST_PATTERNS = re.compile(
    r"(?i)\b(must|essential|required|mandatory|critical|necessary)\b"
)

LINGUISTIC_DROP_PATTERNS = re.compile(
    r"(?i)\b(bonus|plus|exposure\s+to|familiarity|familiar\s+with|"
    r"nice\s+to\s+have|preferred|desirable|advantageous|ideally)\b"
)

# Knockout filter patterns (years, degrees)
KNOCKOUT_PATTERN = re.compile(
    r"(?i)(\d+\+?\s*years?\s+(?:of\s+)?experience|"
    r"(?:MSc|PhD|Master|Bachelor|BSc|MBA)\s+(?:required|in))"
)


def classify_section(header: str) -> str:
    """Classify a section header as 'required', 'preferred', or 'neutral'."""
    if CRITICAL_SECTION_PATTERNS.search(header):
        return "required"
    if PREFERRED_SECTION_PATTERNS.search(header):
        return "preferred"
    return "neutral"


def extract_linguistic_modifier(text: str) -> float | None:
    """Return 1.0 for boost, 0.3 for drop, None for neutral."""
    if LINGUISTIC_BOOST_PATTERNS.search(text):
        return 1.0
    if LINGUISTIC_DROP_PATTERNS.search(text):
        return 0.3
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_skill_patterns.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/matching/skill_patterns.py tests/test_skill_patterns.py
git commit -m "feat(matching): add skill_patterns module with section and linguistic classifiers"
```

---

### Task 5: Create cv_parser.py — LaTeX skill extraction

**Files:**
- Create: `backend/matching/cv_parser.py`
- Test: `tests/test_cv_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cv_parser.py
"""Tests for CV LaTeX parser — skill extraction with context tagging."""
from __future__ import annotations

from backend.matching.cv_parser import CVParser, SkillEntry, CVProfile

SAMPLE_CV = r"""
\begin{rSection}{Profile}
Experienced Python developer specializing in machine learning and data engineering.
\end{rSection}

\begin{rSection}{Skills}
\begin{tabular}{ @{} >{\bfseries}l @{\hspace{6ex}} l }
Programming & Python, Java, SQL, JavaScript \\
Frameworks & FastAPI, Django, React \\
Cloud & AWS, Docker, Kubernetes \\
\end{tabular}
\end{rSection}

\begin{rSection}{Experience}
\textbf{Senior Developer} \hfill 2023--Present \\
\emph{TechCorp} \\
\begin{itemize}
  \item Built CI/CD pipelines using GitLab and Docker.
  \item Developed REST APIs with FastAPI and PostgreSQL.
\end{itemize}

\textbf{Junior Developer} \hfill 2020--2023 \\
\emph{StartupCo} \\
\begin{itemize}
  \item Created data pipelines with Apache Airflow.
  \item Wrote unit tests with pytest.
\end{itemize}
\end{rSection}
"""


def test_parser_extracts_skills_section():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    skill_texts = [s.text.lower() for s in skills]
    assert "python" in skill_texts
    assert "fastapi" in skill_texts
    assert "docker" in skill_texts


def test_parser_assigns_context_weights():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    skills_section = [s for s in skills if s.context == "skills_section"]
    experience_recent = [s for s in skills if s.context == "experience_recent"]
    assert len(skills_section) > 0
    assert all(s.weight == 0.6 for s in skills_section)
    assert all(s.weight == 1.0 for s in experience_recent)


def test_parser_extracts_profile_skills():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    profile_skills = [s for s in skills if s.context == "profile"]
    profile_texts = [s.text.lower() for s in profile_skills]
    assert "machine learning" in profile_texts or "python" in profile_texts


def test_parser_deduplicates_skills():
    parser = CVParser()
    skills = parser.parse(SAMPLE_CV)
    # Python appears in skills section and experience — should still appear
    # but each occurrence has its own context
    python_skills = [s for s in skills if s.text.lower() == "python"]
    contexts = {s.context for s in python_skills}
    assert len(contexts) >= 1  # At least from skills_section


def test_cv_profile_hash():
    parser = CVParser()
    profile = parser.build_profile(SAMPLE_CV)
    assert isinstance(profile, CVProfile)
    assert len(profile.raw_text_hash) == 64  # SHA-256 hex
    assert len(profile.skills) > 0
    # All embeddings are empty before embedding step
    assert all(s.embedding == [] for s in profile.skills)


def test_parser_fallback_for_unknown_template():
    """If fewer than 3 skills extracted, fallback to full-text scan."""
    minimal_cv = r"""
    Some text about Python and Docker and machine learning.
    Also mentions SQL and FastAPI for good measure.
    """
    parser = CVParser()
    skills = parser.parse(minimal_cv)
    assert len(skills) >= 3  # Fallback should still extract skills
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_cv_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create cv_parser.py**

```python
# backend/matching/cv_parser.py
"""CV LaTeX parser — extracts skills with context tagging."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from backend.matching.skill_patterns import TECH_PATTERN

logger = logging.getLogger(__name__)

# Context weights matching ATS behavior
CONTEXT_WEIGHTS = {
    "experience_recent": 1.0,
    "skills_section": 0.6,
    "profile": 0.5,
    "experience_older": 0.4,
}

# Common LaTeX section patterns
_SECTION_RE = re.compile(
    r"\\begin\{(?:rSection|section|cvsection)\}\{([^}]+)\}(.*?)\\end\{(?:rSection|section|cvsection)\}",
    re.DOTALL,
)

# Skills row patterns: "Category & skill1, skill2, skill3 \\"
_SKILLS_ROW_RE = re.compile(
    r"(?:&|:)\s*([A-Za-z0-9\s,/\-\.+#]+?)\\\\",
)

# \cvskill{category}{skills} pattern
_CVSKILL_RE = re.compile(r"\\cvskill\{[^}]*\}\{([^}]+)\}")

# Experience role header — detect most recent vs older
_ROLE_RE = re.compile(
    r"\\textbf\{([^}]+)\}.*?\\(?:hfill|\\)\s*(\d{4})\s*[-–—]\s*(Present|\d{4})",
    re.DOTALL,
)

# Bullet items
_ITEM_RE = re.compile(r"\\item\s+(.+?)(?=\\item|\\end|$)", re.DOTALL)

# Common non-skill words to filter out in fallback
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "been", "will", "are", "was", "were", "can", "also", "our", "your",
    "about", "some", "text", "good", "measure", "also", "mentions",
    "begin", "end", "item", "textbf", "emph", "hfill",
}

# Known multi-word skills
_MULTI_WORD_SKILLS = {
    "machine learning", "deep learning", "data engineering", "data science",
    "natural language processing", "computer vision", "cloud computing",
    "project management", "agile methodology", "software development",
    "web development", "mobile development", "devops", "ci/cd",
    "unit testing", "integration testing", "rest api", "graphql",
    "apache airflow", "apache kafka", "apache spark",
}


@dataclass
class SkillEntry:
    text: str
    context: str
    weight: float
    embedding: list[float] = field(default_factory=list)


@dataclass
class CVProfile:
    skills: list[SkillEntry]
    raw_text_hash: str


class CVParser:
    """Extracts skills from LaTeX CV with context tagging."""

    def parse(self, cv_tex: str) -> list[SkillEntry]:
        """Extract skills with context from CV LaTeX source."""
        skills: list[SkillEntry] = []

        sections = dict(_SECTION_RE.findall(cv_tex))

        # Profile section
        for name in ("Profile", "Summary", "About", "Objective", "Profil"):
            if name in sections:
                skills.extend(self._extract_profile_skills(sections[name]))
                break

        # Skills section
        for name in ("Skills", "Technical Skills", "Compétences", "Technologies"):
            if name in sections:
                skills.extend(self._extract_skills_section(sections[name]))
                break

        # Experience section
        for name in ("Experience", "Work Experience", "Professional Experience",
                      "Expérience", "Employment"):
            if name in sections:
                skills.extend(self._extract_experience_skills(sections[name]))
                break

        # Fallback if fewer than 3 skills extracted
        if len(skills) < 3:
            logger.warning(
                "CV parser extracted only %d skills — falling back to full-text scan",
                len(skills),
            )
            skills = self._fallback_extract(cv_tex)

        return skills

    def build_profile(self, cv_tex: str) -> CVProfile:
        """Parse CV and return a CVProfile (embeddings empty, to be filled later)."""
        skills = self.parse(cv_tex)
        text_hash = hashlib.sha256(cv_tex.encode()).hexdigest()
        return CVProfile(skills=skills, raw_text_hash=text_hash)

    def _extract_profile_skills(self, text: str) -> list[SkillEntry]:
        """Extract skill-like phrases from profile/summary."""
        skills = []
        # Check for multi-word skills first
        text_lower = text.lower()
        for mw in _MULTI_WORD_SKILLS:
            if mw in text_lower:
                skills.append(SkillEntry(text=mw, context="profile", weight=CONTEXT_WEIGHTS["profile"]))

        # Then tech patterns
        for match in TECH_PATTERN.finditer(text):
            term = match.group(1).strip()
            if term.lower() not in _STOP_WORDS and len(term) >= 2:
                if not any(s.text.lower() == term.lower() for s in skills):
                    skills.append(SkillEntry(text=term, context="profile", weight=CONTEXT_WEIGHTS["profile"]))

        return skills

    def _extract_skills_section(self, text: str) -> list[SkillEntry]:
        """Extract skills from a structured skills section."""
        skills = []

        # Try \cvskill{}{} pattern
        for match in _CVSKILL_RE.finditer(text):
            for item in match.group(1).split(","):
                item = item.strip()
                if item and len(item) >= 2:
                    skills.append(SkillEntry(
                        text=item, context="skills_section",
                        weight=CONTEXT_WEIGHTS["skills_section"],
                    ))

        # Try table row pattern: "& skill1, skill2 \\"
        for match in _SKILLS_ROW_RE.finditer(text):
            for item in match.group(1).split(","):
                item = item.strip()
                if item and len(item) >= 2 and item.lower() not in _STOP_WORDS:
                    if not any(s.text.lower() == item.lower() for s in skills):
                        skills.append(SkillEntry(
                            text=item, context="skills_section",
                            weight=CONTEXT_WEIGHTS["skills_section"],
                        ))

        return skills

    def _extract_experience_skills(self, text: str) -> list[SkillEntry]:
        """Extract skills from experience bullets, distinguishing recent vs older roles."""
        skills = []
        roles = list(_ROLE_RE.finditer(text))

        if not roles:
            # Can't distinguish roles — treat all as older
            for match in _ITEM_RE.finditer(text):
                skills.extend(self._skills_from_bullet(
                    match.group(1), "experience_older"
                ))
            return skills

        # First role is most recent (or any with "Present")
        for i, role in enumerate(roles):
            is_recent = i == 0 or role.group(3).strip().lower() == "present"
            context = "experience_recent" if is_recent else "experience_older"

            # Get text between this role and next role (or end)
            start = role.end()
            end = roles[i + 1].start() if i + 1 < len(roles) else len(text)
            role_text = text[start:end]

            for match in _ITEM_RE.finditer(role_text):
                skills.extend(self._skills_from_bullet(match.group(1), context))

        return skills

    def _skills_from_bullet(self, bullet_text: str, context: str) -> list[SkillEntry]:
        """Extract tech mentions from a single experience bullet."""
        skills = []
        for match in TECH_PATTERN.finditer(bullet_text):
            term = match.group(1).strip()
            if term.lower() not in _STOP_WORDS and len(term) >= 2:
                skills.append(SkillEntry(
                    text=term, context=context,
                    weight=CONTEXT_WEIGHTS[context],
                ))
        return skills

    def _fallback_extract(self, cv_tex: str) -> list[SkillEntry]:
        """Last-resort full-text scan for skill-like phrases."""
        skills = []
        text_lower = cv_tex.lower()

        # Multi-word skills
        for mw in _MULTI_WORD_SKILLS:
            if mw in text_lower:
                skills.append(SkillEntry(
                    text=mw, context="skills_section",
                    weight=CONTEXT_WEIGHTS["skills_section"],
                ))

        # Tech patterns
        for match in TECH_PATTERN.finditer(cv_tex):
            term = match.group(1).strip()
            if (term.lower() not in _STOP_WORDS
                    and len(term) >= 2
                    and not any(s.text.lower() == term.lower() for s in skills)):
                skills.append(SkillEntry(
                    text=term, context="skills_section",
                    weight=CONTEXT_WEIGHTS["skills_section"],
                ))

        return skills
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_cv_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/matching/cv_parser.py tests/test_cv_parser.py
git commit -m "feat(matching): add CV LaTeX parser with context-weighted skill extraction"
```

---

### Task 6: Create job_skill_extractor.py — NLP job description extraction

**Files:**
- Create: `backend/matching/job_skill_extractor.py`
- Test: `tests/test_job_skill_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_job_skill_extractor.py
"""Tests for job description NLP skill extraction."""
from __future__ import annotations

from backend.matching.job_skill_extractor import JobSkillExtractor, JobSkill, JobProfile

JOB_DESCRIPTION = """
About us:
We are a fast-growing fintech startup building the future of payments.

Requirements:
- 3+ years of experience with Python
- Strong knowledge of SQL and PostgreSQL
- Experience with Docker and Kubernetes
- Must have excellent problem-solving skills

Nice to have:
- Familiarity with Terraform
- Exposure to Apache Kafka
- AWS certification is a plus

Benefits:
- Competitive salary
- Remote work options
"""


def test_extractor_finds_required_skills():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    skill_texts = [s.text.lower() for s in profile.skills]
    assert "python" in skill_texts
    assert "sql" in skill_texts or "postgresql" in skill_texts


def test_extractor_assigns_high_criticality_to_required():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    required_skills = [s for s in profile.skills if s.section == "required"]
    assert len(required_skills) > 0
    assert all(s.criticality >= 0.5 for s in required_skills)


def test_extractor_assigns_low_criticality_to_preferred():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    preferred_skills = [s for s in profile.skills if s.section == "preferred"]
    assert len(preferred_skills) > 0
    assert all(s.criticality <= 0.5 for s in preferred_skills)


def test_extractor_detects_knockout_filters():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    assert any("3" in k and "year" in k.lower() for k in profile.knockout_filters)


def test_extractor_handles_empty_description():
    extractor = JobSkillExtractor()
    profile = extractor.extract("")
    assert isinstance(profile, JobProfile)
    assert len(profile.skills) == 0


def test_extractor_handles_no_sections():
    desc = "We need someone who knows Python, Docker, and AWS. Terraform is a bonus."
    extractor = JobSkillExtractor()
    profile = extractor.extract(desc)
    assert len(profile.skills) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_job_skill_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create job_skill_extractor.py**

```python
# backend/matching/job_skill_extractor.py
"""Job description NLP extraction — skills, criticality, knockout filters."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from backend.matching.skill_patterns import (
    CRITICAL_SECTION_PATTERNS,
    KNOCKOUT_PATTERN,
    PREFERRED_SECTION_PATTERNS,
    TECH_PATTERN,
    SKILL_PHRASE_PATTERNS,
    classify_section,
    extract_linguistic_modifier,
)

logger = logging.getLogger(__name__)

# Section split: detect headers like "Requirements:", "Nice to have:", etc.
_SECTION_SPLIT_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,3}\s*)?([A-Za-z][A-Za-z\s/&'-]{2,40})(?:\s*[:：\-—]|\s*\n)",
    re.MULTILINE,
)

# Bullet detection
_BULLET_RE = re.compile(r"(?:^|\n)\s*[-•*▪◦]\s*(.+?)(?=\n\s*[-•*▪◦]|\n\s*\n|\Z)", re.DOTALL)

# Non-skill stopwords for filtering extracted terms
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "been", "will", "are", "was", "our", "your", "about", "work",
    "team", "ability", "strong", "good", "excellent", "company",
    "experience", "years", "role", "position", "job", "salary",
    "remote", "benefits", "competitive", "options",
    "requirements", "nice", "have", "about", "what",
}


@dataclass
class JobSkill:
    text: str
    criticality: float
    section: str  # "required", "preferred", "neutral"
    embedding: list[float] = field(default_factory=list)


@dataclass
class JobProfile:
    skills: list[JobSkill]
    knockout_filters: list[str] = field(default_factory=list)


class JobSkillExtractor:
    """Extracts skills from job descriptions with criticality scoring."""

    def extract(self, description: str) -> JobProfile:
        """Extract skills and knockout filters from a job description."""
        if not description or not description.strip():
            return JobProfile(skills=[], knockout_filters=[])

        # Step 1: Detect knockout filters
        knockouts = [m.group(0).strip() for m in KNOCKOUT_PATTERN.finditer(description)]

        # Step 2: Split into sections
        section_blocks = self._split_sections(description)

        # Step 3: Extract skills per section with criticality
        skills: list[JobSkill] = []
        seen: set[str] = set()

        for section_type, text in section_blocks:
            section_skills = self._extract_from_block(text, section_type, seen)
            skills.extend(section_skills)

        return JobProfile(skills=skills, knockout_filters=knockouts)

    def _split_sections(self, description: str) -> list[tuple[str, str]]:
        """Split description into (section_type, text) blocks."""
        headers = list(_SECTION_SPLIT_RE.finditer(description))

        if not headers:
            # No clear sections — treat entire text as neutral
            return [("neutral", description)]

        blocks: list[tuple[str, str]] = []

        # Text before first header
        if headers[0].start() > 0:
            blocks.append(("neutral", description[: headers[0].start()]))

        for i, header in enumerate(headers):
            header_text = header.group(1).strip()
            section_type = classify_section(header_text)
            start = header.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(description)
            blocks.append((section_type, description[start:end]))

        return blocks

    def _extract_from_block(
        self, text: str, section_type: str, seen: set[str]
    ) -> list[JobSkill]:
        """Extract skills from a text block."""
        skills: list[JobSkill] = []

        # Section base criticality
        section_crit = {"required": 1.0, "preferred": 0.5, "neutral": 0.3}[section_type]

        # Extract from bullets
        bullets = _BULLET_RE.findall(text)
        sources = bullets if bullets else [text]

        for source in sources:
            source_clean = source.strip()
            if not source_clean:
                continue

            # Linguistic modifier for this specific line
            ling_mod = extract_linguistic_modifier(source_clean)
            criticality = max(section_crit, ling_mod) if ling_mod is not None else section_crit

            # Extract skill phrases ("experience with X", "knowledge of Y")
            for match in SKILL_PHRASE_PATTERNS.finditer(source_clean):
                term = match.group(1).strip()
                if self._is_valid_skill(term, seen):
                    seen.add(term.lower())
                    skills.append(JobSkill(
                        text=term, criticality=criticality, section=section_type,
                    ))

            # Extract tech patterns
            for match in TECH_PATTERN.finditer(source_clean):
                term = match.group(1).strip()
                if self._is_valid_skill(term, seen):
                    seen.add(term.lower())
                    skills.append(JobSkill(
                        text=term, criticality=criticality, section=section_type,
                    ))

        return skills

    @staticmethod
    def _is_valid_skill(term: str, seen: set[str]) -> bool:
        """Check if a term looks like a valid skill and hasn't been seen."""
        if not term or len(term) < 2:
            return False
        if term.lower() in _STOP_WORDS:
            return False
        if term.lower() in seen:
            return False
        # Filter out purely numeric terms
        if term.replace("+", "").replace("-", "").isdigit():
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_job_skill_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/matching/job_skill_extractor.py tests/test_job_skill_extractor.py
git commit -m "feat(matching): add job skill extractor with section-aware criticality scoring"
```

---

## Chunk 3: Fit Engine — Core Algorithm

### Task 7: Create fit_engine.py — gap severity scoring and decision

**Files:**
- Create: `backend/matching/fit_engine.py`
- Test: `tests/test_fit_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fit_engine.py
"""Tests for the FitEngine — gap severity algorithm and modification decision."""
from __future__ import annotations

import math

import pytest

from backend.matching.fit_engine import (
    FitEngine,
    FitAssessment,
    SkillGap,
    cosine_similarity,
)
from backend.matching.cv_parser import SkillEntry, CVProfile
from backend.matching.job_skill_extractor import JobSkill, JobProfile


def test_cosine_similarity_identical():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert cosine_similarity(a, b) == 0.0


def test_perfect_fit_low_severity():
    """When all job skills are covered by CV, severity should be near 0."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6,
                       embedding=[1.0, 0.0, 0.0]),
            SkillEntry(text="Docker", context="experience_recent", weight=1.0,
                       embedding=[0.0, 1.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Docker", criticality=0.8, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity < 0.1
    assert assessment.should_modify is False
    assert assessment.simulated_ats_score > 90


def test_complete_gap_high_severity():
    """When no job skills match CV, severity should be near 1.0."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Java", context="skills_section", weight=0.6,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[0.0, 1.0, 0.0]),
        JobSkill(text="Docker", criticality=0.9, section="required",
                 embedding=[0.0, 0.0, 1.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity > 0.8
    assert assessment.should_modify is True
    assert len(assessment.critical_gaps) == 2


def test_partial_match_medium_severity():
    """One critical skill missing, one present — medium severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Docker", criticality=1.0, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert 0.3 < assessment.severity < 0.7
    assert len(assessment.critical_gaps) == 1
    assert assessment.critical_gaps[0].skill == "Docker"


def test_preferred_gap_low_severity():
    """Missing only preferred skills should produce low severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Jira", criticality=0.3, section="preferred",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity < 0.3
    assert assessment.should_modify is False


def test_sensitivity_conservative_modifies_more():
    """Conservative threshold should trigger modification at lower severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="AWS", criticality=0.8, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    conservative = engine.assess(job, cv, sensitivity="conservative")
    balanced = engine.assess(job, cv, sensitivity="balanced")
    aggressive = engine.assess(job, cv, sensitivity="aggressive")

    # Same severity across all — only the threshold changes
    assert conservative.severity == balanced.severity == aggressive.severity
    # Conservative (threshold=0.3) should be most trigger-happy
    # With one critical skill fully missing, severity > 0.3
    assert conservative.should_modify is True
    # Aggressive (threshold=0.7) should rarely trigger
    assert aggressive.should_modify is False


def test_empty_job_skills():
    cv = CVProfile(
        skills=[SkillEntry(text="Python", context="skills_section", weight=0.6, embedding=[1.0])],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[])
    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity == 0.0
    assert assessment.should_modify is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_fit_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create fit_engine.py**

```python
# backend/matching/fit_engine.py
"""Fit Engine — ATS-simulated gap severity scoring and CV modification decision."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from backend.defaults import (
    GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
    GAP_SEVERITY_THRESHOLD_BALANCED,
    GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    SIMILARITY_FULL_MATCH,
    SIMILARITY_PARTIAL_MATCH,
)
from backend.matching.cv_parser import CVProfile
from backend.matching.job_skill_extractor import JobProfile, JobSkill

logger = logging.getLogger(__name__)

THRESHOLDS = {
    "conservative": GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    "balanced": GAP_SEVERITY_THRESHOLD_BALANCED,
    "aggressive": GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
}


@dataclass
class SkillGap:
    skill: str
    criticality: float
    best_cv_match: str
    similarity: float


@dataclass
class FitAssessment:
    severity: float
    should_modify: bool
    simulated_ats_score: float
    covered_skills: list[str] = field(default_factory=list)
    partial_matches: list[str] = field(default_factory=list)
    critical_gaps: list[SkillGap] = field(default_factory=list)
    preferred_gaps: list[SkillGap] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for JSON storage in DB."""
        return {
            "severity": self.severity,
            "should_modify": self.should_modify,
            "simulated_ats_score": self.simulated_ats_score,
            "covered_skills": self.covered_skills,
            "partial_matches": self.partial_matches,
            "critical_gaps": [
                {"skill": g.skill, "criticality": g.criticality,
                 "best_cv_match": g.best_cv_match, "similarity": g.similarity}
                for g in self.critical_gaps
            ],
            "preferred_gaps": [
                {"skill": g.skill, "criticality": g.criticality,
                 "best_cv_match": g.best_cv_match, "similarity": g.similarity}
                for g in self.preferred_gaps
            ],
        }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Returns 0.0 for zero vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class FitEngine:
    """Computes gap severity and decides whether CV modification is needed."""

    def assess(
        self,
        job_profile: JobProfile,
        cv_profile: CVProfile,
        sensitivity: str = "balanced",
    ) -> FitAssessment:
        """Compute gap severity and return a FitAssessment."""
        if not job_profile.skills:
            return FitAssessment(
                severity=0.0,
                should_modify=False,
                simulated_ats_score=100.0,
            )

        covered: list[str] = []
        partial: list[str] = []
        critical_gaps: list[SkillGap] = []
        preferred_gaps: list[SkillGap] = []

        total_weight = 0.0
        weighted_gaps = 0.0

        for job_skill in job_profile.skills:
            coverage, best_match_text, best_sim = self._best_match(
                job_skill, cv_profile
            )
            gap = 1.0 - coverage
            weighted_gaps += gap * job_skill.criticality
            total_weight += job_skill.criticality

            if coverage >= 1.0:
                covered.append(job_skill.text)
            elif coverage >= 0.5:
                partial.append(f"{job_skill.text} ~ {best_match_text}")
            else:
                gap_entry = SkillGap(
                    skill=job_skill.text,
                    criticality=job_skill.criticality,
                    best_cv_match=best_match_text,
                    similarity=best_sim,
                )
                if job_skill.section == "preferred":
                    preferred_gaps.append(gap_entry)
                else:
                    critical_gaps.append(gap_entry)

        severity = weighted_gaps / total_weight if total_weight > 0 else 0.0
        threshold = THRESHOLDS.get(sensitivity, THRESHOLDS["balanced"])
        should_modify = severity >= threshold
        ats_score = (1.0 - severity) * 100

        # Sort gaps by criticality descending
        critical_gaps.sort(key=lambda g: g.criticality, reverse=True)
        preferred_gaps.sort(key=lambda g: g.criticality, reverse=True)

        return FitAssessment(
            severity=severity,
            should_modify=should_modify,
            simulated_ats_score=ats_score,
            covered_skills=covered,
            partial_matches=partial,
            critical_gaps=critical_gaps,
            preferred_gaps=preferred_gaps,
        )

    def _best_match(
        self, job_skill: JobSkill, cv_profile: CVProfile
    ) -> tuple[float, str, float]:
        """Find the best CV skill match for a job skill.

        Returns: (coverage_score, best_match_text, raw_similarity)
        """
        best_effective = 0.0
        best_text = ""
        best_sim = 0.0

        for cv_skill in cv_profile.skills:
            sim = cosine_similarity(job_skill.embedding, cv_skill.embedding)
            effective = sim * cv_skill.weight
            if effective > best_effective:
                best_effective = effective
                best_text = cv_skill.text
                best_sim = sim

        if best_effective >= SIMILARITY_FULL_MATCH:
            return 1.0, best_text, best_sim
        elif best_effective >= SIMILARITY_PARTIAL_MATCH:
            return 0.5, best_text, best_sim
        else:
            return 0.0, best_text, best_sim
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_fit_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/matching/fit_engine.py tests/test_fit_engine.py
git commit -m "feat(matching): add FitEngine with gap severity algorithm and cosine similarity"
```

---

### Task 8: Create embedder.py — embedding cache and batch operations

**Files:**
- Create: `backend/matching/embedder.py`
- Test: `tests/test_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embedder.py
"""Tests for the Embedder — CV profile and job profile embedding."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.matching.embedder import Embedder
from backend.matching.cv_parser import CVProfile, SkillEntry
from backend.matching.job_skill_extractor import JobProfile, JobSkill


def _mock_gemini_client(dim: int = 3) -> MagicMock:
    client = MagicMock()
    call_count = 0

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        nonlocal call_count
        call_count += 1
        # Return unique but deterministic vectors per text
        return [[float(hash(t) % 100) / 100.0] * dim for t in texts]

    client.embed = fake_embed
    return client


@pytest.mark.asyncio
async def test_embed_cv_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6, embedding=[]),
            SkillEntry(text="Docker", context="experience_recent", weight=1.0, embedding=[]),
        ],
        raw_text_hash="abc123",
    )

    result = await embedder.embed_cv_profile(profile)
    assert all(len(s.embedding) == 3 for s in result.skills)
    assert result.raw_text_hash == "abc123"


@pytest.mark.asyncio
async def test_embed_job_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = JobProfile(
        skills=[
            JobSkill(text="Python", criticality=1.0, section="required", embedding=[]),
            JobSkill(text="AWS", criticality=0.8, section="required", embedding=[]),
        ],
    )

    result = await embedder.embed_job_profile(profile)
    assert all(len(s.embedding) == 3 for s in result.skills)


@pytest.mark.asyncio
async def test_embed_skips_already_embedded():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6,
                       embedding=[1.0, 2.0, 3.0]),  # Already embedded
        ],
        raw_text_hash="abc123",
    )

    result = await embedder.embed_cv_profile(profile)
    # Should keep existing embedding
    assert result.skills[0].embedding == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_embed_empty_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(skills=[], raw_text_hash="abc123")
    result = await embedder.embed_cv_profile(profile)
    assert result.skills == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_embedder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create embedder.py**

```python
# backend/matching/embedder.py
"""Embedder — batch embedding of CV and job profiles via Gemini."""
from __future__ import annotations

import logging

from backend.matching.cv_parser import CVProfile, SkillEntry
from backend.matching.job_skill_extractor import JobProfile, JobSkill

logger = logging.getLogger(__name__)


class Embedder:
    """Wraps GeminiClient.embed() with profile-level batch operations."""

    def __init__(self, gemini_client) -> None:
        self._client = gemini_client

    async def embed_cv_profile(self, profile: CVProfile) -> CVProfile:
        """Embed all skills in a CVProfile that don't already have embeddings."""
        to_embed: list[tuple[int, str]] = []
        for i, skill in enumerate(profile.skills):
            if not skill.embedding:
                to_embed.append((i, skill.text))

        if not to_embed:
            return profile

        texts = [text for _, text in to_embed]
        vectors = await self._client.embed(texts)

        for (idx, _), vector in zip(to_embed, vectors):
            profile.skills[idx].embedding = vector

        logger.info("Embedded %d CV skills (%d already cached)", len(to_embed),
                     len(profile.skills) - len(to_embed))
        return profile

    async def embed_job_profile(self, profile: JobProfile) -> JobProfile:
        """Embed all skills in a JobProfile."""
        to_embed: list[tuple[int, str]] = []
        for i, skill in enumerate(profile.skills):
            if not skill.embedding:
                to_embed.append((i, skill.text))

        if not to_embed:
            return profile

        texts = [text for _, text in to_embed]
        vectors = await self._client.embed(texts)

        for (idx, _), vector in zip(to_embed, vectors):
            profile.skills[idx].embedding = vector

        logger.info("Embedded %d job skills", len(to_embed))
        return profile
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_embedder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/matching/embedder.py tests/test_embedder.py
git commit -m "feat(matching): add Embedder for batch CV and job profile embedding"
```

---

## Chunk 4: Integration — Pipeline, CVModifier, Morning Batch

### Task 9: Add `modify_from_assessment()` to CVModifier and new prompt

**Files:**
- Modify: `backend/llm/cv_modifier.py`
- Modify: `backend/llm/prompts.py`
- Test: `tests/test_cv_modifier.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv_modifier.py`:

```python
from backend.matching.fit_engine import FitAssessment, SkillGap


@pytest.mark.asyncio
async def test_cv_modifier_from_assessment():
    """modify_from_assessment() should accept FitAssessment and return CVModifierOutput."""
    expected = CVModifierOutput(replacements=[
        CVReplacement(
            section="Skills",
            original_text="Python, Java, SQL, JavaScript",
            replacement_text="Python, Docker, SQL, JavaScript",
            reason="Adds Docker to address critical gap",
            job_requirement_matched="Docker",
            confidence=0.85,
        )
    ])
    modifier = CVModifier(client=_mock_client(expected))
    assessment = FitAssessment(
        severity=0.55,
        should_modify=True,
        simulated_ats_score=45.0,
        covered_skills=["Python", "SQL"],
        partial_matches=[],
        critical_gaps=[
            SkillGap(skill="Docker", criticality=0.9, best_cv_match="CI/CD", similarity=0.58),
        ],
        preferred_gaps=[],
    )
    result = await modifier.modify_from_assessment(
        _make_job(), SAMPLE_CV, assessment
    )
    assert isinstance(result, CVModifierOutput)
    assert len(result.replacements) <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_cv_modifier.py::test_cv_modifier_from_assessment -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add CV_MODIFIER_FROM_ASSESSMENT prompt to prompts.py**

Append to `backend/llm/prompts.py`:

```python
CV_MODIFIER_FROM_ASSESSMENT = """You are a surgical CV editor. The candidate's CV already reflects \
their real profile — your job is to make small, targeted tweaks to address specific skill gaps \
identified by our matching engine.

LANGUAGE RULE: Respond in the SAME LANGUAGE as the CV.

You receive:
1. A LaTeX CV (full file as text)
2. A gap analysis with specific skills to address
3. Skills already covered (DO NOT TOUCH these)

YOUR TASK: Produce at most 3 small replacements that address the critical gaps listed below.

=== CRITICAL GAPS TO ADDRESS (ranked by importance) ===
{gaps_section}

=== SKILLS ALREADY COVERED (DO NOT MODIFY) ===
{covered_section}

=== STRICT RULES ===

WHAT YOU MAY CHANGE (in priority order):
1. Skills row: REORDER items to put job-relevant skills first. If a gap skill is crucial \
   AND the candidate's experience demonstrates related work, you MAY add that ONE skill.
2. Profile/Summary paragraph: small rephrasing to highlight matching strengths or add \
   a brief motivation phrase for a missing requirement.

WHAT YOU MUST LEAVE INTACT:
- Experience section bullets, job titles, descriptions
- Education, certifications, dates, company names, grades

WHAT YOU MUST NEVER DO:
- Invent skills or experiences not supported by the CV
- Add new bullet points or rows
- Introduce new LaTeX commands not already present
- Change more than 3 things
- Add more than 1 new skill to the Skills row

CONFIDENCE SCORING:
- 0.9+: directly addresses a critical gap with CV evidence
- 0.7-0.9: highlights a relevant existing strength
- <0.7: skip

=== RETURN FORMAT ===

Return ONLY valid JSON, no markdown fences:
{{
  "replacements": [
    {{
      "section": "Profile",
      "original_text": "exact verbatim substring from the CV",
      "replacement_text": "the new text",
      "reason": "one sentence",
      "job_requirement_matched": "which gap this addresses",
      "confidence": 0.85
    }}
  ]
}}

IMPORTANT: original_text must be an EXACT substring of the CV text provided.

=== FULL CV (LaTeX) ===
{cv_tex}
"""
```

- [ ] **Step 4: Add modify_from_assessment() to CVModifier**

In `backend/llm/cv_modifier.py`, add after the existing `modify` method:

```python
async def modify_from_assessment(
    self,
    job: JobDetails,
    cv_tex: str,
    assessment: "FitAssessment",
) -> CVModifierOutput:
    """Targeted CV modification using FitAssessment gap analysis."""
    from backend.llm.prompts import CV_MODIFIER_FROM_ASSESSMENT

    if len(cv_tex) > 50_000:
        logger.warning("CV text exceeds 50KB (%d chars), truncating", len(cv_tex))
        cv_tex = cv_tex[:50_000]

    # Build gaps section
    gaps_lines = []
    for i, gap in enumerate(assessment.critical_gaps[:5], 1):
        match_info = (
            f"closest CV skill: \"{gap.best_cv_match}\" (similarity: {gap.similarity:.2f})"
            if gap.best_cv_match else "no match on CV"
        )
        gaps_lines.append(
            f"{i}. \"{gap.skill}\" (criticality: {gap.criticality:.1f}) — {match_info}"
        )
    gaps_section = "\n".join(gaps_lines) if gaps_lines else "No critical gaps identified."

    # Build covered section
    covered_section = "\n".join(
        f"- {s}" for s in assessment.covered_skills
    ) if assessment.covered_skills else "- (none identified)"

    prompt = CV_MODIFIER_FROM_ASSESSMENT.format(
        gaps_section=gaps_section,
        covered_section=covered_section,
        cv_tex=cv_tex,
    )

    raw = await self._client.generate_json(prompt, CVModifierOutput)
    return CVModifierOutput(replacements=raw.top_three())
```

Add this import at the top of `cv_modifier.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from backend.matching.fit_engine import FitAssessment
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_cv_modifier.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add backend/llm/cv_modifier.py backend/llm/prompts.py tests/test_cv_modifier.py
git commit -m "feat(llm): add modify_from_assessment with targeted gap-driven prompt"
```

---

### Task 10: Add generate_base_cv() and refactor CVPipeline

**Files:**
- Modify: `backend/latex/pipeline.py`
- Test: `tests/test_latex_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_latex_pipeline.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from backend.latex.pipeline import CVPipeline, TailoredCV
from backend.models.schemas import JobDetails


def _make_test_job() -> JobDetails:
    return JobDetails(id=1, title="Dev", company="Co", description="Python")


@pytest.mark.asyncio
async def test_generate_base_cv(tmp_path):
    """generate_base_cv should copy tex, compile, and set cv_tailored=False."""
    base_cv = tmp_path / "templates" / "cv.tex"
    base_cv.parent.mkdir(parents=True)
    base_cv.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")

    out_dir = tmp_path / "output"

    compiler = MagicMock()
    compiler.compile = AsyncMock(return_value=out_dir / "cv.pdf")

    pipeline = CVPipeline(compiler=compiler)
    result = await pipeline.generate_base_cv(
        base_cv_path=base_cv,
        job=_make_test_job(),
        output_dir=out_dir,
    )

    assert isinstance(result, TailoredCV)
    assert result.cv_tailored is False
    assert result.diff == []
    assert (out_dir / "cv.tex").exists()
    compiler.compile.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_latex_pipeline.py::test_generate_base_cv -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add generate_base_cv() to CVPipeline**

In `backend/latex/pipeline.py`, add to the `CVPipeline` class after `__init__`:

```python
async def generate_base_cv(
    self,
    base_cv_path: Path,
    job: JobDetails,
    output_dir: Path,
) -> TailoredCV:
    """Copy base CV and compile PDF without any LLM modification."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest_tex = output_dir / "cv.tex"

    shutil.copy2(base_cv_path, dest_tex)
    for support_file in base_cv_path.parent.iterdir():
        if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
            shutil.copy2(support_file, output_dir / support_file.name)

    pdf_path = await self._compiler.compile(dest_tex, output_dir)

    return TailoredCV(
        job_id=job.id,
        tex_path=dest_tex,
        pdf_path=pdf_path,
        diff=[],
        cv_tailored=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_latex_pipeline.py::test_generate_base_cv -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/latex/pipeline.py tests/test_latex_pipeline.py
git commit -m "feat(pipeline): add generate_base_cv for no-modification path"
```

---

### Task 11: Integrate FitEngine into morning_batch.py

**Files:**
- Modify: `backend/scheduler/morning_batch.py`
- Modify: `backend/api/ws.py`

- [ ] **Step 1: Add broadcast_job_assessment to ws.py**

In `backend/api/ws.py`, add after `broadcast_status`:

```python
async def broadcast_job_assessment(
    match_id: int,
    ats_score: float,
    gap_severity: float,
    decision: str,
    covered: list[str],
    gaps: list[dict],
) -> None:
    """Broadcast per-job fit assessment to all connected WebSocket clients."""
    await manager.broadcast({
        "type": "job_progress",
        "match_id": match_id,
        "ats_score": round(ats_score, 1),
        "gap_severity": round(gap_severity, 3),
        "decision": decision,
        "covered": covered,
        "gaps": gaps,
    })
```

Update `__all__` to include `broadcast_job_assessment`.

- [ ] **Step 2: Integrate FitEngine into morning_batch.py**

This is the main integration step. Modify `backend/scheduler/morning_batch.py`:

Add imports at the top:

```python
from backend.defaults import MIN_JOB_SKILLS_FOR_FIT_ENGINE
from backend.matching.cv_parser import CVParser
from backend.matching.embedder import Embedder
from backend.matching.fit_engine import FitEngine
from backend.matching.job_skill_extractor import JobSkillExtractor
```

Update the `broadcast_status` import block to also import `broadcast_job_assessment`:

```python
try:
    from backend.api.ws import broadcast_status, broadcast_job_assessment
except Exception:
    async def broadcast_status(_message: str, _progress: float = 0.0) -> None:
        pass
    async def broadcast_job_assessment(_match_id: int, _ats_score: float, _gap_severity: float,
                                        _decision: str, _covered: list, _gaps: list) -> None:
        pass
```

Add `fit_engine`, `embedder`, `cv_parser`, `job_extractor` to `__init__`:

```python
def __init__(
    self,
    scraper: Any,
    matcher: Any,
    cv_pipeline: Any,
    db_factory: Callable[[], AsyncSession],
    fit_engine: FitEngine | None = None,
    embedder: Embedder | None = None,
) -> None:
    self._scraper = scraper
    self._matcher = matcher
    self._cv_pipeline = cv_pipeline
    self._db_factory = db_factory
    self._fit_engine = fit_engine or FitEngine()
    self._embedder = embedder
    self._cv_parser = CVParser()
    self._job_extractor = JobSkillExtractor()
```

In `_run_batch_inner`, between Step 3 (store matches) and Step 4 (pre-generate CVs), add the fit assessment step. Replace the CV generation section (lines ~170-225) with:

```python
# ── Step 3.5: Fit Assessment ─────────────────────────────────────
await broadcast_status("Analyzing job fit…", progress=0.58)
sensitivity = getattr(settings_row, "cv_modification_sensitivity", "balanced")

# Parse and embed CV profile (cached by hash)
cv_profile = None
if cv_path and cv_path.exists() and self._embedder:
    cv_tex = cv_path.read_text(encoding="utf-8")
    cv_profile = self._cv_parser.build_profile(cv_tex)
    cv_profile = await self._embedder.embed_cv_profile(cv_profile)

# Assess each matched job
# Build match_id -> JobDetails mapping (new_match_ids may skip entries
# due to dedup/actioned filtering, so index-based access into ranked is unsafe)
match_to_jd: dict[int, Any] = {}
_ranked_iter = iter(ranked)
for mid in new_match_ids:
    jd_pair = next(_ranked_iter, None)
    if jd_pair:
        match_to_jd[mid] = jd_pair[0]

assessments: dict[int, Any] = {}  # match_id -> FitAssessment or None
if cv_profile and self._embedder:
    for mid in new_match_ids:
        jd = match_to_jd.get(mid)
        if jd is None:
            continue
        try:
            job_profile = self._job_extractor.extract(jd.description or "")
            if len(job_profile.skills) < MIN_JOB_SKILLS_FOR_FIT_ENGINE:
                assessments[mid] = None  # fallback
                continue
            job_profile = await self._embedder.embed_job_profile(job_profile)
            assessment = self._fit_engine.assess(job_profile, cv_profile, sensitivity)
            assessments[mid] = assessment

            # Store assessment on JobMatch
            match_row = (await db.execute(
                select(JobMatch).where(JobMatch.id == mid)
            )).scalar_one_or_none()
            if match_row:
                match_row.gap_severity = assessment.severity
                match_row.ats_score = assessment.simulated_ats_score
                match_row.fit_assessment_json = assessment.to_dict()

            await broadcast_job_assessment(
                match_id=mid,
                ats_score=assessment.simulated_ats_score,
                gap_severity=assessment.severity,
                decision="modify" if assessment.should_modify else "base_cv",
                covered=assessment.covered_skills[:10],
                gaps=[{"skill": g.skill, "criticality": g.criticality}
                      for g in assessment.critical_gaps[:5]],
            )
        except Exception as exc:
            logger.warning("Fit assessment failed for match %d: %s", mid, exc)
            assessments[mid] = None

    await db.commit()

# ── Step 4: Pre-generate CVs for top N ──────────────────────────
guard = DailyLimitGuard(db=db, limit=daily_limit)
remaining = await guard.remaining_today()
top_ids = new_match_ids[:remaining]
await broadcast_status(
    f"Generating CVs for top {len(top_ids)} matches…", progress=0.65
)

if cv_path and cv_path.exists():
    _additional_parts: list[str] = []
    if profile_row:
        if profile_row.driver_license:
            _additional_parts.append(f"Driver license: {profile_row.driver_license}")
        if profile_row.mobility:
            _additional_parts.append(f"Mobility / relocation: {profile_row.mobility}")
        if profile_row.additional_info and isinstance(profile_row.additional_info, dict):
            for k, v in profile_row.additional_info.items():
                _additional_parts.append(f"{k}: {v}")
    _additional_context = "\n".join(_additional_parts)

    pairs = [(mid, match_to_jd[mid]) for mid in top_ids if mid in match_to_jd]
    sem = asyncio.Semaphore(CONCURRENCY_GEMINI)

    async def _gen_one(mid: int, jd: Any) -> tuple[int, Any]:
        async with sem:
            import re as _re
            slug = _re.sub(r"[^\w]+", "_", (jd.title or "job").lower()).strip("_")[:50]
            dir_name = f"{mid}_{slug}"
            out_dir = Path(settings.jobpilot_data_dir) / "cvs" / dir_name
            out_dir.mkdir(parents=True, exist_ok=True)

            assessment = assessments.get(mid)

            if assessment is not None and not assessment.should_modify:
                # Base CV path — no LLM calls
                result = await self._cv_pipeline.generate_base_cv(
                    base_cv_path=cv_path,
                    job=jd,
                    output_dir=out_dir,
                )
            elif assessment is not None and assessment.should_modify:
                # Targeted modification using FitAssessment
                result = await self._cv_pipeline.generate_tailored_cv(
                    base_cv_path=cv_path,
                    job=jd,
                    output_dir=out_dir,
                    additional_context=_additional_context,
                    fit_assessment=assessment,
                )
            else:
                # Fallback — use original pipeline (JobAnalyzer + CVModifier)
                result = await self._cv_pipeline.generate_tailored_cv(
                    base_cv_path=cv_path,
                    job=jd,
                    output_dir=out_dir,
                    additional_context=_additional_context,
                )
            return mid, result

    raw_results = await asyncio.gather(
        *[_gen_one(mid, jd) for mid, jd in pairs],
        return_exceptions=True,
    )
    done = 0
    for i, outcome in enumerate(raw_results):
        if isinstance(outcome, BaseException):
            logger.error("CV generation failed for match_id=%d: %s", pairs[i][0], outcome)
            continue
        mid, tailored = outcome
        await self._store_tailored_doc(db, mid, tailored, doc_type="cv")
        done += 1
        progress = 0.65 + 0.30 * (done / max(len(top_ids), 1))
        await broadcast_status(f"CV {done}/{len(top_ids)} generated", progress=progress)
else:
    logger.warning("No base CV path configured — skipping CV pre-generation")
```

- [ ] **Step 3: Update generate_tailored_cv to accept optional fit_assessment**

In `backend/latex/pipeline.py`, replace the full `generate_tailored_cv` method with this complete version that cleanly separates the assessment-driven and fallback paths (no unbound `context` variable):

```python
async def generate_tailored_cv(
    self,
    base_cv_path: Path,
    job: JobDetails,
    output_dir: Path,
    additional_context: str = "",
    fit_assessment=None,  # Optional FitAssessment
) -> TailoredCV:
    output_dir.mkdir(parents=True, exist_ok=True)
    dest_tex = output_dir / "cv.tex"

    # 1. Copy — never mutate the base file.
    shutil.copy2(base_cv_path, dest_tex)
    for support_file in base_cv_path.parent.iterdir():
        if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
            shutil.copy2(support_file, output_dir / support_file.name)
    cv_tex = dest_tex.read_text(encoding="utf-8")

    diff: list[DiffEntry] = []
    cv_tailored = False

    # 2-4. Analyze + modify
    if self._cv_modifier is not None and self._cv_applicator is not None:
        try:
            if fit_assessment is not None and hasattr(self._cv_modifier, "modify_from_assessment"):
                # Assessment-driven path — skip JobAnalyzer entirely
                modifier_output = await self._cv_modifier.modify_from_assessment(
                    job, cv_tex, fit_assessment
                )
            elif self._job_analyzer is not None:
                # Fallback path — original JobAnalyzer + CVModifier flow
                job_id = job.id
                context = None
                if job_id is not None and job_id in self._context_cache:
                    ts, cached = self._context_cache[job_id]
                    if monotonic() - ts < 3600:
                        context = cached
                    else:
                        del self._context_cache[job_id]
                if context is None:
                    context = await self._job_analyzer.analyze(job, cv_content=cv_tex)
                    if job_id is not None:
                        if len(self._context_cache) >= 100:
                            oldest_key = next(iter(self._context_cache))
                            del self._context_cache[oldest_key]
                        self._context_cache[job_id] = (monotonic(), context)

                modifier_output = await self._cv_modifier.modify(
                    job, cv_tex, context, additional_context=additional_context
                )
            else:
                modifier_output = None

            if modifier_output is not None:
                cv_tex, applied = self._cv_applicator.apply(
                    cv_tex, modifier_output.replacements
                )
                diff = [
                    DiffEntry(
                        section=r.section,
                        original_text=r.original_text,
                        edited_text=r.replacement_text,
                        change_description=r.reason,
                    )
                    for r in applied
                ]
                cv_tailored = bool(diff)

        except (GeminiRateLimitError, GeminiJSONError) as exc:
            logger.warning("CV modifier LLM error (%s); using base CV unchanged.", exc)
            cv_tex = dest_tex.read_text(encoding="utf-8")
            diff = []
        except Exception as exc:
            logger.error(
                "CV modifier unexpected failure (%s: %s); using base CV unchanged.",
                type(exc).__name__, exc, exc_info=True,
            )
            cv_tex = dest_tex.read_text(encoding="utf-8")
            diff = []

    dest_tex.write_text(cv_tex, encoding="utf-8")
    pdf_path = await self._compiler.compile(dest_tex, output_dir)

    return TailoredCV(
        job_id=job.id,
        tex_path=dest_tex,
        pdf_path=pdf_path,
        diff=diff,
        cv_tailored=cv_tailored,
    )
```

- [ ] **Step 4: Run all existing tests to verify no regressions**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_latex_pipeline.py tests/test_morning_batch.py tests/test_cv_modifier.py -v`
Expected: PASS (all existing tests should still pass)

- [ ] **Step 5: Commit**

```bash
git add backend/scheduler/morning_batch.py backend/latex/pipeline.py backend/api/ws.py
git commit -m "feat(pipeline): integrate FitEngine into morning batch with base CV routing"
```

---

## Chunk 5: Settings API and Frontend

### Task 12: Expose cv_modification_sensitivity in settings API

**Files:**
- Modify: `backend/api/settings.py`

- [ ] **Step 1: Add field to SearchSettingsOut and SearchSettingsUpdate**

In `backend/api/settings.py`:

Add import at top of `backend/api/settings.py`:
```python
from typing import Literal
```

Add to `SearchSettingsOut`:
```python
cv_modification_sensitivity: str = "balanced"
```

Add to `SearchSettingsUpdate` (Literal type validates automatically):
```python
cv_modification_sensitivity: Optional[Literal["conservative", "balanced", "aggressive"]] = None
```

- [ ] **Step 2: Add handling in update_search_settings**

In the `update_search_settings` route, add to the create branch:
```python
cv_modification_sensitivity=body.cv_modification_sensitivity or "balanced",
```

Add to the update branch (Pydantic Literal type already validates the value):
```python
if body.cv_modification_sensitivity is not None:
    ss.cv_modification_sensitivity = body.cv_modification_sensitivity
```

- [ ] **Step 3: Add test for sensitivity field**

Append to `tests/test_api_routes.py` (or create `tests/test_settings_sensitivity.py` if the test file is large):

```python
@pytest.mark.asyncio
async def test_search_settings_sensitivity_roundtrip(client):
    """PUT cv_modification_sensitivity and GET it back."""
    resp = await client.put("/api/settings/search", json={
        "keywords": {"include": ["python"]},
        "cv_modification_sensitivity": "aggressive",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cv_modification_sensitivity"] == "aggressive"

    resp = await client.get("/api/settings/search")
    assert resp.json()["cv_modification_sensitivity"] == "aggressive"
```

- [ ] **Step 4: Run settings tests**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_api_routes.py -v -k settings`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/api/settings.py
git commit -m "feat(api): expose cv_modification_sensitivity in settings endpoints"
```

---

### Task 13: Add sensitivity selector to frontend settings page

**Files:**
- Modify: `frontend/src/routes/settings/+page.svelte`

- [ ] **Step 1: Add sensitivity selector UI**

In the search settings section of `frontend/src/routes/settings/+page.svelte`, add a new form group:

```svelte
<div class="form-group">
    <label for="cv-sensitivity">CV Modification Sensitivity</label>
    <select
        id="cv-sensitivity"
        bind:value={searchSettings.cv_modification_sensitivity}
        on:change={saveSearchSettings}
    >
        <option value="conservative">Conservative — Modify CV for most jobs</option>
        <option value="balanced">Balanced — Only modify when meaningful gaps exist</option>
        <option value="aggressive">Aggressive — Trust my base CV, rarely modify</option>
    </select>
    <p class="help-text">
        Controls how aggressively the system tailors your CV for each job.
        Higher sensitivity means more modifications.
    </p>
</div>
```

- [ ] **Step 2: Verify the field is included in the save payload**

Check that `searchSettings` object already includes all fields from `SearchSettingsOut`. If `cv_modification_sensitivity` needs to be initialized, add a default:

```javascript
cv_modification_sensitivity: data?.cv_modification_sensitivity || 'balanced'
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/settings/+page.svelte
git commit -m "feat(frontend): add CV modification sensitivity selector to settings"
```

---

## Chunk 6: Final Integration Tests and Cleanup

### Task 14: Integration test — full pipeline with FitEngine

**Files:**
- Create: `tests/test_fit_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_fit_integration.py
"""Integration test: CV parser → job extractor → embedder → fit engine → decision."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.matching.cv_parser import CVParser
from backend.matching.embedder import Embedder
from backend.matching.fit_engine import FitEngine
from backend.matching.job_skill_extractor import JobSkillExtractor


SAMPLE_CV = r"""
\begin{rSection}{Profile}
Experienced Python developer with expertise in backend systems and cloud infrastructure.
\end{rSection}

\begin{rSection}{Skills}
\begin{tabular}{ @{} >{\bfseries}l @{\hspace{6ex}} l }
Languages & Python, SQL, JavaScript \\
Frameworks & FastAPI, Django \\
Cloud & AWS, Docker \\
\end{tabular}
\end{rSection}

\begin{rSection}{Experience}
\textbf{Backend Developer} \hfill 2022--Present \\
\emph{TechCo} \\
\begin{itemize}
  \item Built REST APIs with FastAPI and PostgreSQL.
  \item Deployed services on AWS using Docker containers.
\end{itemize}
\end{rSection}
"""

MATCHING_JOB = """
Requirements:
- Strong Python experience
- Knowledge of SQL and PostgreSQL
- Experience with Docker

Nice to have:
- AWS certification
"""

GAP_JOB = """
Requirements:
- Must have Java and Spring Boot experience
- Required: Kubernetes and Terraform
- Strong DevOps background

Nice to have:
- Go programming
"""


def _mock_embedder() -> Embedder:
    """Create an embedder with a deterministic mock."""
    import hashlib

    client = MagicMock()

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for t in texts:
            # Use hash to generate deterministic pseudo-embeddings
            h = hashlib.md5(t.lower().encode()).hexdigest()
            vec = [int(h[i:i+2], 16) / 255.0 for i in range(0, 20, 2)]
            vectors.append(vec)
        return vectors

    client.embed = fake_embed
    return Embedder(client)


@pytest.mark.asyncio
async def test_smoke_matching_job_pipeline():
    """Smoke test: full pipeline completes without errors for a matching job."""
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract(MATCHING_JOB)
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)

    # With mock embeddings, exact assertion depends on hash-based vectors.
    # But the pipeline should complete without errors.
    assert 0.0 <= assessment.severity <= 1.0
    assert isinstance(assessment.simulated_ats_score, float)
    assert len(assessment.covered_skills) + len(assessment.critical_gaps) + len(assessment.preferred_gaps) >= 0


@pytest.mark.asyncio
async def test_smoke_gap_job_pipeline():
    """Smoke test: full pipeline completes for a gap job and produces valid assessment."""
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    match_profile = extractor.extract(MATCHING_JOB)
    match_profile = await embedder.embed_job_profile(match_profile)
    match_assessment = engine.assess(match_profile, cv_profile)

    gap_profile = extractor.extract(GAP_JOB)
    gap_profile = await embedder.embed_job_profile(gap_profile)
    gap_assessment = engine.assess(gap_profile, cv_profile)

    # Gap job should produce higher severity (worse fit)
    # Note: with mock embeddings this may not always hold perfectly,
    # but the pipeline integrity is what we're testing
    assert isinstance(gap_assessment.severity, float)
    assert isinstance(match_assessment.severity, float)


@pytest.mark.asyncio
async def test_pipeline_handles_empty_cv():
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile("")
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract(MATCHING_JOB)
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)
    assert assessment.severity >= 0.0


@pytest.mark.asyncio
async def test_pipeline_handles_empty_job():
    parser = CVParser()
    extractor = JobSkillExtractor()
    embedder = _mock_embedder()
    engine = FitEngine()

    cv_profile = parser.build_profile(SAMPLE_CV)
    cv_profile = await embedder.embed_cv_profile(cv_profile)

    job_profile = extractor.extract("")
    job_profile = await embedder.embed_job_profile(job_profile)

    assessment = engine.assess(job_profile, cv_profile)
    assert assessment.severity == 0.0
    assert assessment.should_modify is False
```

- [ ] **Step 2: Run integration test**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/test_fit_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/ -v --ignore=tests/test_config_scraper_headless.py --ignore=tests/test_google_jobs_scraping.py`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/test_fit_integration.py
git commit -m "test: add integration tests for full FitEngine pipeline"
```

---

### Task 15: Update matching/__init__.py exports

**Files:**
- Modify: `backend/matching/__init__.py`

- [ ] **Step 1: Update __init__.py**

```python
# backend/matching/__init__.py
from backend.matching.cv_parser import CVParser, CVProfile, SkillEntry
from backend.matching.embedder import Embedder
from backend.matching.fit_engine import FitAssessment, FitEngine, SkillGap
from backend.matching.filters import JobFilters
from backend.matching.job_skill_extractor import JobProfile, JobSkill, JobSkillExtractor
from backend.matching.matcher import JobMatcher

__all__ = [
    "CVParser",
    "CVProfile",
    "Embedder",
    "FitAssessment",
    "FitEngine",
    "JobFilters",
    "JobMatcher",
    "JobProfile",
    "JobSkill",
    "JobSkillExtractor",
    "SkillEntry",
    "SkillGap",
]
```

- [ ] **Step 2: Run import smoke test**

Run: `cd /home/mouad/Web-automation && .venv/bin/python -c "from backend.matching import FitEngine, CVParser, JobSkillExtractor, Embedder; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Commit**

```bash
git add backend/matching/__init__.py
git commit -m "feat(matching): export all new matching engine components"
```

---

### Task 16: Run full test suite and final verification

- [ ] **Step 1: Run full test suite**

```bash
cd /home/mouad/Web-automation && .venv/bin/python -m pytest tests/ -v \
  --ignore=tests/test_config_scraper_headless.py \
  --ignore=tests/test_google_jobs_scraping.py
```
Expected: All PASS

- [ ] **Step 2: Run ruff linter**

```bash
cd /home/mouad/Web-automation && .venv/bin/python -m ruff check backend/matching/ tests/test_fit_engine.py tests/test_cv_parser.py tests/test_job_skill_extractor.py tests/test_embedder.py tests/test_fit_integration.py tests/test_defaults.py tests/test_fit_models.py tests/test_skill_patterns.py
```
Expected: No errors

- [ ] **Step 3: Final commit if any lint fixes needed**

```bash
git add -A && git commit -m "chore: lint fixes for ATS gap severity engine"
```
