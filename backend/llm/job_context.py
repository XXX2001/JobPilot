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
