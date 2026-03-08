"""Site-specific prompt templates for the adaptive browser-use scraper."""

from __future__ import annotations

SITE_PROMPTS: dict[str, str] = {

    "linkedin": """

        You are on LinkedIn. The user is already logged in.

        Go to LinkedIn Jobs search: https://www.linkedin.com/jobs/

        Search for: {keywords}

        Apply filters if available: location={location}

        Extract job listings from the search results (first page only).

        CRITICAL — DO NOT CLICK ANYTHING:
        - Do NOT click on any job card, job title, or company name.
        - Do NOT click "Easy Apply" or any apply button.
        - Do NOT navigate away from the search results page.
        - Read all data directly from the search results list without clicking.

        For each job listing visible in the results (up to {max_jobs}), extract:

        - title: The full job title (read from the card text)

        - company: The company name (read from the card text)

        - location: Where the job is located (read from the card text)

        - salary: Salary/compensation if shown on the card (null if not)

        - posted_date: When it was posted if shown on the card (null if not)

        - description_preview: Any description snippet visible on the card (null if not shown)

        - apply_url: Construct this as https://www.linkedin.com/jobs/view/{jobId}/
          where {jobId} is the numeric job ID found in the card's data attributes or
          in the currentJobId URL parameter when hovering. Look for aria-label, data-job-id,
          or href attributes on the job card element to find the job ID number.
          NEVER use the search results URL as apply_url.

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

        You are on Indeed. Search for: {keywords} in {location}

        Navigate to: https://{country_domain}/jobs?q={keywords}&l={location}

        Extract job listings from the search results (first page only).

        For each job (up to {max_jobs}), extract:

        - title: The full job title

        - company: The company name

        - location: Where the job is located

        - salary: Salary if shown (null if not)

        - posted_date: When it was posted (null if not shown)

        - description_preview: First 200 chars of the description preview

        - apply_url: The direct job detail URL (not the Indeed redirect if possible)

        Do NOT click any job titles, cards, or apply buttons.
        Read all data directly from the search results list without navigating away.

        If prompted to sign in, dismiss the modal and continue extracting from the search page.

        Return the results as a JSON array.

    """,

    "google_jobs": """

        Navigate DIRECTLY to this URL (do not use the search box):
        https://{google_domain}/search?q={keywords}+emplois+{location}&ibp=htl;jobs

        If the page shows a location/cookie consent popup, dismiss it by clicking "Reject all",
        "No thanks", or pressing Escape — then continue.

        Wait for the Google Jobs panel to appear in the search results.
        If the "Jobs" tab/section is not visible, try:
        https://{google_domain}/search?q={keywords}+jobs+{location}
        and look for the Jobs section.

        Extract job listings from the Google Jobs panel.
        Do NOT click on individual job cards — read all data from the panel list without clicking.

        For each job visible in the panel (up to {max_jobs}), extract:

        - title: The full job title

        - company: The company name

        - location: Where the job is located

        - salary: Salary if shown (null if not)

        - posted_date: When it was posted (null if not shown)

        - description_preview: Any description snippet visible in the panel (null if not shown)

        - apply_url: The href/link of the job card or "Apply" button pointing to the original posting

        If there is no Jobs panel at all, extract job listings from the regular search results.

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

        "login_url": "https://www.linkedin.com/login",

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

    # Build country_domain from country_code if not explicitly provided
    _country_code = str(kwargs.get("country_code", "gb")).lower()
    _INDEED_DOMAINS = {
        "fr": "fr.indeed.com", "gb": "uk.indeed.com", "de": "de.indeed.com",
        "es": "es.indeed.com", "it": "it.indeed.com", "nl": "indeed.nl",
        "be": "be.indeed.com", "ca": "ca.indeed.com", "au": "au.indeed.com",
        "us": "www.indeed.com", "in": "in.indeed.com", "br": "br.indeed.com",
        "sg": "sg.indeed.com",
    }
    _country_domain = _INDEED_DOMAINS.get(_country_code, f"{_country_code}.indeed.com")

    _GOOGLE_DOMAINS = {
        "fr": "www.google.fr", "gb": "www.google.co.uk", "de": "www.google.de",
        "es": "www.google.es", "it": "www.google.it", "nl": "www.google.nl",
        "be": "www.google.be", "ca": "www.google.ca", "au": "www.google.com.au",
        "us": "www.google.com", "in": "www.google.co.in", "br": "www.google.com.br",
        "sg": "www.google.com.sg",
    }
    _google_domain = _GOOGLE_DOMAINS.get(_country_code, "www.google.com")

    defaults: dict[str, str] = {

        "keywords": "",

        "location": "",

        "max_jobs": "20",

        "url": "",

        "country_code": _country_code,

        "country_domain": _country_domain,

        "google_domain": _google_domain,

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

