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

    "linkedin_easy_apply": """

        You are on LinkedIn and you need to apply to a job using Easy Apply.

        The job application URL is: {apply_url}



        1. Navigate to the job URL and look for the "Easy Apply" button.

        2. Click "Easy Apply" to open the application modal.

        3. Work through the multi-step application form:

           - Step 1 (Contact info): Verify name, email, phone are pre-filled. If fields are

             empty, fill them using: name={applicant_name}, email={applicant_email},

             phone={applicant_phone}.

           - Step 2 (Work experience / CV): Upload the CV file at path {cv_pdf_path} if a

             file upload field appears. Otherwise skip.

           - Step 3+ (Screening questions): Answer each question using the answers dict:

             {additional_answers}. For yes/no questions: answer "Yes" unless the answer

             dict says otherwise. For numeric fields: enter the most appropriate number

             from the answers dict. For dropdowns: choose the closest matching option.

           - Final step: Review page — check that name/email look correct.

             DO NOT click Submit. Stop here and return status=review_required.

        4. If at any step you encounter an unexpected page or error, stop and return

           status=needs_human.

        5. After completing all steps up to (but not including) submit, return a JSON object:

           {{"status": "review_required", "filled_fields": {{field_name: value, ...}},

             "step_reached": <number>, "company": "{company}", "title": "{title}"}}

        IMPORTANT: max_steps applies — do not take more than 25 browser steps total.

        IMPORTANT: If you reach the Submit button, STOP — do NOT click it.

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

        If prompted to sign in, skip that step and continue extracting from the search page.

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

        If there is no Jobs panel, extract from the regular search results instead.

        Return the results as a JSON array.

    """,

    "welcome_to_the_jungle": """

        You are on Welcome to the Jungle (wttj.co / welcometothejungle.com).

        Search URL: https://www.welcometothejungle.com/en/jobs?query={keywords}&refinementList[offices.country_reference_code][0]={country_code}

        Or navigate to https://www.welcometothejungle.com/en/jobs and search for: {keywords}

        Apply location filter: {location}

        Extract job listings from the search results (first page only).

        For each job (up to {max_jobs}), extract:

        - title: The full job title

        - company: The company/startup name

        - location: Office location(s) shown

        - salary: Salary range if shown (null if not)

        - posted_date: When it was posted (null if not shown)

        - description_preview: First 200 chars of the job teaser text

        - apply_url: The full URL to the job detail page

        Do NOT click apply buttons. Do NOT sign in.

        Return the results as a JSON array.

    """,

    "glassdoor": """

        You are on Glassdoor.com. Search for jobs.

        Navigate to: https://www.glassdoor.com/Job/jobs.htm?suggestChosen=false&clickSource=searchBtn&typedKeyword={keywords}&locT=C&locId=0&jobType=all

        Or search for: {keywords} in {location}

        Extract job listings from the first page of results.

        For each job (up to {max_jobs}), extract:

        - title: The full job title

        - company: The company name

        - location: Where the job is located

        - salary: Salary estimate if shown (null if not)

        - posted_date: When it was posted (null if not shown)

        - description_preview: First 200 chars of the description

        - apply_url: The URL to the job detail page

        Dismiss any signup/login modals by pressing Escape or clicking the X.

        Do NOT click apply. Return the results as a JSON array.

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





# ---------------------------------------------------------------------------

# Site configuration — metadata for each supported source

# ---------------------------------------------------------------------------



SITE_CONFIGS: dict[str, dict] = {

    "linkedin": {

        "name": "linkedin",

        "display_name": "LinkedIn",

        "prompt_key": "linkedin",

        "apply_prompt_key": "linkedin_easy_apply",

        "requires_login": True,

        "apply_method": "auto",          # supports Easy Apply

        "type": "browser",

        "country_codes": ["gb", "us", "de", "fr", "nl", "ca", "au"],

        "base_url": "https://www.linkedin.com/jobs/",

    },

    "indeed": {

        "name": "indeed",

        "display_name": "Indeed",

        "prompt_key": "indeed",

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "browser",

        "country_codes": ["gb", "us", "ca", "au", "de", "fr"],

        "base_url": "https://www.indeed.com/jobs",

    },

    "google_jobs": {

        "name": "google_jobs",

        "display_name": "Google Jobs",

        "prompt_key": "google_jobs",

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "browser",

        "country_codes": ["gb", "us", "de", "fr", "nl"],

        "base_url": "https://www.google.com/search",

    },

    "welcome_to_the_jungle": {

        "name": "welcome_to_the_jungle",

        "display_name": "Welcome to the Jungle",

        "prompt_key": "welcome_to_the_jungle",

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "browser",

        "country_codes": ["fr", "gb", "de", "nl"],

        "base_url": "https://www.welcometothejungle.com/en/jobs",

    },

    "glassdoor": {

        "name": "glassdoor",

        "display_name": "Glassdoor",

        "prompt_key": "glassdoor",

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "browser",

        "country_codes": ["gb", "us"],

        "base_url": "https://www.glassdoor.com/Job/jobs.htm",

    },

    "adzuna": {

        "name": "adzuna",

        "display_name": "Adzuna",

        "prompt_key": None,              # uses API, not browser prompts

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "api",

        "country_codes": ["gb", "us", "au", "de", "fr", "nl", "ca"],

        "base_url": "https://api.adzuna.com/v1/api/jobs",

    },

    "lab_website": {

        "name": "lab_website",

        "display_name": "Research Lab",

        "prompt_key": "lab_website",

        "apply_prompt_key": None,

        "requires_login": False,

        "apply_method": "manual",

        "type": "lab_url",

        "country_codes": [],

        "base_url": "",

    },

}





def format_prompt(site: str, **kwargs) -> str:

    """Format a site-specific prompt with the given keyword arguments.



    Args:

        site: Site name key in SITE_PROMPTS.

        **kwargs: Variables to substitute into the template.



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

        "country_code": "gb",

        "apply_url": "",

        "applicant_name": "",

        "applicant_email": "",

        "applicant_phone": "",

        "cv_pdf_path": "",

        "additional_answers": "{}",

        "company": "",

        "title": "",

    }

    merged = {**defaults, **{k: str(v) for k, v in kwargs.items()}}



    # Use safe format (ignore unknown keys in template)

    try:

        return template.format(**merged)

    except KeyError:

        # Fallback: return template as-is if substitution fails

        return template





__all__ = ["SITE_PROMPTS", "SITE_CONFIGS", "format_prompt"]

