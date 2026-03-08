from __future__ import annotations

CV_SUMMARY_PROMPT = """You are a professional CV editor. Make MINIMAL, surgical edits \
to this professional summary to better match the target job posting.

RULES:
- Change at most 2-3 phrases. Keep the rest IDENTICAL.
- Keep the same tone, formality level, and approximate length.
- Highlight skills/experience relevant to the job posting.
- NEVER invent skills or experience the candidate doesn't have.
- NEVER change LaTeX formatting commands.
- Respond in the SAME LANGUAGE as the original text.

## Target Job:
{job_title} at {company}
{job_description_excerpt}

## Current Summary:
{current_summary}

## Return JSON:
{{
    "edited_summary": "the edited text (or null if no changes needed)",
    "changes_made": ["brief description of each change"]
}}"""

CV_EXPERIENCE_PROMPT = """You are a professional CV editor. Make MINIMAL edits \
to these experience bullet points to better match the target job posting.

RULES:
- Edit at most 2-3 bullets. Leave the rest UNCHANGED.
- Only REPHRASE existing achievements to emphasize relevant skills.
- NEVER fabricate new achievements or metrics.
- Keep the same structure: "Action verb + what + quantified result"
- NEVER change LaTeX commands (\\textbf, \\item, \\\\, \\hfill, etc.)
- Respond in the SAME LANGUAGE as the original text.

## Target Job:
{job_title} at {company}
Key requirements: {key_requirements}

## Current Experience Bullets:
{bullets_json}

## Return JSON:
{{
    "edits": [
        {{"index": 0, "original": "...", "edited": "...", "reason": "..."}},
        ...
    ]
}}
Only include bullets that were actually changed."""

MOTIVATION_LETTER_PROMPT = """You are a professional cover letter editor. \
Make MINIMAL edits to this motivation letter template for the target job.

RULES:
- Replace {{company_name}} placeholder with the actual company name.
- Edit the CUSTOMIZABLE PARAGRAPH (marked between JOBPILOT markers) \
  to reference 1-2 specific aspects of the job/company.
- Keep all other paragraphs IDENTICAL.
- Same tone, same formality, same length.
- Respond in the SAME LANGUAGE as the original text.

## Target Job:
{job_title} at {company}
{job_description_excerpt}

## Current Letter (with markers):
{letter_content}

## Return JSON:
{{
    "edited_paragraph": "the customized paragraph text",
    "company_name": "{company}"
}}"""

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
