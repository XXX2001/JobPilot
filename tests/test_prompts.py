"""Prefix-caching tests for LLM prompt templates (LLM-01).

Gemini's implicit prompt caching only triggers when consecutive requests
share a byte-identical prefix (~1024+ tokens). Every template in
backend.llm.prompts must therefore put invariant content (system rules,
output schema, the user's own CV/letter body) BEFORE any per-job variable
(job title, company, job description, gap analysis).

These tests build two prompts that differ only in the per-job variables
and assert that their shared prefix is "long enough" to be cache-eligible.
"""
from __future__ import annotations

from os.path import commonprefix

import pytest

from backend.llm.prompts import (
    CV_MODIFIER_FROM_ASSESSMENT,
    CV_MODIFIER_SKILL,
    JOB_ANALYZER_PROMPT,
    MOTIVATION_LETTER_PROMPT,
)

# Realistic-length CV body — Gemini's implicit cache kicks in around 1024 tokens
# (~4000 chars). The CV body is what makes the cache hit on real workloads, so
# the test uses a CV of that order of magnitude.
SAMPLE_CV = (
    r"""\begin{document}
\begin{rSection}{Profile}
Junior Food Scientist with three years of laboratory experience in fish cell
lines, aseptic technique, and quality control of dairy raw materials.
""" + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 80) + r"""
\end{rSection}
\begin{rSection}{Skills}
Python, R, HACCP, GMP, aseptic sampling, qPCR, cell culture, ISO 17025
\end{rSection}
\begin{rSection}{Experience}
\begin{itemize}
""" + "  \\item " + ("Performed routine QC checks on incoming raw materials. " * 40) + r"""
\end{itemize}
\end{rSection}
\end{document}
"""
)

SAMPLE_LETTER_CONTENT = (
    "...\n"
    "% --- JOBPILOT:LETTER:PARA:START ---\n"
    + ("Je suis ravi de postuler à ce poste. " * 60) + "\n"
    "% --- JOBPILOT:LETTER:PARA:END ---\n"
    "..."
)

# Two distinct jobs — used to prove the per-call variables don't leak into the
# shared prefix.
JOB_A = {
    "job_title": "Quality Control Technician",
    "company": "Nestlé",
    "job_description": "Requires HACCP, GMP, aseptic sampling. ISO 22000 preferred.",
    "job_description_excerpt": "Requires HACCP, GMP, aseptic sampling.",
}
JOB_B = {
    "job_title": "Senior Data Scientist",
    "company": "Acme Corp",
    "job_description": "Looking for ML engineer with PyTorch, MLOps, distributed training.",
    "job_description_excerpt": "Looking for ML engineer with PyTorch, MLOps.",
}

# Cache-eligibility threshold. Gemini's implicit cache requires ~1024 tokens of
# common prefix; at ~4 chars/token that's ~4000 chars. We assert a slightly
# more conservative 2000 chars to leave headroom — the real CV is the dominant
# contributor and it sits comfortably above 4000 chars in production.
MIN_SHARED_PREFIX_CHARS = 2000


def _shared_prefix_len(a: str, b: str) -> int:
    return len(commonprefix([a, b]))


def test_motivation_letter_prompt_has_invariant_prefix() -> None:
    p_a = MOTIVATION_LETTER_PROMPT.format(
        letter_content=SAMPLE_LETTER_CONTENT, **JOB_A
    )
    p_b = MOTIVATION_LETTER_PROMPT.format(
        letter_content=SAMPLE_LETTER_CONTENT, **JOB_B
    )
    shared = _shared_prefix_len(p_a, p_b)
    assert shared >= MIN_SHARED_PREFIX_CHARS, (
        f"MOTIVATION_LETTER_PROMPT shared prefix is only {shared} chars; "
        f"variable placeholders likely leaked above the invariant block."
    )
    # And the variable portion must appear AFTER the shared prefix.
    assert "Nestlé" not in p_a[:shared]
    assert "Acme Corp" not in p_b[:shared]


def test_job_analyzer_prompt_has_invariant_prefix() -> None:
    p_a = JOB_ANALYZER_PROMPT.format(cv_content=SAMPLE_CV, **JOB_A)
    p_b = JOB_ANALYZER_PROMPT.format(cv_content=SAMPLE_CV, **JOB_B)
    shared = _shared_prefix_len(p_a, p_b)
    assert shared >= MIN_SHARED_PREFIX_CHARS, (
        f"JOB_ANALYZER_PROMPT shared prefix is only {shared} chars."
    )
    assert "Quality Control Technician" not in p_a[:shared]
    assert "Senior Data Scientist" not in p_b[:shared]
    # The invariant CV body MUST sit inside the shared prefix.
    assert "Junior Food Scientist" in p_a[:shared]


def test_cv_modifier_skill_prompt_has_invariant_prefix() -> None:
    ctx_a = (
        "# Job Context: Quality Control Technician at Nestlé\n"
        "## Required Skills\n- HACCP\n- GMP\n"
    )
    ctx_b = (
        "# Job Context: Senior Data Scientist at Acme Corp\n"
        "## Required Skills\n- PyTorch\n- MLOps\n"
    )
    p_a = CV_MODIFIER_SKILL.format(
        job_context_md=ctx_a,
        cv_tex=SAMPLE_CV,
        additional_context="None provided.",
    )
    p_b = CV_MODIFIER_SKILL.format(
        job_context_md=ctx_b,
        cv_tex=SAMPLE_CV,
        additional_context="None provided.",
    )
    shared = _shared_prefix_len(p_a, p_b)
    assert shared >= MIN_SHARED_PREFIX_CHARS, (
        f"CV_MODIFIER_SKILL shared prefix is only {shared} chars."
    )
    assert "Quality Control Technician" not in p_a[:shared]
    assert "Senior Data Scientist" not in p_b[:shared]
    assert "Junior Food Scientist" in p_a[:shared]


def test_cv_modifier_from_assessment_prompt_has_invariant_prefix() -> None:
    # Use unique tokens not present in SAMPLE_CV so we can prove they're
    # confined to the *suffix* of the prompt.
    gaps_a = '1. "Kubernetes" (criticality: 0.9) — no match on CV'
    gaps_b = '1. "PyTorch" (criticality: 0.9) — no match on CV'
    covered_a = "- Salesforce\n- Tableau"
    covered_b = "- Excel\n- VBA"
    p_a = CV_MODIFIER_FROM_ASSESSMENT.format(
        gaps_section=gaps_a, covered_section=covered_a, cv_tex=SAMPLE_CV
    )
    p_b = CV_MODIFIER_FROM_ASSESSMENT.format(
        gaps_section=gaps_b, covered_section=covered_b, cv_tex=SAMPLE_CV
    )
    shared = _shared_prefix_len(p_a, p_b)
    assert shared >= MIN_SHARED_PREFIX_CHARS, (
        f"CV_MODIFIER_FROM_ASSESSMENT shared prefix is only {shared} chars."
    )
    assert "Kubernetes" not in p_a[:shared]
    assert "PyTorch" not in p_b[:shared]
    assert "Junior Food Scientist" in p_a[:shared]


@pytest.mark.parametrize(
    "template,extra_kwargs",
    [
        (
            MOTIVATION_LETTER_PROMPT,
            {"letter_content": SAMPLE_LETTER_CONTENT},
        ),
        (
            JOB_ANALYZER_PROMPT,
            {"cv_content": SAMPLE_CV},
        ),
    ],
)
def test_no_variable_placeholder_precedes_invariant_data(
    template: str, extra_kwargs: dict[str, str]
) -> None:
    """Structural check: in the raw template, no `{job_*}` / `{company}` /
    `{job_description*}` placeholder appears before the invariant data
    placeholder (`{cv_content}` or `{letter_content}`).
    """
    invariant_key = next(iter(extra_kwargs))  # "cv_content" or "letter_content"
    invariant_idx = template.index("{" + invariant_key + "}")
    for var_key in ("job_title", "company", "job_description", "job_description_excerpt"):
        placeholder = "{" + var_key + "}"
        if placeholder not in template:
            continue
        var_idx = template.index(placeholder)
        assert var_idx > invariant_idx, (
            f"Variable placeholder {placeholder} at offset {var_idx} appears "
            f"BEFORE invariant {{{invariant_key}}} at offset {invariant_idx}; "
            f"this breaks Gemini prefix caching (LLM-01)."
        )
