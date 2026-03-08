# CV Pipeline Redesign — Whole-CV + Context.md Architecture

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the brittle marker-based CV tailoring pipeline with a whole-CV LLM approach that reads any LaTeX CV structure, generates a structured job context document, and makes ≤3 surgical replacements per application.

**Architecture:** A `JobAnalyzer` LLM call converts a raw job description into a structured `JobContext` (cached per job_id). A `CVModifier` LLM call receives the full CV text + context markdown and returns a validated list of `CVReplacement` items (max 3). A `CVApplicator` validates and applies each replacement with per-item safety checks.

**Tech Stack:** Python 3.11+, Pydantic v2, `GeminiClient` (existing), `LaTeXCompiler` (existing, unchanged), pytest + AsyncMock.

---

## Current State (read before starting)

| File | Role | Action |
|---|---|---|
| `backend/llm/cv_editor.py` | Old marker-based editor | **Replace** |
| `backend/llm/prompts.py` | Old prompts | **Extend** (add 2 new prompts, keep `MOTIVATION_LETTER_PROMPT`) |
| `backend/llm/validators.py` | Pydantic output models | **Extend** (add new models, remove old ones) |
| `backend/latex/pipeline.py` | `CVPipeline` orchestration | **Update** wiring |
| `backend/latex/injector.py` | Marker-based injector | **Keep** (still used by `LetterPipeline`) |
| `backend/latex/parser.py` | Marker-based parser | **Keep** (still used by `LetterPipeline`) |
| `backend/latex/compiler.py` | Tectonic wrapper | **Untouched** |
| `backend/latex/validator.py` | LaTeX validation | **Untouched** |
| `backend/llm/gemini_client.py` | Gemini API client | **Untouched** |
| `tests/fixtures/sample_cv.tex` | Test fixture | **Update** (remove markers) |

## Key Invariants (never break these)

1. `LetterPipeline` continues to use `LaTeXParser` + `LaTeXInjector` + markers — do not touch it.
2. The base CV file is never mutated — always copy first.
3. Each `CVReplacement.original_text` must exist verbatim in the CV before applying.
4. No new LaTeX commands may be introduced by any replacement.
5. Only replacements with `confidence >= 0.7` are applied.

---

## Task 1: `JobContext` Pydantic model + `JOB_ANALYZER_PROMPT`

**Files:**
- Create: `backend/llm/job_context.py`
- Modify: `backend/llm/prompts.py`

This task defines the structured output of the job analysis step and the prompt that produces it. Nothing is wired up yet — just data models and the prompt string.

**Step 1: Write the failing test**

Create `tests/test_job_context.py`:

```python
"""Tests for JobContext model and its to_markdown() serialization."""
from backend.llm.job_context import JobContext


def test_job_context_to_markdown_contains_required_fields():
    ctx = JobContext(
        required_skills=["HACCP", "aseptic sampling"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety", "traceability"],
        candidate_matches=["HACCP ✓", "aseptic sampling ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["dates", "grades", "company names", "certifications"],
        top_changes_hint=[
            "Profile: add motivation to learn ISO 22000",
            "Skills: reorder to put HACCP first",
        ],
    )
    md = ctx.to_markdown(job_title="QC Technician", company="Nestlé")

    assert "QC Technician" in md
    assert "Nestlé" in md
    assert "HACCP" in md
    assert "ISO 22000" in md
    assert "DO NOT TOUCH" in md
    assert "Profile:" in md


def test_job_context_to_markdown_empty_gaps():
    ctx = JobContext(
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["data"],
        candidate_matches=["Python ✓"],
        candidate_gaps=[],
        do_not_touch=["dates"],
        top_changes_hint=["Profile: emphasise Python"],
    )
    md = ctx.to_markdown(job_title="Analyst", company="Acme")
    assert "no gaps" in md.lower() or "candidate_gaps" not in md or "[]" not in md
```

**Step 2: Run to verify it fails**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_context.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.llm.job_context'`

**Step 3: Create `backend/llm/job_context.py`**

```python
"""JobContext — structured output of the job analysis LLM call."""
from __future__ import annotations

from pydantic import BaseModel


class JobContext(BaseModel):
    required_skills: list[str]
    nice_to_have_skills: list[str]
    keywords: list[str]
    candidate_matches: list[str]   # skills already on CV that match job
    candidate_gaps: list[str]      # required skills NOT on CV
    do_not_touch: list[str]        # locked fields (always include dates, grades, etc.)
    top_changes_hint: list[str]    # LLM's suggested edit targets (max 3)

    def to_markdown(self, job_title: str, company: str) -> str:
        """Serialize to a structured context.md string for the CV modifier prompt."""
        gaps_text = (
            "\n".join(f"- {g}" for g in self.candidate_gaps)
            if self.candidate_gaps
            else "- (none — candidate matches all requirements)"
        )
        return f"""# Job Context: {job_title} at {company}

## Required Skills (candidate HAS these)
{chr(10).join(f"- {s}" for s in self.candidate_matches) or "- (none identified)"}

## Required Skills (candidate LACKS — motivation framing only)
{gaps_text}

## Nice-to-Have Skills
{chr(10).join(f"- {s}" for s in self.nice_to_have_skills) or "- (none listed)"}

## Keywords to Weave In
{chr(10).join(f"- {k}" for k in self.keywords) or "- (none identified)"}

## Top Suggested Changes (max 3)
{chr(10).join(f"{i+1}. {h}" for i, h in enumerate(self.top_changes_hint[:3]))}

## DO NOT TOUCH (these fields must remain identical)
{chr(10).join(f"- {d}" for d in self.do_not_touch)}
"""
```

**Step 4: Add `JOB_ANALYZER_PROMPT` to `backend/llm/prompts.py`**

Append at the end of the file (keep existing prompts intact):

```python
JOB_ANALYZER_PROMPT = """You are a recruitment analyst. Analyze the job posting below \
and extract structured information to help tailor a candidate's CV.

The candidate's CV is in Food Science / Laboratory domain. Their known skills include:
cell culture techniques, XTT assays, HACCP, GMP, aseptic sampling, Python (data analysis),
ERP/SPC systems, cytotoxicity testing, trypan blue exclusion.

RULES:
- required_skills: skills/certifications explicitly required by the posting
- nice_to_have_skills: skills mentioned as preferred/advantageous
- keywords: 3-6 domain keywords to weave into the profile (e.g. "food safety", "traceability")
- candidate_matches: subset of required_skills already on the candidate's CV
- candidate_gaps: required skills NOT on the candidate's CV (only facts, no guessing)
- do_not_touch: always include ["education dates", "grades", "company names", "certifications"]
- top_changes_hint: your top 1-3 most impactful edit suggestions, format: "Section: action"
  - For gaps: suggest "Profile: add motivation to learn X" (never fabricate skills)
  - For matches: suggest "Skills: reorder to put X first" or "Profile: emphasise X"
  - For bullets: suggest "Experience bullet N: rephrase to highlight X"

## Job Posting:
Title: {job_title}
Company: {company}
Description:
{job_description}

## Return JSON:
{{
    "required_skills": ["..."],
    "nice_to_have_skills": ["..."],
    "keywords": ["..."],
    "candidate_matches": ["..."],
    "candidate_gaps": ["..."],
    "do_not_touch": ["education dates", "grades", "company names", "certifications"],
    "top_changes_hint": ["..."]
}}"""
```

**Step 5: Run tests to verify they pass**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_context.py -v
```

Expected: 2 PASSED

**Step 6: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/llm/job_context.py backend/llm/prompts.py tests/test_job_context.py
git commit -m "feat(cv): add JobContext model and JOB_ANALYZER_PROMPT"
```

---

## Task 2: `CVReplacement` + `CVModifierOutput` models

**Files:**
- Modify: `backend/llm/validators.py`

Add new models. **Do not remove old models yet** — `LetterEdit` is still used by `LetterPipeline`. Remove `CVSummaryEdit`, `CVExperienceEdit`, `BulletEdit` at the end of Task 5 once pipeline is rewired.

**Step 1: Write the failing test**

Add to `tests/test_job_context.py` (append at end):

```python
def test_cv_modifier_output_caps_at_three_replacements():
    """CVModifierOutput must not apply more than 3 replacements."""
    from backend.llm.validators import CVModifierOutput, CVReplacement
    output = CVModifierOutput(replacements=[
        CVReplacement(section="Profile", original_text="a", replacement_text="b",
                      reason="r1", job_requirement_matched="x", confidence=0.9),
        CVReplacement(section="Profile", original_text="c", replacement_text="d",
                      reason="r2", job_requirement_matched="y", confidence=0.8),
        CVReplacement(section="Skills", original_text="e", replacement_text="f",
                      reason="r3", job_requirement_matched="z", confidence=0.75),
        CVReplacement(section="Experience", original_text="g", replacement_text="h",
                      reason="r4", job_requirement_matched="w", confidence=0.7),
    ])
    assert len(output.top_three()) == 3


def test_cv_replacement_confidence_threshold():
    from backend.llm.validators import CVReplacement
    r = CVReplacement(section="Profile", original_text="x", replacement_text="y",
                      reason="test", job_requirement_matched="req", confidence=0.65)
    assert not r.is_applicable()

    r2 = CVReplacement(section="Profile", original_text="x", replacement_text="y",
                       reason="test", job_requirement_matched="req", confidence=0.7)
    assert r2.is_applicable()
```

**Step 2: Run to verify it fails**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_context.py -v -k "caps_at_three or threshold"
```

Expected: `ImportError` or `AttributeError`

**Step 3: Add models to `backend/llm/validators.py`**

Append at the end of the file (keep existing models):

```python
class CVReplacement(BaseModel):
    section: str                    # "Profile" | "Experience" | "Skills" | "Additional Information"
    original_text: str              # verbatim substring that must exist in the CV
    replacement_text: str           # the new text to substitute in
    reason: str                     # human-readable explanation
    job_requirement_matched: str    # which job requirement this addresses
    confidence: float               # 0.0–1.0; only apply if >= 0.7

    def is_applicable(self) -> bool:
        return self.confidence >= 0.7


class CVModifierOutput(BaseModel):
    replacements: list[CVReplacement] = []

    def top_three(self) -> list[CVReplacement]:
        """Return at most 3 replacements, sorted by confidence descending."""
        return sorted(self.replacements, key=lambda r: r.confidence, reverse=True)[:3]
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_context.py -v
```

Expected: 4 PASSED

**Step 5: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/llm/validators.py tests/test_job_context.py
git commit -m "feat(cv): add CVReplacement and CVModifierOutput pydantic models"
```

---

## Task 3: `CV_MODIFIER_SKILL` prompt

**Files:**
- Modify: `backend/llm/prompts.py`

This is the most important prompt in the system — the system-level skill that tells the LLM exactly how to behave as a CV modifier.

**Step 1: Append to `backend/llm/prompts.py`**

```python
CV_MODIFIER_SKILL = """You are a surgical CV editor with one rule above all: LESS IS MORE.

You receive:
1. A LaTeX CV (full file as text)
2. A job context document (pre-analyzed, structured)

YOUR TASK: Produce at most 3 replacements that maximally increase job fit.

=== STRICT RULES ===

WHAT YOU MAY CHANGE:
- Profile/Summary paragraph: rephrase 1-2 phrases to highlight matching skills or add
  "motivated to develop [gap skill]" for missing requirements (Profile section ONLY)
- Experience bullets: rephrase to front-load job-relevant action verbs or techniques
- Skills row: REORDER items within an existing row to put job-relevant skills first
  (e.g. move HACCP to front if the job requires it) — you may NOT add new skills

WHAT YOU MUST NEVER DO:
- Invent skills, certifications, experiences, or metrics not in the CV
- Add new bullet points or new rows
- Change dates, company names, grades, institutions, or certifications
- Introduce new LaTeX commands (\\textbf, \\textit, etc.) not already in that text
- Change more than 3 things total

FABRICATION RULE: If a required skill is missing from the CV, the ONLY allowed action is
adding "motivated to develop [skill]" or "keen to learn [skill]" in the Profile paragraph.
This counts as 1 of your 3 changes.

CONFIDENCE SCORING:
- 0.9+: change directly addresses a required skill with exact CV evidence
- 0.7-0.9: change highlights a relevant existing strength
- <0.7: skip — not worth making

=== RETURN FORMAT ===

Return ONLY valid JSON, no markdown fences, no prose:
{{
  "replacements": [
    {{
      "section": "Profile",
      "original_text": "exact verbatim substring from the CV that will be replaced",
      "replacement_text": "the new text (same length, same LaTeX structure)",
      "reason": "one sentence explaining what this change achieves",
      "job_requirement_matched": "which requirement from the job context this addresses",
      "confidence": 0.85
    }}
  ]
}}

IMPORTANT: original_text must be an EXACT substring of the CV text provided. Copy-paste it.
If you cannot find an exact substring to replace, do not include that replacement.

=== JOB CONTEXT ===
{job_context_md}

=== FULL CV (LaTeX) ===
{cv_tex}
"""
```

**Step 2: Verify prompt string is importable**

```bash
cd /home/mouad/Web-automation && python -c "from backend.llm.prompts import CV_MODIFIER_SKILL; print('OK', len(CV_MODIFIER_SKILL))"
```

Expected: `OK` followed by a number > 1000

**Step 3: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/llm/prompts.py
git commit -m "feat(cv): add CV_MODIFIER_SKILL prompt"
```

---

## Task 4: `JobAnalyzer` class

**Files:**
- Create: `backend/llm/job_analyzer.py`
- Create: `tests/test_job_analyzer.py`

**Step 1: Write the failing test**

```python
"""Tests for JobAnalyzer — all using mocked GeminiClient."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.llm.job_analyzer import JobAnalyzer
from backend.llm.job_context import JobContext
from backend.models.schemas import JobDetails


def _make_job() -> JobDetails:
    return JobDetails(
        id=42,
        title="Quality Control Technician",
        company="Nestlé",
        description="Requires HACCP, GMP, aseptic sampling. ISO 22000 preferred.",
    )


def _mock_client(return_value) -> MagicMock:
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=return_value)
    return client


@pytest.mark.asyncio
async def test_job_analyzer_returns_job_context():
    expected = JobContext(
        required_skills=["HACCP", "GMP", "aseptic sampling"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety", "quality control"],
        candidate_matches=["HACCP ✓", "GMP ✓", "aseptic sampling ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["education dates", "grades", "company names", "certifications"],
        top_changes_hint=["Profile: add motivation to learn ISO 22000"],
    )
    analyzer = JobAnalyzer(client=_mock_client(expected))
    result = await analyzer.analyze(_make_job())
    assert isinstance(result, JobContext)
    assert "HACCP" in result.required_skills


@pytest.mark.asyncio
async def test_job_analyzer_context_markdown_is_valid():
    """to_markdown() produces non-empty string with job title."""
    expected = JobContext(
        required_skills=["HACCP"],
        nice_to_have_skills=[],
        keywords=["food safety"],
        candidate_matches=["HACCP ✓"],
        candidate_gaps=[],
        do_not_touch=["dates"],
        top_changes_hint=["Skills: reorder to put HACCP first"],
    )
    analyzer = JobAnalyzer(client=_mock_client(expected))
    ctx = await analyzer.analyze(_make_job())
    md = ctx.to_markdown("Quality Control Technician", "Nestlé")
    assert len(md) > 100
    assert "Nestlé" in md
    assert "HACCP" in md


@pytest.mark.asyncio
async def test_job_analyzer_propagates_gemini_error():
    from backend.llm.gemini_client import GeminiJSONError
    client = MagicMock()
    client.generate_json = AsyncMock(side_effect=GeminiJSONError("bad json"))
    analyzer = JobAnalyzer(client=client)
    with pytest.raises(GeminiJSONError):
        await analyzer.analyze(_make_job())
```

**Step 2: Run to verify it fails**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_analyzer.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.llm.job_analyzer'`

**Step 3: Create `backend/llm/job_analyzer.py`**

```python
"""JobAnalyzer — converts a raw JobDetails into a structured JobContext."""
from __future__ import annotations

import logging

from backend.llm.gemini_client import GeminiClient
from backend.llm.job_context import JobContext
from backend.llm.prompts import JOB_ANALYZER_PROMPT
from backend.models.schemas import JobDetails

logger = logging.getLogger(__name__)


class JobAnalyzer:
    """Single LLM call: job description → structured JobContext."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    async def analyze(self, job: JobDetails) -> JobContext:
        prompt = JOB_ANALYZER_PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description=job.description[:2000],  # truncate to control cost
        )
        return await self._client.generate_json(prompt, JobContext)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_job_analyzer.py -v
```

Expected: 3 PASSED

**Step 5: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/llm/job_analyzer.py tests/test_job_analyzer.py
git commit -m "feat(cv): add JobAnalyzer LLM call"
```

---

## Task 5: `CVModifier` class

**Files:**
- Create: `backend/llm/cv_modifier.py`
- Create: `tests/test_cv_modifier.py`

**Step 1: Write the failing test**

```python
"""Tests for CVModifier — all using mocked GeminiClient."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.llm.cv_modifier import CVModifier
from backend.llm.validators import CVModifierOutput, CVReplacement
from backend.llm.job_context import JobContext
from backend.models.schemas import JobDetails

SAMPLE_CV = r"""
\begin{rSection}{Profile}
Junior Food Scientist with laboratory experience in fish cell lines.
\end{rSection}
\begin{rSection}{Experience}
\begin{itemize}
  \item Conducted quality control tests on raw materials.
  \item Performed aseptic sampling.
\end{itemize}
\end{rSection}
"""


def _make_job() -> JobDetails:
    return JobDetails(id=1, title="QC Technician", company="Nestlé",
                      description="Requires HACCP. ISO 22000 preferred.")


def _make_context() -> JobContext:
    return JobContext(
        required_skills=["HACCP"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety"],
        candidate_matches=["HACCP ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["dates", "grades"],
        top_changes_hint=["Profile: add motivation to learn ISO 22000"],
    )


def _mock_client(return_value) -> MagicMock:
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=return_value)
    return client


@pytest.mark.asyncio
async def test_cv_modifier_returns_output():
    expected = CVModifierOutput(replacements=[
        CVReplacement(
            section="Profile",
            original_text="Junior Food Scientist with laboratory experience in fish cell lines.",
            replacement_text="Junior Food Scientist with laboratory experience in fish cell lines, motivated to develop ISO 22000 expertise.",
            reason="Addresses gap in ISO 22000",
            job_requirement_matched="ISO 22000",
            confidence=0.8,
        )
    ])
    modifier = CVModifier(client=_mock_client(expected))
    result = await modifier.modify(_make_job(), SAMPLE_CV, _make_context())
    assert isinstance(result, CVModifierOutput)
    assert len(result.replacements) == 1


@pytest.mark.asyncio
async def test_cv_modifier_caps_at_three():
    """Even if LLM returns 4 replacements, top_three() caps at 3."""
    four_replacements = CVModifierOutput(replacements=[
        CVReplacement(section="Profile", original_text=f"text{i}",
                      replacement_text=f"new{i}", reason="r", job_requirement_matched="x",
                      confidence=0.9 - i * 0.05)
        for i in range(4)
    ])
    modifier = CVModifier(client=_mock_client(four_replacements))
    result = await modifier.modify(_make_job(), SAMPLE_CV, _make_context())
    assert len(result.top_three()) == 3


@pytest.mark.asyncio
async def test_cv_modifier_propagates_error():
    from backend.llm.gemini_client import GeminiJSONError
    client = MagicMock()
    client.generate_json = AsyncMock(side_effect=GeminiJSONError("bad"))
    modifier = CVModifier(client=client)
    with pytest.raises(GeminiJSONError):
        await modifier.modify(_make_job(), SAMPLE_CV, _make_context())
```

**Step 2: Run to verify it fails**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_cv_modifier.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.llm.cv_modifier'`

**Step 3: Create `backend/llm/cv_modifier.py`**

```python
"""CVModifier — whole-CV LLM call that returns surgical replacements."""
from __future__ import annotations

import logging

from backend.llm.gemini_client import GeminiClient
from backend.llm.job_context import JobContext
from backend.llm.prompts import CV_MODIFIER_SKILL
from backend.llm.validators import CVModifierOutput
from backend.models.schemas import JobDetails

logger = logging.getLogger(__name__)


class CVModifier:
    """Single LLM call: full CV text + JobContext → CVModifierOutput (≤3 replacements)."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    async def modify(
        self,
        job: JobDetails,
        cv_tex: str,
        context: JobContext,
    ) -> CVModifierOutput:
        context_md = context.to_markdown(job.title, job.company)
        prompt = CV_MODIFIER_SKILL.format(
            job_context_md=context_md,
            cv_tex=cv_tex,
        )
        return await self._client.generate_json(prompt, CVModifierOutput)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_cv_modifier.py -v
```

Expected: 3 PASSED

**Step 5: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/llm/cv_modifier.py tests/test_cv_modifier.py
git commit -m "feat(cv): add CVModifier whole-CV LLM editor"
```

---

## Task 6: `CVApplicator` — validated replacement engine

**Files:**
- Create: `backend/latex/applicator.py`
- Create: `tests/test_cv_applicator.py`

This replaces `LaTeXInjector` for CV editing. `LaTeXInjector` is kept for `LetterPipeline`.

**Step 1: Write the failing test**

```python
"""Tests for CVApplicator — applies CVReplacement items with safety checks."""
from __future__ import annotations
import pytest
from backend.latex.applicator import CVApplicator
from backend.llm.validators import CVReplacement

SAMPLE_CV = """\
\\begin{rSection}{Profile}
Junior Food Scientist with laboratory experience in fish cell lines.
\\end{rSection}
\\begin{rSection}{Additional Information}
Skills & Cell culture techniques, XTT assays, HACCP, GMP
\\end{rSection}
"""


def _make_replacement(**kwargs) -> CVReplacement:
    defaults = dict(
        section="Profile",
        original_text="Junior Food Scientist with laboratory experience in fish cell lines.",
        replacement_text="Junior Food Scientist with laboratory experience in fish cell lines, motivated to develop ISO 22000 expertise.",
        reason="Addresses ISO 22000 gap",
        job_requirement_matched="ISO 22000",
        confidence=0.85,
    )
    defaults.update(kwargs)
    return CVReplacement(**defaults)


def test_applicator_applies_valid_replacement():
    applicator = CVApplicator()
    replacement = _make_replacement()
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert replacement.replacement_text in result_tex
    assert len(applied) == 1


def test_applicator_rejects_low_confidence():
    applicator = CVApplicator()
    replacement = _make_replacement(confidence=0.5)
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV  # unchanged
    assert len(applied) == 0


def test_applicator_rejects_missing_original():
    """Replacement whose original_text is not in the CV is skipped."""
    applicator = CVApplicator()
    replacement = _make_replacement(original_text="This text does not exist in the CV at all.")
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV
    assert len(applied) == 0


def test_applicator_rejects_new_latex_commands():
    """Replacement that introduces new LaTeX commands is rejected."""
    applicator = CVApplicator()
    replacement = _make_replacement(
        replacement_text="\\textbf{Junior} Food Scientist with laboratory experience in fish cell lines.",
    )
    result_tex, applied = applicator.apply(SAMPLE_CV, [replacement])
    assert result_tex == SAMPLE_CV
    assert len(applied) == 0


def test_applicator_caps_at_three():
    """Only first 3 (by confidence) are ever applied."""
    applicator = CVApplicator()
    # 4 replacements all targeting different substrings of the CV
    cv = "aaa bbb ccc ddd eee"
    replacements = [
        CVReplacement(section="Profile", original_text=text, replacement_text=text + "X",
                      reason="r", job_requirement_matched="x", confidence=conf)
        for text, conf in [("aaa", 0.95), ("bbb", 0.90), ("ccc", 0.85), ("ddd", 0.80)]
    ]
    result_tex, applied = applicator.apply(cv, replacements)
    assert len(applied) == 3
    assert "dddX" not in result_tex  # 4th was dropped


def test_applicator_never_mutates_original():
    applicator = CVApplicator()
    replacement = _make_replacement()
    original_copy = SAMPLE_CV
    applicator.apply(SAMPLE_CV, [replacement])
    assert SAMPLE_CV == original_copy  # Python str is immutable, but let's be explicit
```

**Step 2: Run to verify it fails**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_cv_applicator.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.latex.applicator'`

**Step 3: Create `backend/latex/applicator.py`**

```python
"""CVApplicator — validates and applies CVReplacement items to a LaTeX string."""
from __future__ import annotations

import logging
import re

from backend.llm.validators import CVReplacement

logger = logging.getLogger(__name__)

_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _has_new_latex_commands(original: str, edited: str) -> bool:
    orig_cmds = set(_LATEX_CMD_RE.findall(original))
    edit_cmds = set(_LATEX_CMD_RE.findall(edited))
    return bool(edit_cmds - orig_cmds)


class CVApplicator:
    """Applies a list of CVReplacement items to a LaTeX string with per-item validation."""

    CONFIDENCE_THRESHOLD = 0.7
    MAX_REPLACEMENTS = 3

    def apply(
        self,
        cv_tex: str,
        replacements: list[CVReplacement],
    ) -> tuple[str, list[CVReplacement]]:
        """Apply validated replacements. Returns (modified_tex, applied_list).

        Validation order per replacement:
        1. confidence >= CONFIDENCE_THRESHOLD
        2. original_text exists verbatim in current tex
        3. replacement_text introduces no new LaTeX commands
        """
        # Sort by confidence descending, cap at MAX_REPLACEMENTS
        candidates = sorted(replacements, key=lambda r: r.confidence, reverse=True)
        candidates = candidates[: self.MAX_REPLACEMENTS]

        result = cv_tex
        applied: list[CVReplacement] = []

        for r in candidates:
            if not r.is_applicable():
                logger.debug("Skipping replacement (confidence=%.2f < %.2f): %r",
                             r.confidence, self.CONFIDENCE_THRESHOLD, r.original_text[:50])
                continue

            if r.original_text not in result:
                logger.warning("original_text not found in CV — skipping: %r",
                               r.original_text[:80])
                continue

            if _has_new_latex_commands(r.original_text, r.replacement_text):
                logger.warning("Replacement introduces new LaTeX commands — skipping: %r",
                               r.replacement_text[:80])
                continue

            result = result.replace(r.original_text, r.replacement_text, 1)
            applied.append(r)
            logger.info("Applied replacement in section=%s (confidence=%.2f): %s",
                        r.section, r.confidence, r.reason)

        return result, applied
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_cv_applicator.py -v
```

Expected: 6 PASSED

**Step 5: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/latex/applicator.py tests/test_cv_applicator.py
git commit -m "feat(cv): add CVApplicator with per-replacement validation"
```

---

## Task 7: Update `CVPipeline` to use new components

**Files:**
- Modify: `backend/latex/pipeline.py`
- Modify: `tests/test_latex_pipeline.py`
- Modify: `tests/fixtures/sample_cv.tex`

This is the wiring task. The `LetterPipeline` class in the same file is **untouched**.

**Step 1: Update `tests/fixtures/sample_cv.tex`**

Replace the file content entirely (remove markers — new pipeline doesn't need them):

```latex
\documentclass{article}
\begin{document}

\section*{Profile}
Experienced software engineer with 5 years in distributed systems and ML pipelines.

\section*{Experience}
\textbf{Software Engineer} \hfill 2022--Present \\
\textit{TechCorp}
\begin{itemize}
    \item Designed distributed data pipeline processing 10TB/day
    \item Led migration to microservices, reducing deploy time by 80\%
    \item Mentored 3 junior engineers on system design
\end{itemize}

\section*{Additional Information}
\begin{tabular}{ l l }
Skills & Python, distributed systems, ML pipelines \\
\end{tabular}

\end{document}
```

**Step 2: Rewrite `CVPipeline` in `backend/latex/pipeline.py`**

Replace **only** the `CVPipeline` class (lines 36–129). Leave `LetterPipeline`, `TailoredCV`, `TailoredLetter`, `DiffEntry`, and `generate_diff` intact.

New `CVPipeline`:

```python
class CVPipeline:
    """Generates a tailored CV PDF for a job from a base LaTeX template.

    New architecture (marker-free):
    1. Copy base CV .tex to output_dir/cv.tex
    2. Run JobAnalyzer → JobContext (cached per job_id)
    3. Run CVModifier (whole CV text + context) → CVModifierOutput
    4. Run CVApplicator → apply validated replacements
    5. Compile with Tectonic
    6. Return TailoredCV with paths and diff
    """

    def __init__(
        self,
        compiler: LaTeXCompiler | None = None,
        job_analyzer=None,
        cv_modifier=None,
        cv_applicator=None,
    ) -> None:
        self._compiler = compiler or LaTeXCompiler()
        self._job_analyzer = job_analyzer   # backend.llm.job_analyzer.JobAnalyzer
        self._cv_modifier = cv_modifier     # backend.llm.cv_modifier.CVModifier
        self._cv_applicator = cv_applicator # backend.latex.applicator.CVApplicator
        self._context_cache: dict[int, object] = {}  # job_id → JobContext

    async def generate_tailored_cv(
        self,
        base_cv_path: Path,
        job: JobDetails,
        output_dir: Path,
    ) -> TailoredCV:
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_tex = output_dir / "cv.tex"

        # 1. Copy — never mutate the base file
        shutil.copy2(base_cv_path, dest_tex)
        cv_tex = dest_tex.read_text(encoding="utf-8")

        diff: list[DiffEntry] = []
        cv_tailored = False

        # 2–4. Analyze + modify (only when all three components are wired up)
        if self._job_analyzer is not None and self._cv_modifier is not None and self._cv_applicator is not None:
            try:
                # 2. JobAnalyzer (cached per job_id)
                job_id = job.id
                if job_id is not None and job_id in self._context_cache:
                    context = self._context_cache[job_id]
                    logger.debug("Using cached JobContext for job_id=%s", job_id)
                else:
                    context = await self._job_analyzer.analyze(job)
                    if job_id is not None:
                        self._context_cache[job_id] = context
                        logger.debug("Cached JobContext for job_id=%s", job_id)

                # 3. CVModifier
                modifier_output = await self._cv_modifier.modify(job, cv_tex, context)

                # 4. CVApplicator
                cv_tex, applied = self._cv_applicator.apply(cv_tex, modifier_output.replacements)

                # Build diff from applied replacements
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

            except Exception as exc:
                logger.warning("CV modifier failed (%s); using base CV unchanged.", exc)
                cv_tex = dest_tex.read_text(encoding="utf-8")  # reset to unmodified copy
                diff = []

        # Write (possibly edited) tex back
        dest_tex.write_text(cv_tex, encoding="utf-8")

        # 5. Compile
        pdf_path = await self._compiler.compile(dest_tex, output_dir)

        return TailoredCV(
            job_id=job.id,
            tex_path=dest_tex,
            pdf_path=pdf_path,
            diff=diff,
            cv_tailored=cv_tailored,
        )
```

Also add the missing import at the top of `pipeline.py`:

```python
from backend.latex.applicator import CVApplicator
```

**Step 3: Update `tests/test_latex_pipeline.py`**

Replace the file content with updated tests that match the new pipeline (no markers, new mocking pattern):

```python
"""Tests for the LaTeX pipeline — new whole-CV architecture."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.latex.compiler import LaTeXCompiler, LaTeXCompilationError
from backend.latex.pipeline import CVPipeline, TailoredCV, generate_diff, DiffEntry
from backend.models.schemas import JobDetails
from backend.llm.validators import CVReplacement, CVModifierOutput
from backend.llm.job_context import JobContext

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CV = FIXTURE_DIR / "sample_cv.tex"


def _make_job(job_id: int = 1) -> JobDetails:
    return JobDetails(
        id=job_id,
        title="Senior Python Engineer",
        company="Acme Corp",
        description="We need a Python engineer with experience in distributed systems.",
    )


def _make_context() -> JobContext:
    return JobContext(
        required_skills=["Python", "distributed systems"],
        nice_to_have_skills=[],
        keywords=["scalability"],
        candidate_matches=["Python ✓", "distributed systems ✓"],
        candidate_gaps=[],
        do_not_touch=["dates", "grades"],
        top_changes_hint=["Profile: emphasise distributed systems"],
    )


def _make_replacement(original_text: str, replacement_text: str) -> CVReplacement:
    return CVReplacement(
        section="Profile",
        original_text=original_text,
        replacement_text=replacement_text,
        reason="Emphasise distributed systems",
        job_requirement_matched="distributed systems",
        confidence=0.85,
    )


# ─── Compiler tests ───────────────────────────────────────────────────────────

def test_missing_tectonic_raises_clear_error():
    compiler = LaTeXCompiler()
    with pytest.raises(LaTeXCompilationError):
        asyncio.get_event_loop().run_until_complete(
            (lambda: (_ for _ in ()).throw(LaTeXCompilationError("Tectonic not found")))()
        )


# ─── Pipeline: no modifiers wired (base CV passthrough) ───────────────────────

def test_cv_pipeline_no_modifiers_compiles(tmp_path: Path):
    """Without modifiers, pipeline copies + compiles the base CV unchanged."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed")

    pipeline = CVPipeline()
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "out")
    )
    assert result.pdf_path.exists()
    assert result.cv_tailored is False
    assert result.diff == []


def test_cv_pipeline_does_not_modify_original(tmp_path: Path):
    """Original base CV file is never mutated."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed")

    original_content = SAMPLE_CV.read_text()
    pipeline = CVPipeline()
    asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(2), tmp_path / "out2")
    )
    assert SAMPLE_CV.read_text() == original_content


# ─── Pipeline: with mocked modifiers ──────────────────────────────────────────

def test_cv_pipeline_with_modifiers_applies_replacement(tmp_path: Path):
    """When modifiers are wired, replacements are applied and diff is populated."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed")

    # Read real CV text to build a valid original_text substring
    cv_text = SAMPLE_CV.read_text()
    original_phrase = "Experienced software engineer with 5 years"
    assert original_phrase in cv_text, "Fixture must contain this phrase"

    replacement = _make_replacement(
        original_text=original_phrase,
        replacement_text="Expert software engineer with 5 years",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())

    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(return_value=CVModifierOutput(replacements=[replacement]))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "out3")
    )

    assert result.cv_tailored is True
    assert len(result.diff) == 1
    assert result.diff[0].section == "Profile"
    assert result.pdf_path.exists()


def test_cv_pipeline_caches_job_context(tmp_path: Path):
    """JobAnalyzer.analyze is called only once for the same job_id."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed")

    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())
    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(return_value=CVModifierOutput(replacements=[]))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    job = _make_job(job_id=99)
    for i in range(3):
        asyncio.get_event_loop().run_until_complete(
            pipeline.generate_tailored_cv(SAMPLE_CV, job, tmp_path / f"out{i}")
        )

    # analyze() called exactly once despite 3 pipeline runs
    assert mock_analyzer.analyze.call_count == 1


def test_cv_pipeline_modifier_failure_falls_back(tmp_path: Path):
    """If CVModifier raises, pipeline falls back to unmodified base CV."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed")

    from backend.llm.gemini_client import GeminiJSONError
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())
    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(side_effect=GeminiJSONError("fail"))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "fallback")
    )

    assert result.cv_tailored is False
    assert result.diff == []
    assert result.pdf_path.exists()


# ─── generate_diff helper ─────────────────────────────────────────────────────

def test_generate_diff_returns_entries():
    """generate_diff still works for LetterPipeline compatibility."""
    from backend.llm.validators import CVSummaryEdit, CVExperienceEdit, BulletEdit, LetterEdit
    from backend.latex.parser import LaTeXSections

    original = LaTeXSections(
        summary="Original summary text.",
        experience_bullets=["Built something cool"],
        has_markers=True,
    )
    summary_edit = CVSummaryEdit(
        edited_summary="Updated summary for Python roles.",
        changes_made=["Emphasised Python skills"],
    )
    exp_edit = CVExperienceEdit(
        edits=[BulletEdit(index=0, original="Built something cool",
                          edited="Built a distributed data pipeline in Python",
                          reason="More relevant to job")]
    )
    diff = generate_diff(original, (summary_edit, exp_edit, None))
    assert len(diff) == 2
    assert diff[0].section == "summary"
```

**Step 4: Run the full test suite**

```bash
cd /home/mouad/Web-automation && python -m pytest tests/test_latex_pipeline.py tests/test_cv_applicator.py tests/test_cv_modifier.py tests/test_job_analyzer.py tests/test_job_context.py -v
```

Expected: All tests pass (Tectonic-dependent ones may be skipped if not installed)

**Step 5: Run the full project test suite to check for regressions**

```bash
cd /home/mouad/Web-automation && python -m pytest --ignore=tests/integration -v 2>&1 | tail -30
```

Expected: No new failures. Existing `test_gemini_editors.py` tests for `CVEditor.edit_summary` / `edit_experience` will still pass since we haven't removed `CVEditor` yet — that's fine.

**Step 6: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/latex/pipeline.py backend/latex/applicator.py tests/test_latex_pipeline.py tests/fixtures/sample_cv.tex
git commit -m "feat(cv): rewire CVPipeline to use JobAnalyzer + CVModifier + CVApplicator"
```

---

## Task 8: Clean up old dead code

**Files:**
- Modify: `backend/llm/validators.py` — remove `CVSummaryEdit`, `CVExperienceEdit`, `BulletEdit`
- Modify: `backend/llm/prompts.py` — remove `CV_SUMMARY_PROMPT`, `CV_EXPERIENCE_PROMPT`
- Modify: `backend/llm/cv_editor.py` — remove `edit_summary`, `edit_experience` (keep `edit_letter`)
- Delete: `tests/test_gemini_editors.py` (tests for removed methods)

**Step 1: Verify nothing outside tests imports old code**

```bash
cd /home/mouad/Web-automation && grep -r "CVSummaryEdit\|CVExperienceEdit\|BulletEdit\|CV_SUMMARY_PROMPT\|CV_EXPERIENCE_PROMPT\|edit_summary\|edit_experience" backend/ --include="*.py" -l
```

Expected: Only `backend/llm/cv_editor.py` and `backend/llm/prompts.py` themselves. If any other file appears, fix that import first.

**Step 2: Remove from `backend/llm/validators.py`**

Delete the three classes: `BulletEdit`, `CVSummaryEdit`, `CVExperienceEdit`.

Keep: `LetterEdit`, `CVReplacement`, `CVModifierOutput`.

**Step 3: Remove from `backend/llm/prompts.py`**

Delete `CV_SUMMARY_PROMPT` and `CV_EXPERIENCE_PROMPT` strings.

Keep: `MOTIVATION_LETTER_PROMPT`, `JOB_ANALYZER_PROMPT`, `CV_MODIFIER_SKILL`.

**Step 4: Trim `backend/llm/cv_editor.py`**

Remove `edit_summary()` and `edit_experience()` methods. Remove their imports (`CV_EXPERIENCE_PROMPT`, `CV_SUMMARY_PROMPT`, `CVSummaryEdit`, `CVExperienceEdit`, `BulletEdit`). Keep `edit_letter()` and all letter-related imports.

**Step 5: Delete `tests/test_gemini_editors.py`**

```bash
cd /home/mouad/Web-automation && rm tests/test_gemini_editors.py
```

**Step 6: Run full test suite to verify no breakage**

```bash
cd /home/mouad/Web-automation && python -m pytest --ignore=tests/integration -v 2>&1 | tail -30
```

Expected: All remaining tests pass. No `ImportError`.

**Step 7: Commit**

```bash
cd /home/mouad/Web-automation && git add -u
git commit -m "refactor(cv): remove old marker-based CVEditor methods and dead prompts"
```

---

## Task 9: Wire `JobAnalyzer` + `CVModifier` into the running app

**Files:**
- Read first: `backend/main.py` (find where `CVPipeline` is instantiated)
- Modify: wherever `CVPipeline()` is constructed (likely `backend/main.py` or `backend/applier/`)

**Step 1: Find where CVPipeline is instantiated**

```bash
cd /home/mouad/Web-automation && grep -rn "CVPipeline" backend/ --include="*.py"
```

Note the file(s) and line numbers.

**Step 2: Update the instantiation**

In the file found above, replace bare `CVPipeline()` with the fully wired version:

```python
from backend.llm.job_analyzer import JobAnalyzer
from backend.llm.cv_modifier import CVModifier
from backend.latex.applicator import CVApplicator

cv_pipeline = CVPipeline(
    job_analyzer=JobAnalyzer(),
    cv_modifier=CVModifier(),
    cv_applicator=CVApplicator(),
)
```

**Step 3: Verify app starts without error**

```bash
cd /home/mouad/Web-automation && python -c "from backend.main import app; print('OK')"
```

Expected: `OK` (no import errors)

**Step 4: Commit**

```bash
cd /home/mouad/Web-automation && git add backend/main.py  # or whichever file was changed
git commit -m "feat(cv): wire JobAnalyzer + CVModifier + CVApplicator into running app"
```

---

## Task 10: Final integration check + run all tests

**Step 1: Run full test suite**

```bash
cd /home/mouad/Web-automation && python -m pytest --ignore=tests/integration -v
```

Expected: All tests pass.

**Step 2: Check for remaining marker references that might be stale**

```bash
cd /home/mouad/Web-automation && grep -rn "JOBPILOT:" backend/ --include="*.py" | grep -v "LETTER:PARA"
```

Expected: No results (only letter markers remain in `LaTeXInjector` and `LaTeXParser`).

**Step 3: Commit**

```bash
cd /home/mouad/Web-automation && git add -u
git commit -m "chore(cv): final cleanup and verified all tests pass"
```

---

## Architecture Summary (after plan complete)

```
Job JSON
  └─→ [JobAnalyzer] ──────────────────────→ JobContext (cached per job_id)
                                                  │
CV .tex (full file)                               │
  └──────────────────────────────────────────────┤
                                                  ▼
                                          [CVModifier LLM]
                                     (CV_MODIFIER_SKILL prompt)
                                                  │
                                                  ▼
                                     CVModifierOutput (≤3 CVReplacement)
                                                  │
                                          [CVApplicator]
                                    per-item: confidence ≥ 0.7
                                             + substring exists
                                             + no new LaTeX cmds
                                                  │
                                                  ▼
                                         modified .tex → Tectonic → PDF
                                         + DiffEntry list for UI

LetterPipeline (UNCHANGED):
  base_letter.tex + JOBPILOT:LETTER:PARA markers → CVEditor.edit_letter → PDF
```

## Files Created

| File | Purpose |
|---|---|
| `backend/llm/job_context.py` | `JobContext` pydantic model + `to_markdown()` |
| `backend/llm/job_analyzer.py` | `JobAnalyzer` LLM call |
| `backend/llm/cv_modifier.py` | `CVModifier` whole-CV LLM call |
| `backend/latex/applicator.py` | `CVApplicator` validated applicator |
| `tests/test_job_context.py` | Tests for `JobContext` + new validators |
| `tests/test_job_analyzer.py` | Tests for `JobAnalyzer` |
| `tests/test_cv_modifier.py` | Tests for `CVModifier` |
| `tests/test_cv_applicator.py` | Tests for `CVApplicator` |

## Files Modified

| File | Change |
|---|---|
| `backend/llm/prompts.py` | Add `JOB_ANALYZER_PROMPT`, `CV_MODIFIER_SKILL`; remove old prompts |
| `backend/llm/validators.py` | Add `CVReplacement`, `CVModifierOutput`; remove old models |
| `backend/llm/cv_editor.py` | Remove `edit_summary`, `edit_experience`; keep `edit_letter` |
| `backend/latex/pipeline.py` | Rewrite `CVPipeline`; `LetterPipeline` untouched |
| `tests/test_latex_pipeline.py` | Update for new architecture |
| `tests/fixtures/sample_cv.tex` | Remove markers |
| `backend/main.py` (or similar) | Wire up new components |

## Files Deleted

| File | Reason |
|---|---|
| `tests/test_gemini_editors.py` | Tests for removed `CVEditor` methods |
