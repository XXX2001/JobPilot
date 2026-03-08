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
        matches_text = (
            "\n".join(f"- {s}" for s in self.candidate_matches)
            or "- (none identified)"
        )
        gaps_text = (
            "\n".join(f"- {g}" for g in self.candidate_gaps)
            if self.candidate_gaps
            else "- (none — candidate matches all requirements)"
        )
        nice_text = (
            "\n".join(f"- {s}" for s in self.nice_to_have_skills)
            or "- (none listed)"
        )
        keywords_text = (
            "\n".join(f"- {k}" for k in self.keywords)
            or "- (none identified)"
        )
        hints_text = (
            "\n".join(f"{i+1}. {h}" for i, h in enumerate(self.top_changes_hint[:3]))
            or "- (none suggested)"
        )
        locked_text = (
            "\n".join(f"- {d}" for d in self.do_not_touch)
            or "- (none specified — use default list)"
        )
        return f"""# Job Context: {job_title} at {company}

## Required Skills (candidate HAS these)
{matches_text}

## Required Skills (candidate LACKS — motivation framing only)
{gaps_text}

## Nice-to-Have Skills
{nice_text}

## Keywords to Weave In
{keywords_text}

## Top Suggested Changes (max 3)
{hints_text}

## DO NOT TOUCH (these fields must remain identical)
{locked_text}
"""
