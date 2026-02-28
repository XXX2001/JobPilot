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
