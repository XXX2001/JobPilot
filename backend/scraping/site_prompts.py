"""Site-specific prompt templates for the adaptive browser-use scraper."""

from __future__ import annotations

SITE_PROMPTS: dict[str, str] = {
    "linkedin": """
        You are on LinkedIn. The user is already logged in.
        Go to LinkedIn Jobs search: https://www.linkedin.com/jobs/
        Search for: {keywords}
        Apply filters if available: location={location}
        Extract job listings from the results (first page only).
        For each job listing (up to {max_jobs}), extract:
        - title: The full job title
        - company: The company name
        - location: Where the job is located
        - salary: Salary/compensation if shown (null if not)
        - posted_date: When it was posted (null if not shown)
        - description_preview: First 200 chars of the description preview
        - apply_url: The URL to the job detail page
        IMPORTANT: Do NOT click "Easy Apply" — only extract data.
        IMPORTANT: Do NOT navigate away from the search results page.
        Return the results as a JSON array with the above fields.
    """,
    "indeed": """
        You are on Indeed.com. Search for: {keywords} in {location}
        URL: https://www.indeed.com/jobs?q={keywords}&l={location}
        Extract job listings from the search results (first page only).
        For each job (up to {max_jobs}), extract:
        - title: The full job title
        - company: The company name
        - location: Where the job is located
        - salary: Salary if shown (null if not)
        - posted_date: When it was posted (null if not shown)
        - description_preview: First 200 chars of the description preview
        - apply_url: The direct job detail URL (not the Indeed redirect if possible)
        Do NOT click any apply buttons.
        Return the results as a JSON array.
    """,
    "google_jobs": """
        Go to Google and search: {keywords} jobs {location}
        Click on the "Jobs" tab or section in Google results if it appears.
        Extract job listings from the Google Jobs panel/section.
        For each job (up to {max_jobs}), extract:
        - title: The full job title
        - company: The company name
        - location: Where the job is located
        - salary: Salary if shown (null if not)
        - posted_date: When it was posted (null if not shown)
        - description_preview: First 200 chars of the description
        - apply_url: The link to the original job posting
        Return the results as a JSON array.
    """,
    "lab_website": """
        You are on a research lab or university careers page: {url}
        Find any job/position openings listed on this page.
        These may be labeled as: positions, openings, careers, jobs,
        PhD, postdoc, research engineer, software engineer, etc.
        For each position found (up to {max_jobs}), extract whatever is available:
        - title: The position title
        - company: The lab/institution name (infer from page if not explicit)
        - location: Location if shown (null if not)
        - salary: Salary/stipend if shown (null if not)
        - posted_date: When posted if shown (null if not)
        - description_preview: First 200 chars of description
        - apply_url: The link to apply or more details (use current URL if no direct link)
        Return the results as a JSON array. Return empty array [] if no positions found.
    """,
    "generic": """
        You are on a job board or careers page: {url}
        Find all job listings on the page.
        For each job (up to {max_jobs}), extract:
        - title: The job title
        - company: The company name
        - location: Where the job is located (null if not shown)
        - salary: Salary if shown (null if not)
        - posted_date: When it was posted (null if not shown)
        - description_preview: First 200 chars of description (null if not shown)
        - apply_url: The URL to the job detail or apply page
        Return the results as a JSON array. Return empty array [] if no jobs found.
    """,
}


def format_prompt(site: str, **kwargs) -> str:
    """Format a site-specific prompt with the given keyword arguments.

    Args:
        site: One of 'linkedin', 'indeed', 'google_jobs', 'lab_website', 'generic'
        **kwargs: Variables to substitute into the template (keywords, location, max_jobs, url)

    Returns:
        Formatted prompt string ready to pass to the browser-use Agent.
    """
    template = SITE_PROMPTS.get(site, SITE_PROMPTS["generic"])

    # Provide sensible defaults for all substitution variables
    defaults: dict[str, str] = {
        "keywords": "",
        "location": "",
        "max_jobs": "20",
        "url": "",
    }
    merged = {**defaults, **{k: str(v) for k, v in kwargs.items()}}

    # Use safe format (ignore unknown keys in template)
    try:
        return template.format(**merged)
    except KeyError:
        # Fallback: return template as-is if substitution fails
        return template


__all__ = ["SITE_PROMPTS", "format_prompt"]
