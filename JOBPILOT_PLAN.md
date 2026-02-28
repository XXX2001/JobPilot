# JobPilot — Full Project Plan

> AI-powered job application assistant with adaptive scraping, surgical CV/letter tailoring, and hybrid auto-apply.

**Author**: Auto-generated from interview session  
**Date**: 2026-02-28  
**Status**: Planning Phase  

---

## Table of Contents

1. [Project Vision](#1-project-vision)
2. [Requirements Summary](#2-requirements-summary)
3. [Architecture Overview](#3-architecture-overview)
4. [Tech Stack](#4-tech-stack)
5. [Core Module Designs](#5-core-module-designs)
   - 5.1 [Adaptive Scraping Engine (browser-use + Gemini)](#51-adaptive-scraping-engine)
   - 5.2 [Job Matching & Ranking](#52-job-matching--ranking)
   - 5.3 [LaTeX Pipeline (CV + Motivation Letter)](#53-latex-pipeline)
   - 5.4 [Application Engine (Hybrid Apply)](#54-application-engine)
   - 5.5 [Dashboard UI](#55-dashboard-ui)
   - 5.6 [Scheduler & Orchestration](#56-scheduler--orchestration)
6. [Data Model](#6-data-model)
7. [Project Structure](#7-project-structure)
8. [Installation & Packaging (Cross-Platform)](#8-installation--packaging)
9. [Implementation Phases](#9-implementation-phases)
10. [API & Cost Analysis](#10-api--cost-analysis)
11. [Risk Matrix & Mitigations](#11-risk-matrix--mitigations)
12. [Legal & Ethical Considerations](#12-legal--ethical-considerations)
13. [Future Enhancements](#13-future-enhancements)

---

## 1. Project Vision

A **local web application** that runs on Windows and Linux, automating the tedious parts of job searching:

1. **Discover** — Scrape jobs from 8+ sources using an LLM-powered adaptive scraper (no brittle CSS selectors)
2. **Match** — Rank jobs against your profile using keywords, filters, and relevance scoring
3. **Tailor** — Surgically edit your LaTeX CV and motivation letter per job (minimal, targeted changes)
4. **Review** — Present a morning dashboard where you approve, skip, or customize each application
5. **Apply** — Auto-fill Easy Apply forms where possible, or open the page for manual submission
6. **Track** — Full application lifecycle tracking: applied → heard back → interview → offer/rejection

**Key Differentiator**: Instead of traditional scrapers with hardcoded CSS selectors that break whenever a site updates, JobPilot uses `browser-use` — an AI agent framework that combines Playwright with Google Gemini. The LLM "sees" the page, understands its structure, and extracts job data intelligently. This makes the pipeline **self-healing** and able to handle any website without custom adapters.

---

## 2. Requirements Summary

| Category | Requirement | Source |
|---|---|---|
| **Platforms** | LinkedIn, Indeed, Glassdoor, Google Jobs, Welcome to the Jungle, Dice/AngelList, custom lab URLs | Interview R1 |
| **Job Types** | Mixed technical roles (software, data, ML, DevOps, etc.) | Interview R1 |
| **CV** | Existing LaTeX template, surgical edits to summary + experience sections | Interview R1, R2 |
| **Motivation Letter** | LaTeX template with minor per-job edits (swap company, tailor 1 paragraph) | Interview R2 |
| **Apply Method** | Hybrid: auto-apply (Easy Apply), assisted (pre-fill complex forms), manual (open URL) | Interview R2 |
| **Daily Limit** | 10 applications/day (self-imposed discipline) | Interview R1 |
| **Workflow** | Morning batch: scrape → match → generate docs → present queue → review → apply | Interview R2 |
| **Authentication** | Manual login to job sites, bot takes over authenticated session | Interview R3 |
| **Job Matching** | Keywords + filters (location, salary, experience, remote, job type, language) | Interview R3 |
| **Storage** | Local SQLite database | Interview R2 |
| **Notifications** | Dashboard only (no email/push) | Interview R2 |
| **LaTeX Compilation** | Bundled Tectonic (no TeX Live dependency) | Interview R3 |
| **Language** | International/mixed (English + French + others) | Interview R3 |
| **Budget** | Zero — free API tiers only | Interview R2 |
| **UI** | Modern dashboard (Notion/Linear aesthetic), dark mode | Interview R2 |
| **OS** | Windows + Linux compatible, smooth installation | Updated requirement |
| **Scraping** | LLM-driven adaptive scraping via browser-use (no hardcoded selectors) | Updated requirement |

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     USER'S BROWSER                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │            SvelteKit Frontend (localhost:5173)          │  │
│  │                                                        │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────────┐  │  │
│  │  │  Morning    │  │  Job       │  │  Application    │  │  │
│  │  │  Queue      │  │  Details   │  │  Tracker        │  │  │
│  │  └────────────┘  └────────────┘  └─────────────────┘  │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────────┐  │  │
│  │  │  CV/Letter  │  │  Settings  │  │  Analytics      │  │  │
│  │  │  Preview    │  │  & Config  │  │  Dashboard      │  │  │
│  │  └────────────┘  └────────────┘  └─────────────────┘  │  │
│  └───────────────────────┬────────────────────────────────┘  │
└──────────────────────────┼───────────────────────────────────┘
                           │ REST API + WebSocket (live updates)
┌──────────────────────────┼───────────────────────────────────┐
│               FastAPI Backend (localhost:8000)                 │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Adaptive Scraping Engine                     │ │
│  │  ┌──────────────┐   ┌─────────────────────────────┐    │ │
│  │  │ Adzuna API   │   │  browser-use Agent           │    │ │
│  │  │ (structured  │   │  (Gemini + Playwright)       │    │ │
│  │  │  free API)   │   │                              │    │ │
│  │  └──────────────┘   │  "Go to {url}, find job      │    │ │
│  │                      │   listings, extract title,   │    │ │
│  │                      │   company, location, desc,   │    │ │
│  │                      │   apply_url for each job"    │    │ │
│  │                      └─────────────────────────────┘    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │  Job Matcher &   │  │  LaTeX Pipeline                  │  │
│  │  Ranker          │  │  ┌──────────┐  ┌──────────────┐ │  │
│  │  (keyword +      │  │  │ TexSoup  │→ │ Gemini Flash │ │  │
│  │   filter score)  │  │  │ (parse)  │  │ (edit JSON)  │ │  │
│  └─────────────────┘  │  └──────────┘  └──────┬───────┘ │  │
│                        │  ┌──────────┐  ┌──────┴───────┐ │  │
│  ┌─────────────────┐  │  │ TexSoup  │← │ Validate     │ │  │
│  │  Application     │  │  │ (inject) │  │ (chktex)     │ │  │
│  │  Engine          │  │  └────┬─────┘  └──────────────┘ │  │
│  │  (browser-use    │  │       │                          │  │
│  │   auto-apply)    │  │  ┌────┴─────────────────────┐   │  │
│  └─────────────────┘  │  │ Tectonic (compile → PDF)  │   │  │
│                        │  └──────────────────────────┘   │  │
│  ┌──────────────────────────────────────────────────────┐│  │
│  │              SQLite (via SQLAlchemy)                   ││  │
│  └──────────────────────────────────────────────────────┘│  │
└──────────────────────────────────────────────────────────────┘
```

**Why this architecture:**

- **FastAPI** — Async Python, perfect for I/O-heavy scraping + LLM calls. WebSocket support for live updates.
- **browser-use + Gemini** — The LLM acts as the "eyes" of the scraper. No per-site selector maintenance. It reads the page like a human would.
- **SvelteKit** — Lightweight, fast, excellent DX. Pre-built as static files, served by FastAPI in production.
- **Local-first** — Everything runs on your machine. No cloud dependency. Your data stays yours.

---

## 4. Tech Stack

| Layer | Technology | Version | Why This Choice |
|---|---|---|---|
| **Runtime** | Python 3.12+ | 3.12 | Type hints, async, broad ecosystem |
| **Package Manager** | `uv` (Astral) | latest | 10-100x faster than pip, cross-platform, handles venvs |
| **Backend Framework** | FastAPI | 0.115+ | Async, auto-docs, WebSocket, fast |
| **Browser Automation** | browser-use | latest | LLM-driven Playwright agent. Adapts to any website |
| **LLM** | Google Gemini 2.0 Flash | via `google-generativeai` | Free tier: 15 RPM, 1M tokens/min. Native `ChatGoogle` in browser-use |
| **LaTeX Parsing** | TexSoup | 0.3+ | DOM-like LaTeX parsing, find/replace sections |
| **LaTeX Compilation** | Tectonic | latest | Self-contained TeX engine, auto-downloads packages, cross-platform binary |
| **LaTeX Validation** | chktex | bundled | Catches syntax errors before compilation |
| **Database** | SQLite + SQLAlchemy | 2.0+ | Zero-config, local, async support via `aiosqlite` |
| **Task Scheduling** | APScheduler | 3.10+ | Cron-like scheduling for morning batch |
| **Frontend Framework** | SvelteKit | 2.x | Fast, small bundle, great transitions/animations |
| **UI Components** | shadcn-svelte + TailwindCSS | latest | Notion/Linear aesthetic out of the box |
| **Job Search API** | Adzuna | v1 | Free tier: 250 calls/day. Legal, structured, international |
| **Dev Server** | Vite | 5.x | Bundled with SvelteKit, HMR for development |

### Why browser-use Instead of Traditional Scrapers

| Aspect | Traditional Scrapers | browser-use + LLM |
|---|---|---|
| **Setup per site** | Write custom selectors, test, maintain | Give a goal prompt — the LLM figures it out |
| **Maintenance** | Breaks when site updates HTML/CSS | Self-healing — LLM adapts to new layouts |
| **New site support** | Days of development per site | Works immediately on any website |
| **Cost** | Free (but high dev time) | ~$0 with Gemini free tier (15 RPM) |
| **Speed** | Fast (direct DOM queries) | Slower (LLM reasoning per page) |
| **Reliability** | Fragile (selector-dependent) | Robust (semantic understanding) |

**For our use case (10 apps/day, ~50 pages scraped/morning)**, the LLM cost is negligible and the maintenance savings are massive.

### browser-use + Gemini Integration

browser-use has **native Gemini support** via `ChatGoogle`:

```python
from browser_use import Agent, Browser, ChatGoogle

llm = ChatGoogle(model="gemini-2.0-flash", api_key=os.getenv("GOOGLE_API_KEY"))

agent = Agent(
    task="""
    Go to https://jobs.example.com/careers
    Find all job listings on the page.
    For each job, extract: title, company, location, posted_date, apply_url.
    Return the results as a JSON array.
    """,
    llm=llm,
    browser=Browser()
)

result = await agent.run()
```

The same agent framework handles both **scraping** (extracting job data) and **applying** (filling forms, uploading files):

```python
from browser_use.tools.views import UploadFileAction

agent = Agent(
    task=f"""
    Go to {job.apply_url}
    Fill out the application form with this information: {applicant_info}
    Upload the resume from the file upload field.
    Submit the application.
    """,
    llm=llm,
    browser=Browser(),
    tools=[UploadFileAction(file_path=str(cv_pdf_path))]
)
```

---

## 5. Core Module Designs

### 5.1 Adaptive Scraping Engine

The scraping engine uses a **two-tier approach**:

#### Tier 1: Structured APIs (Fast, Reliable, Free)

```python
# Adzuna API — 250 free calls/day, covers most international job boards
class AdzunaClient:
    """Structured job search via Adzuna REST API."""
    
    BASE_URL = "https://api.adzuna.com/v1/api/jobs"
    
    async def search(
        self, 
        keywords: list[str],
        filters: JobFilters,
        country: str = "gb",  # gb, us, fr, de, etc.
        page: int = 1,
        results_per_page: int = 20
    ) -> list[RawJob]:
        """
        Search Adzuna for jobs matching keywords + filters.
        Free tier: 250 calls/day (plenty for 10 apps/day).
        Returns structured JSON with title, company, location, 
        salary, description, redirect_url.
        """
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": " ".join(keywords),
            "where": filters.location or "",
            "salary_min": filters.salary_min,
            "full_time": 1 if "full-time" in filters.job_types else 0,
            "results_per_page": results_per_page,
            "page": page,
        }
        # ... HTTP call, parse response into RawJob objects
```

#### Tier 2: LLM-Driven Browser Agent (Adaptive, Any Website)

```python
from browser_use import Agent, Browser, BrowserConfig, ChatGoogle

class AdaptiveScraper:
    """
    LLM-powered scraper that works on ANY website.
    Uses browser-use + Gemini to understand page structure
    and extract job data without hardcoded selectors.
    """
    
    def __init__(self, gemini_api_key: str):
        self.llm = ChatGoogle(
            model="gemini-2.0-flash", 
            api_key=gemini_api_key
        )
        self.browser_config = BrowserConfig(
            headless=False,  # Headful for manual login sites
        )
    
    async def scrape_job_listings(
        self, 
        url: str, 
        keywords: list[str],
        max_jobs: int = 20
    ) -> list[RawJob]:
        """
        Navigate to a job listing page and extract all jobs.
        The LLM reads the page and returns structured data.
        """
        browser = Browser(config=self.browser_config)
        
        agent = Agent(
            task=f"""
            Navigate to: {url}
            
            This is a job listings page. Find all job postings visible on the page.
            If there are search/filter fields, search for: {', '.join(keywords)}
            
            For each job listing found (up to {max_jobs}), extract:
            - title: The job title
            - company: The company name
            - location: Where the job is located
            - salary: Salary if shown (null if not)
            - posted_date: When it was posted (null if not shown)
            - description_preview: First 200 chars of the description
            - apply_url: The URL to apply (the link to the job detail page)
            
            Return the results as a JSON array.
            If there are multiple pages, only scrape the first page.
            Do NOT click on any job or navigate away from the listings page.
            """,
            llm=self.llm,
            browser=browser,
        )
        
        result = await agent.run()
        return self._parse_agent_result(result)
    
    async def scrape_job_details(self, job_url: str) -> JobDetails:
        """
        Navigate to a single job posting and extract full details.
        """
        browser = Browser(config=self.browser_config)
        
        agent = Agent(
            task=f"""
            Navigate to: {job_url}
            
            This is a job posting page. Extract the FULL details:
            - title: Full job title
            - company: Company name
            - location: Full location (city, country, remote status)
            - salary: Salary range if shown
            - description: FULL job description text
            - requirements: List of requirements/qualifications
            - benefits: List of benefits if shown
            - apply_url: The direct apply button/link URL
            - apply_method: Is this "Easy Apply" / "one-click" or does it redirect to another site?
            - posted_date: When posted
            
            Return as JSON. Do NOT click the apply button.
            """,
            llm=self.llm,
            browser=browser,
        )
        
        result = await agent.run()
        return self._parse_job_details(result)
```

#### Site-Specific Strategies (Prompts, Not Code)

Instead of writing different scraper classes per site, we use **different prompts**:

```python
# Site-specific prompt templates (the only per-site config needed)
SITE_PROMPTS: dict[str, str] = {
    "linkedin": """
        You are on LinkedIn. The user is already logged in.
        Go to LinkedIn Jobs search. Search for: {keywords}
        Apply filters: {filters}
        Extract job listings from the results.
        IMPORTANT: Do NOT click "Easy Apply" — only extract data.
    """,
    
    "indeed": """
        You are on Indeed.com. Search for: {keywords} in {location}
        Extract job listings from the search results.
        For each job, get the direct job URL (not the Indeed redirect).
    """,
    
    "google_jobs": """
        Go to Google and search: {keywords} jobs {location}
        Click on the "Jobs" tab/section in Google results.
        Extract job listings from the Google Jobs panel.
    """,
    
    "lab_website": """
        You are on a research lab or university careers page: {url}
        Find any job/position openings listed on this page.
        These may be labeled as: positions, openings, careers, jobs, 
        PhD, postdoc, research engineer, etc.
        Extract whatever is available.
    """,
    
    "generic": """
        You are on a job board or careers page: {url}
        Find all job listings. Extract structured data for each.
    """,
}
```

#### Browser Session Management

For sites requiring login (LinkedIn, Indeed, Glassdoor):

```python
class BrowserSessionManager:
    """
    Manages persistent browser sessions.
    User logs in manually once, session is reused.
    """
    
    SESSIONS_DIR = Path("data/browser_sessions")
    
    async def get_or_create_session(self, site: str) -> Browser:
        """
        Returns a browser with the user's saved session.
        If no session exists, opens browser for manual login.
        """
        storage_path = self.SESSIONS_DIR / f"{site}_state.json"
        
        if storage_path.exists():
            # Reuse saved session (cookies, localStorage)
            config = BrowserConfig(
                headless=False,
                storage_state=str(storage_path),
            )
            return Browser(config=config)
        else:
            # First time: open browser, let user log in
            browser = Browser(config=BrowserConfig(headless=False))
            # Signal to UI: "Please log into {site} in the browser window"
            # After user confirms login, save state:
            await browser.save_storage_state(str(storage_path))
            return browser
```

#### Scraping Orchestrator

```python
class ScrapingOrchestrator:
    """
    Coordinates all job sources for a morning batch.
    Runs API calls first (fast), then browser scraping (slower).
    """
    
    async def run_morning_batch(
        self, 
        keywords: list[str], 
        filters: JobFilters,
        sources: list[JobSource]
    ) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        
        # Phase 1: API sources (fast, parallel)
        api_sources = [s for s in sources if s.type == "api"]
        api_tasks = [
            self.adzuna.search(keywords, filters, country=s.config.country)
            for s in api_sources
        ]
        api_results = await asyncio.gather(*api_tasks, return_exceptions=True)
        all_jobs.extend(flatten_results(api_results))
        
        # Phase 2: Browser sources (sequential to avoid detection)
        browser_sources = [s for s in sources if s.type == "browser"]
        for source in browser_sources:
            try:
                browser = await self.session_mgr.get_or_create_session(source.name)
                prompt = SITE_PROMPTS.get(source.name, SITE_PROMPTS["generic"])
                jobs = await self.adaptive_scraper.scrape_job_listings(
                    url=source.url,
                    keywords=keywords,
                    prompt_template=prompt
                )
                all_jobs.extend(jobs)
                
                # Human-like delay between sites
                await asyncio.sleep(random.uniform(3, 8))
            except Exception as e:
                logger.warning(f"Scraping {source.name} failed: {e}")
                # Graceful degradation — skip this source, continue others
        
        # Phase 3: Lab websites (parallel, no login needed)
        lab_sources = [s for s in sources if s.type == "lab_url"]
        lab_tasks = [
            self.adaptive_scraper.scrape_job_listings(
                url=s.url, keywords=keywords,
                prompt_template=SITE_PROMPTS["lab_website"]
            )
            for s in lab_sources
        ]
        lab_results = await asyncio.gather(*lab_tasks, return_exceptions=True)
        all_jobs.extend(flatten_results(lab_results))
        
        # Deduplicate
        return self.deduplicator.deduplicate(all_jobs)
```

---

### 5.2 Job Matching & Ranking

```python
@dataclass
class JobFilters:
    keywords: list[str]            # "machine learning", "python developer"
    excluded_keywords: list[str]   # "senior staff", "10+ years", "clearance"
    locations: list[str]           # "Paris", "Remote", "Berlin"
    salary_min: int | None         # Minimum salary (in local currency)
    experience_range: tuple[int, int] | None  # e.g., (1, 5) years
    remote_only: bool              # Only remote positions
    job_types: list[str]           # "full-time", "contract", "internship"
    languages: list[str]           # "English", "French"
    excluded_companies: list[str]  # Blacklist

class JobMatcher:
    """Scores and ranks jobs against user profile and filters."""
    
    def score(self, job: JobDetails, filters: JobFilters) -> float:
        """
        Returns 0-100 relevance score.
        
        Scoring weights:
        - Keyword match (TF-IDF on description): 40%
        - Location match: 20%
        - Experience level match: 15%
        - Salary match: 10%
        - Recency (newer = better): 10%
        - Exclusion penalty (blacklisted terms): -100% (auto-skip)
        """
        score = 0.0
        
        # Keyword matching (TF-IDF or simple overlap)
        keyword_score = self._keyword_match(job.description, filters.keywords)
        score += keyword_score * 40
        
        # Exclusion check (instant disqualify)
        if self._has_excluded_terms(job, filters):
            return 0.0
        
        # Location
        score += self._location_match(job.location, filters) * 20
        
        # Experience level
        score += self._experience_match(job, filters) * 15
        
        # Salary
        score += self._salary_match(job, filters) * 10
        
        # Recency
        score += self._recency_score(job.posted_date) * 10
        
        # Company blacklist
        if job.company in filters.excluded_companies:
            return 0.0
        
        return min(100.0, score)
    
    def rank_and_filter(
        self, 
        jobs: list[JobDetails], 
        filters: JobFilters, 
        min_score: float = 30.0
    ) -> list[tuple[JobDetails, float]]:
        """Score all jobs, filter below threshold, sort by score desc."""
        scored = [(job, self.score(job, filters)) for job in jobs]
        filtered = [(j, s) for j, s in scored if s >= min_score]
        return sorted(filtered, key=lambda x: x[1], reverse=True)
```

#### Deduplication

Jobs appear on multiple boards. We deduplicate by normalized key:

```python
class JobDeduplicator:
    def _make_key(self, job: RawJob) -> str:
        """Normalize and hash: company + title + location."""
        norm = lambda s: re.sub(r'\s+', ' ', s.lower().strip())
        key = f"{norm(job.company)}|{norm(job.title)}|{norm(job.location)}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def deduplicate(self, jobs: list[RawJob]) -> list[RawJob]:
        seen: dict[str, RawJob] = {}
        for job in jobs:
            key = self._make_key(job)
            if key not in seen:
                seen[key] = job
            else:
                # Keep the one with more data (longer description)
                if len(job.description or "") > len(seen[key].description or ""):
                    seen[key] = job
        return list(seen.values())
```

---

### 5.3 LaTeX Pipeline

**Core Principle**: The LLM never sees or rewrites the full LaTeX document. It only edits extracted text content, which is injected back into the LaTeX structure.

```
                    ┌──────────────────────┐
                    │   base_cv.tex        │
                    │   (user's template)  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   TexSoup Parser     │
                    │   Extract sections:  │
                    │   - summary          │
                    │   - experience[]     │
                    └──────────┬───────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                  │
    ┌─────────▼──────────┐            ┌─────────▼──────────┐
    │  Gemini Flash       │            │  Gemini Flash       │
    │  "Edit summary      │            │  "Edit experience   │
    │   for this job"     │            │   bullets for this  │
    │                     │            │   job"              │
    │  → JSON diff        │            │  → JSON diff        │
    └─────────┬──────────┘            └─────────┬──────────┘
              │                                  │
              └────────────────┬────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Validate JSON      │
                    │   (schema check)     │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   TexSoup Injector   │
                    │   Replace sections   │
                    │   in .tex copy       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   chktex Validate    │
                    │   (syntax check)     │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Tectonic Compile   │
                    │   .tex → .pdf        │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Output: tailored   │
                    │   CV.pdf + diff view │
                    └──────────────────────┘
```

#### LaTeX Section Markers

User's LaTeX template should include comment markers for editable sections:

```latex
% --- JOBPILOT:SUMMARY:START ---
Experienced software engineer with 5 years of experience in 
distributed systems, machine learning pipelines, and cloud 
infrastructure. Passionate about building scalable solutions.
% --- JOBPILOT:SUMMARY:END ---

\section{Experience}
% --- JOBPILOT:EXPERIENCE:START ---
\textbf{Software Engineer} \hfill 2022--Present \\
\textit{TechCorp, Paris}
\begin{itemize}
    \item Designed and implemented distributed data pipeline processing 10TB/day
    \item Led migration from monolith to microservices, reducing deploy time by 80\%
    \item Mentored 3 junior engineers on system design best practices
\end{itemize}
% --- JOBPILOT:EXPERIENCE:END ---
```

If no markers exist, TexSoup will attempt to find sections by `\section{}` commands.

#### LLM Prompts for CV Editing

```python
CV_SUMMARY_PROMPT = """You are a professional CV editor. Make MINIMAL, surgical edits 
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

CV_EXPERIENCE_PROMPT = """You are a professional CV editor. Make MINIMAL edits 
to these experience bullet points to better match the target job posting.

RULES:
- Edit at most 2-3 bullets. Leave the rest UNCHANGED.
- Only REPHRASE existing achievements to emphasize relevant skills.
- NEVER fabricate new achievements or metrics.
- Keep the same structure: "Action verb + what + quantified result"
- NEVER change LaTeX commands (\textbf, \item, \\, \hfill, etc.)
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

MOTIVATION_LETTER_PROMPT = """You are a professional cover letter editor. 
Make MINIMAL edits to this motivation letter template for the target job.

RULES:
- Replace {company_name} placeholder with the actual company name.
- Edit the CUSTOMIZABLE PARAGRAPH (marked between JOBPILOT markers) 
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
```

#### Compilation Pipeline

```python
class LaTeXCompiler:
    """Compiles .tex to .pdf using Tectonic."""
    
    def __init__(self):
        self.tectonic_path = self._find_tectonic()
    
    def _find_tectonic(self) -> Path:
        """Find or download Tectonic binary."""
        # Check if tectonic is in PATH
        tectonic = shutil.which("tectonic")
        if tectonic:
            return Path(tectonic)
        
        # Check bundled binary
        bundled = Path(__file__).parent.parent / "bin" / self._tectonic_binary_name()
        if bundled.exists():
            return bundled
        
        raise RuntimeError(
            "Tectonic not found. Run 'jobpilot setup' to download it."
        )
    
    def _tectonic_binary_name(self) -> str:
        if sys.platform == "win32":
            return "tectonic.exe"
        return "tectonic"
    
    async def compile(self, tex_path: Path, output_dir: Path) -> Path:
        """Compile .tex to .pdf. Returns path to output PDF."""
        proc = await asyncio.create_subprocess_exec(
            str(self.tectonic_path),
            str(tex_path),
            "--outdir", str(output_dir),
            "--keep-logs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise LaTeXCompilationError(
                f"Tectonic failed:\n{stderr.decode()}"
            )
        
        pdf_path = output_dir / tex_path.with_suffix(".pdf").name
        return pdf_path
```

#### Full CV Pipeline

```python
class CVPipeline:
    """End-to-end: extract → edit → validate → compile."""
    
    def __init__(self, gemini_client, compiler: LaTeXCompiler):
        self.gemini = gemini_client
        self.compiler = compiler
        self.parser = LaTeXParser()
    
    async def generate_tailored_cv(
        self,
        base_cv_path: Path,
        job: JobDetails,
        output_dir: Path,
    ) -> TailoredCV:
        """
        Generate a tailored CV for a specific job.
        Returns the PDF path + a diff of what changed.
        """
        # 1. Parse the base CV
        sections = self.parser.extract_sections(base_cv_path)
        
        # 2. Ask Gemini for surgical edits (parallel)
        summary_edit, experience_edits = await asyncio.gather(
            self.gemini.edit_summary(sections.summary, job),
            self.gemini.edit_experience(sections.experience_bullets, job),
        )
        
        # 3. Validate the edits (schema check)
        self._validate_edits(summary_edit, experience_edits)
        
        # 4. Create a modified copy of the .tex file
        modified_tex = self.parser.inject_edits(
            base_cv_path, summary_edit, experience_edits
        )
        modified_path = output_dir / f"cv_{job.id}.tex"
        modified_path.write_text(modified_tex)
        
        # 5. Validate LaTeX syntax
        await self._validate_latex(modified_path)
        
        # 6. Compile to PDF
        pdf_path = await self.compiler.compile(modified_path, output_dir)
        
        # 7. Generate diff for user review
        diff = self._generate_diff(sections, summary_edit, experience_edits)
        
        return TailoredCV(
            tex_path=modified_path,
            pdf_path=pdf_path,
            diff=diff,
            job_id=job.id,
        )
```

---

### 5.4 Application Engine

Three modes, all powered by browser-use:

```python
class ApplicationEngine:
    """Hybrid application engine with three modes."""
    
    async def apply(
        self, 
        job: JobMatch, 
        cv_pdf: Path, 
        letter_pdf: Path,
        mode: ApplyMode,
        user_info: ApplicantInfo,
    ) -> ApplicationResult:
        
        match mode:
            case ApplyMode.AUTO:
                return await self._auto_apply(job, cv_pdf, letter_pdf, user_info)
            case ApplyMode.ASSISTED:
                return await self._assisted_apply(job, cv_pdf, letter_pdf, user_info)
            case ApplyMode.MANUAL:
                return await self._manual_apply(job, cv_pdf, letter_pdf)
    
    async def _auto_apply(self, job, cv_pdf, letter_pdf, user_info):
        """
        Full automation via browser-use agent.
        Best for: LinkedIn Easy Apply, simple one-page forms.
        """
        agent = Agent(
            task=f"""
            Go to: {job.apply_url}
            
            Fill out the job application form with this information:
            - Name: {user_info.full_name}
            - Email: {user_info.email}
            - Phone: {user_info.phone}
            - Location: {user_info.location}
            
            Upload the resume/CV when prompted.
            Upload the cover/motivation letter if there's a field for it.
            
            Answer any additional questions using this context:
            {user_info.additional_answers_json}
            
            After filling everything, PAUSE and DO NOT submit yet.
            Report what you've filled in.
            """,
            llm=self.llm,
            browser=self.browser,
            tools=[
                UploadFileAction(file_path=str(cv_pdf)),
                UploadFileAction(file_path=str(letter_pdf)),
            ],
        )
        
        result = await agent.run()
        
        # Send result to frontend for user confirmation
        await self.websocket.send_json({
            "type": "apply_review",
            "job_id": job.id,
            "filled_fields": result.extracted_content,
            "screenshot": result.screenshot_base64,
            "action_required": "confirm_submit",
        })
        
        # Wait for user to confirm or cancel
        confirmation = await self.websocket.receive_json()
        
        if confirmation["action"] == "submit":
            # Click submit
            submit_agent = Agent(
                task="Click the Submit/Apply button to send the application.",
                llm=self.llm,
                browser=self.browser,
            )
            await submit_agent.run()
            return ApplicationResult(status="applied", method="auto")
        else:
            return ApplicationResult(status="cancelled", method="auto")
    
    async def _assisted_apply(self, job, cv_pdf, letter_pdf, user_info):
        """
        Pre-fill what we can, then hand control to user.
        Best for: Complex multi-page forms, custom questions.
        """
        # Open the URL in a visible browser window
        agent = Agent(
            task=f"""
            Go to: {job.apply_url}
            Fill in any fields you can identify with:
            - Name: {user_info.full_name}
            - Email: {user_info.email}
            - Phone: {user_info.phone}
            Then STOP. Do not submit.
            """,
            llm=self.llm,
            browser=Browser(config=BrowserConfig(headless=False)),
        )
        await agent.run()
        
        # Notify user to take over
        return ApplicationResult(
            status="assisted",
            method="assisted",
            message="Form pre-filled. Please review and submit manually.",
        )
    
    async def _manual_apply(self, job, cv_pdf, letter_pdf):
        """
        Just open the URL. User does everything.
        Best for: Email applications, complex employer portals.
        """
        import webbrowser
        webbrowser.open(job.apply_url)
        
        return ApplicationResult(
            status="manual",
            method="manual",
            message=f"Opened {job.apply_url}. CV/letter saved to {cv_pdf.parent}",
        )
```

#### Daily Limit Enforcement

```python
class DailyLimitGuard:
    """Enforces the 10 applications/day limit."""
    
    def __init__(self, db: AsyncSession, limit: int = 10):
        self.db = db
        self.limit = limit
    
    async def remaining_today(self) -> int:
        count = await self.db.scalar(
            select(func.count(Application.id)).where(
                Application.applied_at >= date.today(),
                Application.status.in_(["applied", "pending"])
            )
        )
        return max(0, self.limit - (count or 0))
    
    async def can_apply(self) -> bool:
        return (await self.remaining_today()) > 0
```

---

### 5.5 Dashboard UI

**Design Philosophy**: Notion/Linear aesthetic — clean, spacious, monochrome with accent colors. Dark mode default.

#### Pages

| Page | Route | Purpose |
|---|---|---|
| **Morning Queue** | `/` | Today's matched jobs, scored & sorted. Primary daily view. |
| **Job Detail** | `/jobs/:id` | Full job description + tailored CV/letter preview + apply actions |
| **Application Tracker** | `/tracker` | Kanban board: Applied → Heard Back → Interview → Offer → Rejected |
| **CV Manager** | `/cv` | Upload/edit base LaTeX CV, preview PDF, see edit history per application |
| **Settings** | `/settings` | Keywords, filters, sources, lab URLs, API keys, daily limit, profile info |
| **Analytics** | `/analytics` | Apps/week, response rate, best keywords, source effectiveness |

#### Morning Queue (Main Page)

```
┌─────────────────────────────────────────────────────────────┐
│  JobPilot                    Morning Queue    [⚙️] [📊]     │
│                                                              │
│  Tuesday, Feb 28 · 7 remaining today · 23 matches found     │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 🟢 92% match                                            ││
│  │ ML Engineer — DeepMind — London (Remote OK)             ││
│  │ £90-120k · Posted 2d ago · via Adzuna                   ││
│  │                                                          ││
│  │ [Preview CV] [Preview Letter] [Auto Apply] [Skip]       ││
│  └─────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 🟡 78% match                                            ││
│  │ Data Engineer — CERN — Geneva                            ││
│  │ Competitive · Posted 1w ago · via Lab URLs               ││
│  │                                                          ││
│  │ [Preview CV] [Preview Letter] [Open & Apply] [Skip]     ││
│  └─────────────────────────────────────────────────────────┘│
│  ...                                                         │
└─────────────────────────────────────────────────────────────┘
```

#### Tech Details

- **SvelteKit** with `adapter-static` for production build (served by FastAPI)
- **shadcn-svelte** for UI components (buttons, cards, dialogs, kanban)
- **TailwindCSS** with custom theme (dark mode, Notion-style spacing)
- **WebSocket** for real-time updates (scraping progress, apply status)
- **PDF preview** via `pdf.js` embedded viewer

---

### 5.6 Scheduler & Orchestration

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

class MorningBatchScheduler:
    """Orchestrates the daily morning batch job."""
    
    def __init__(self, scraper, matcher, cv_pipeline, db):
        self.scheduler = AsyncIOScheduler()
        self.scraper = scraper
        self.matcher = matcher
        self.cv_pipeline = cv_pipeline
        self.db = db
    
    def start(self, batch_time: str = "08:00"):
        """Schedule morning batch at specified time."""
        hour, minute = batch_time.split(":")
        self.scheduler.add_job(
            self.run_batch,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="morning_batch",
        )
        self.scheduler.start()
    
    async def run_batch(self):
        """
        Full morning batch pipeline:
        1. Scrape all sources
        2. Match & rank
        3. Pre-generate CVs for top matches
        4. Notify dashboard
        """
        settings = await self._load_settings()
        
        # Step 1: Scrape
        await self._emit_status("scraping", "Searching for jobs...")
        raw_jobs = await self.scraper.run_morning_batch(
            keywords=settings.keywords,
            filters=settings.filters,
            sources=settings.enabled_sources,
        )
        
        # Step 2: Match & rank
        await self._emit_status("matching", f"Ranking {len(raw_jobs)} jobs...")
        ranked = self.matcher.rank_and_filter(raw_jobs, settings.filters)
        
        # Step 3: Store new matches
        new_matches = await self._store_new_matches(ranked)
        
        # Step 4: Pre-generate CVs for top N (where N = remaining daily limit)
        remaining = await self.daily_limit.remaining_today()
        top_matches = new_matches[:remaining]
        
        await self._emit_status("tailoring", f"Generating CVs for top {len(top_matches)} matches...")
        for match in top_matches:
            try:
                tailored = await self.cv_pipeline.generate_tailored_cv(
                    base_cv_path=settings.cv_path,
                    job=match.job,
                    output_dir=Path(f"data/cvs/{match.id}"),
                )
                await self._store_tailored_cv(match.id, tailored)
            except Exception as e:
                logger.error(f"CV generation failed for {match.job.title}: {e}")
        
        # Step 5: Notify dashboard
        await self._emit_status("ready", f"{len(top_matches)} applications ready for review")
```

---

## 6. Data Model

```sql
-- User profile and preferences
CREATE TABLE user_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    location TEXT,
    base_cv_path TEXT,          -- Path to base LaTeX CV
    base_letter_path TEXT,      -- Path to base LaTeX motivation letter
    additional_info JSON,       -- Extra fields for form filling
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Job search settings
CREATE TABLE search_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    keywords JSON NOT NULL,          -- ["machine learning", "data engineer"]
    excluded_keywords JSON,          -- ["senior staff", "clearance"]
    locations JSON,                  -- ["Paris", "Remote", "Berlin"]
    salary_min INTEGER,
    experience_min INTEGER,
    experience_max INTEGER,
    remote_only BOOLEAN DEFAULT FALSE,
    job_types JSON,                  -- ["full-time", "contract"]
    languages JSON,                  -- ["English", "French"]
    excluded_companies JSON,         -- ["BadCorp"]
    daily_limit INTEGER DEFAULT 10,
    batch_time TEXT DEFAULT '08:00', -- Morning batch time
    min_match_score REAL DEFAULT 30.0
);

-- Job sources configuration
CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- "linkedin", "adzuna_uk", "lab_mit"
    type TEXT NOT NULL,              -- "api", "browser", "lab_url"
    url TEXT,                        -- Base URL for browser sources
    config JSON,                     -- Source-specific config (API keys, country, etc.)
    prompt_template TEXT,            -- Custom prompt for browser-use agent
    enabled BOOLEAN DEFAULT TRUE,
    last_scraped_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scraped jobs (raw data)
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES job_sources(id),
    external_id TEXT,                -- ID from the source (if available)
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    salary_text TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    description TEXT,
    requirements JSON,               -- List of requirement strings
    benefits JSON,
    url TEXT NOT NULL,                -- Job detail URL
    apply_url TEXT,                   -- Direct apply URL
    apply_method TEXT,                -- "easy_apply", "redirect", "email", "form"
    posted_at TIMESTAMP,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dedup_hash TEXT UNIQUE,           -- MD5(normalized company|title|location)
    raw_data JSON                     -- Full scraped data for debugging
);

-- Matched jobs (scored)
CREATE TABLE job_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id),
    score REAL NOT NULL,              -- 0-100 relevance score
    keyword_hits JSON,                -- Which keywords matched
    status TEXT DEFAULT 'new',        -- new, queued, skipped, applied, expired
    batch_date DATE,                  -- Which morning batch found this
    matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id)
);

-- Tailored documents
CREATE TABLE tailored_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_match_id INTEGER REFERENCES job_matches(id),
    doc_type TEXT NOT NULL,           -- "cv" or "letter"
    tex_path TEXT,
    pdf_path TEXT,
    diff_json JSON,                   -- What the LLM changed
    llm_prompt TEXT,                  -- Prompt used (for debugging)
    llm_response TEXT,                -- Raw LLM response
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Applications
CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_match_id INTEGER REFERENCES job_matches(id),
    method TEXT NOT NULL,             -- "auto", "assisted", "manual"
    status TEXT DEFAULT 'pending',    -- pending, applied, failed, cancelled
    applied_at TIMESTAMP,
    notes TEXT,
    error_log TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Application lifecycle events
CREATE TABLE application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES applications(id),
    event_type TEXT NOT NULL,         -- "submitted", "heard_back", "interview", 
                                     -- "rejected", "offer", "accepted", "note"
    details TEXT,
    event_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Browser sessions (for persistent login)
CREATE TABLE browser_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT UNIQUE NOT NULL,   -- "linkedin", "indeed", etc.
    storage_state_path TEXT,          -- Path to saved browser state JSON
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP
);

-- Indexes
CREATE INDEX idx_jobs_dedup ON jobs(dedup_hash);
CREATE INDEX idx_jobs_scraped ON jobs(scraped_at);
CREATE INDEX idx_matches_status ON job_matches(status);
CREATE INDEX idx_matches_batch ON job_matches(batch_date);
CREATE INDEX idx_applications_date ON applications(applied_at);
CREATE INDEX idx_events_app ON application_events(application_id);
```

---

## 7. Project Structure

```
jobpilot/
│
├── backend/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app, CORS, WebSocket, startup
│   ├── config.py                      # Settings from env/config file
│   ├── database.py                    # SQLAlchemy async engine + session
│   │
│   ├── models/                        # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── user.py                    # UserProfile, SearchSettings
│   │   ├── job.py                     # Job, JobSource, JobMatch
│   │   ├── document.py                # TailoredDocument
│   │   ├── application.py             # Application, ApplicationEvent
│   │   └── session.py                 # BrowserSession
│   │
│   ├── api/                           # FastAPI route handlers
│   │   ├── __init__.py
│   │   ├── jobs.py                    # GET /jobs, GET /jobs/:id, POST /jobs/search
│   │   ├── queue.py                   # GET /queue (morning queue), POST /queue/refresh
│   │   ├── applications.py            # POST /apply, GET /applications, PATCH /applications/:id
│   │   ├── documents.py               # GET /documents/:id/pdf, POST /documents/preview
│   │   ├── settings.py                # GET/PUT /settings, GET/PUT /sources
│   │   ├── analytics.py               # GET /analytics/summary, GET /analytics/trends
│   │   └── ws.py                      # WebSocket handler for live updates
│   │
│   ├── scraping/                      # Job discovery engine
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # ScrapingOrchestrator (coordinates all sources)
│   │   ├── adaptive_scraper.py        # AdaptiveScraper (browser-use + Gemini)
│   │   ├── adzuna_client.py           # AdzunaClient (REST API)
│   │   ├── site_prompts.py            # Per-site prompt templates
│   │   ├── session_manager.py         # BrowserSessionManager (persistent login)
│   │   └── deduplicator.py            # JobDeduplicator
│   │
│   ├── matching/                      # Job scoring & ranking
│   │   ├── __init__.py
│   │   ├── matcher.py                 # JobMatcher (scoring engine)
│   │   └── filters.py                 # JobFilters dataclass
│   │
│   ├── llm/                           # LLM integration
│   │   ├── __init__.py
│   │   ├── gemini_client.py           # Gemini API wrapper (google-generativeai)
│   │   ├── prompts.py                 # All LLM prompt templates
│   │   └── validators.py              # Response schema validation
│   │
│   ├── latex/                         # LaTeX processing pipeline
│   │   ├── __init__.py
│   │   ├── parser.py                  # TexSoup section extraction
│   │   ├── injector.py                # Edit injection into .tex files
│   │   ├── compiler.py                # Tectonic compilation
│   │   ├── validator.py               # chktex syntax validation
│   │   └── pipeline.py                # CVPipeline, LetterPipeline (end-to-end)
│   │
│   ├── applier/                       # Application engine
│   │   ├── __init__.py
│   │   ├── engine.py                  # ApplicationEngine (hybrid router)
│   │   ├── auto_apply.py              # Auto-apply via browser-use agent
│   │   ├── assisted_apply.py          # Pre-fill + hand off to user
│   │   ├── manual_apply.py            # Open URL + clipboard helper
│   │   └── daily_limit.py             # DailyLimitGuard
│   │
│   └── scheduler/                     # Task scheduling
│       ├── __init__.py
│       └── morning_batch.py           # MorningBatchScheduler
│
├── frontend/
│   ├── src/
│   │   ├── routes/
│   │   │   ├── +layout.svelte         # App shell, navigation, dark mode
│   │   │   ├── +page.svelte           # Morning Queue (home)
│   │   │   ├── jobs/
│   │   │   │   └── [id]/+page.svelte  # Job detail + CV preview
│   │   │   ├── tracker/
│   │   │   │   └── +page.svelte       # Application tracker (Kanban)
│   │   │   ├── cv/
│   │   │   │   └── +page.svelte       # CV manager
│   │   │   ├── settings/
│   │   │   │   └── +page.svelte       # Settings & configuration
│   │   │   └── analytics/
│   │   │       └── +page.svelte       # Analytics dashboard
│   │   │
│   │   ├── lib/
│   │   │   ├── components/
│   │   │   │   ├── JobCard.svelte      # Job listing card
│   │   │   │   ├── CVPreview.svelte    # PDF preview + diff view
│   │   │   │   ├── KanbanBoard.svelte  # Drag-and-drop tracker
│   │   │   │   ├── ScoreIndicator.svelte # Match score badge
│   │   │   │   ├── FilterPanel.svelte  # Keyword/filter configuration
│   │   │   │   └── StatusBar.svelte    # Scraping/apply progress
│   │   │   │
│   │   │   ├── stores/
│   │   │   │   ├── jobs.ts             # Job queue store
│   │   │   │   ├── applications.ts     # Application tracker store
│   │   │   │   ├── settings.ts         # Settings store
│   │   │   │   └── websocket.ts        # WebSocket connection store
│   │   │   │
│   │   │   └── api.ts                  # Backend API client (fetch wrapper)
│   │   │
│   │   ├── app.css                     # Tailwind + custom theme
│   │   └── app.html                    # HTML template
│   │
│   ├── static/                         # Static assets
│   ├── svelte.config.js                # SvelteKit config (adapter-static)
│   ├── tailwind.config.js              # Tailwind theme customization
│   ├── vite.config.js
│   └── package.json
│
├── data/                               # Runtime data (gitignored)
│   ├── jobpilot.db                     # SQLite database
│   ├── cvs/                            # Generated CV PDFs (per job)
│   ├── letters/                        # Generated letter PDFs
│   ├── templates/                      # User's base LaTeX files
│   ├── browser_sessions/               # Saved login sessions
│   └── logs/                           # Application logs
│
├── bin/                                # Platform-specific binaries
│   ├── tectonic                        # Linux Tectonic binary
│   └── tectonic.exe                    # Windows Tectonic binary
│
├── scripts/
│   ├── install.sh                      # Linux installer
│   ├── install.ps1                     # Windows installer (PowerShell)
│   └── download_tectonic.py            # Cross-platform Tectonic downloader
│
├── lab_urls.txt                        # User's list of lab career pages
├── pyproject.toml                      # Python project config (uv/pip)
├── start.py                            # Cross-platform launcher
├── Dockerfile                          # Docker alternative
├── docker-compose.yml                  # Docker Compose (optional)
└── README.md
```

---

## 8. Installation & Packaging

### Design Goal

**One command to install, one command to run.** Works identically on Windows and Linux.

### Primary Method: `uv` + Scripts

[`uv`](https://github.com/astral-sh/uv) is a modern Python package manager (by Astral, makers of Ruff) that's 10-100x faster than pip and handles virtual environments automatically.

#### Windows Installation

```powershell
# 1. Install uv (if not installed)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone and install JobPilot
git clone https://github.com/youruser/jobpilot.git
cd jobpilot

# 3. Run the installer (handles everything)
.\scripts\install.ps1
```

**`scripts/install.ps1`** does:
1. `uv sync` — Creates venv, installs all Python dependencies
2. `uv run playwright install chromium` — Downloads Chromium for browser-use
3. `uv run python scripts/download_tectonic.py` — Downloads Tectonic binary for Windows
4. `cd frontend && npm install && npm run build` — Builds SvelteKit frontend
5. Creates a desktop shortcut (optional)

#### Linux Installation

```bash
# 1. Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone and install JobPilot
git clone https://github.com/youruser/jobpilot.git
cd jobpilot

# 3. Run the installer
bash scripts/install.sh
```

**`scripts/install.sh`** does the same as the PowerShell script, adapted for Linux.

#### Running the App

```bash
# Both Windows and Linux:
uv run python start.py

# Or with uv script shortcut (defined in pyproject.toml):
uv run jobpilot
```

**`start.py`** does:
1. Starts FastAPI backend on `localhost:8000`
2. Serves pre-built SvelteKit frontend via FastAPI static files mount
3. Opens default browser to `http://localhost:5173` (dev) or `http://localhost:8000` (prod)
4. Handles graceful shutdown on Ctrl+C

### Alternative: Docker

For users who prefer containerization:

```yaml
# docker-compose.yml
version: '3.8'
services:
  jobpilot:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data        # Persist database and generated files
      - ./lab_urls.txt:/app/lab_urls.txt
    environment:
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
      - ADZUNA_APP_ID=${ADZUNA_APP_ID}
      - ADZUNA_APP_KEY=${ADZUNA_APP_KEY}
```

```bash
docker compose up
# Open http://localhost:8000
```

> **Note**: Docker mode runs browser-use in headless mode only. For manual login to job sites (LinkedIn, etc.), use the native installation.

### Cross-Platform Launcher (`start.py`)

```python
#!/usr/bin/env python3
"""JobPilot launcher — starts backend and opens browser."""

import asyncio
import os
import platform
import subprocess
import sys
import webbrowser
from pathlib import Path

import uvicorn

def check_prerequisites():
    """Verify all dependencies are available."""
    checks = {
        "Database": Path("data/jobpilot.db").parent.exists(),
        "Frontend build": Path("frontend/build").exists(),
        "Tectonic": _find_binary("tectonic"),
    }
    
    for name, ok in checks.items():
        if not ok:
            print(f"⚠  {name} not found. Run the installer first.")
            sys.exit(1)

def _find_binary(name: str) -> bool:
    """Check if a binary is available (PATH or bundled)."""
    import shutil
    if shutil.which(name):
        return True
    ext = ".exe" if platform.system() == "Windows" else ""
    return (Path("bin") / f"{name}{ext}").exists()

def main():
    check_prerequisites()
    
    # Ensure data directories exist
    for d in ["data/cvs", "data/letters", "data/templates", 
              "data/browser_sessions", "data/logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)
    
    host = "127.0.0.1"
    port = 8000
    
    print(f"\n  JobPilot starting on http://{host}:{port}\n")
    
    # Open browser after a short delay
    import threading
    threading.Timer(2.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    
    # Start FastAPI
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )

if __name__ == "__main__":
    main()
```

### `pyproject.toml`

```toml
[project]
name = "jobpilot"
version = "0.1.0"
description = "AI-powered job application assistant"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "browser-use>=0.2",
    "google-generativeai>=0.8",
    "texsoup>=0.3",
    "apscheduler>=3.10",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "pydantic>=2.0",
    "websockets>=12.0",
]

[project.scripts]
jobpilot = "start:main"

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.6",
]
```

---

## 9. Implementation Phases

### Phase 1 — Foundation (Week 1-2)
**Goal**: Search jobs via Adzuna + generate a tailored CV PDF.

| Task | Priority | Effort |
|---|---|---|
| Project scaffolding (backend + frontend + pyproject.toml) | High | 2h |
| SQLite database + SQLAlchemy models | High | 3h |
| FastAPI skeleton (CORS, static mount, basic routes) | High | 2h |
| Adzuna API client (search + parse) | High | 3h |
| LaTeX parser (TexSoup section extraction) | High | 4h |
| Gemini client (google-generativeai wrapper) | High | 2h |
| CV editing pipeline (extract → Gemini → inject) | High | 6h |
| Tectonic integration (compile .tex → .pdf) | High | 2h |
| SvelteKit skeleton + basic job list page | High | 4h |
| PDF preview component | Medium | 3h |
| **Phase 1 deliverable**: Search → see tailored CV → download PDF | | **~31h** |

### Phase 2 — Intelligence (Week 3-4)
**Goal**: Morning batch with matching, ranking, and multiple sources.

| Task | Priority | Effort |
|---|---|---|
| Job matching & scoring engine | High | 4h |
| Job deduplication | High | 2h |
| Morning batch scheduler (APScheduler) | High | 3h |
| Adaptive scraper (browser-use + Gemini) — generic | High | 6h |
| Google Jobs scraping prompt | High | 2h |
| Lab website scraping (from URL file) | High | 3h |
| Browser session manager (persistent login) | High | 4h |
| Morning Queue UI (job cards, score badges, actions) | High | 6h |
| Motivation letter pipeline | Medium | 4h |
| WebSocket live updates | Medium | 3h |
| **Phase 2 deliverable**: Morning queue with matched + tailored jobs | | **~37h** |

### Phase 3 — Automation (Week 5-6)
**Goal**: Hybrid auto-apply working for key sites.

| Task | Priority | Effort |
|---|---|---|
| LinkedIn scraping prompt + session flow | High | 4h |
| LinkedIn Easy Apply agent (browser-use) | High | 8h |
| Indeed scraping prompt | High | 3h |
| Glassdoor scraping prompt | Medium | 3h |
| Generic form filler agent (browser-use) | High | 6h |
| Auto-apply confirmation flow (WebSocket) | High | 4h |
| Assisted apply mode | Medium | 3h |
| Manual apply mode (open URL + clipboard) | Low | 1h |
| Daily limit enforcement | High | 1h |
| Application tracker UI (Kanban board) | High | 6h |
| **Phase 3 deliverable**: Working hybrid auto-apply | | **~39h** |

### Phase 4 — Polish (Week 7-8)
**Goal**: Complete, polished, production-ready app.

| Task | Priority | Effort |
|---|---|---|
| Welcome to the Jungle scraping prompt | Medium | 2h |
| Dice/AngelList scraping prompt | Low | 2h |
| Settings UI (full configuration page) | High | 6h |
| CV Manager UI (upload, preview, history) | Medium | 4h |
| Analytics dashboard | Medium | 6h |
| CV diff viewer (before/after visual) | Medium | 4h |
| Error handling, retry logic, graceful degradation | High | 6h |
| Cross-platform installer scripts | High | 4h |
| Tectonic auto-download script | High | 2h |
| README + first-time setup wizard UI | Medium | 4h |
| Testing (unit + integration) | High | 8h |
| **Phase 4 deliverable**: Ship-ready app | | **~48h** |

### Total Estimated Effort: ~155 hours (~4 weeks full-time)

---

## 10. API & Cost Analysis

### Google Gemini (Free Tier)

| Metric | Free Tier Limit | Our Usage (10 apps/day) |
|---|---|---|
| Requests per minute | 15 RPM | ~2-3 RPM peak |
| Tokens per minute | 1,000,000 | ~50,000 peak |
| Requests per day | 1,500 | ~80-120 |
| Cost | $0 | $0 |

**Breakdown per application cycle**:
- Scraping: ~2-5 LLM calls per site (page understanding)
- CV summary edit: 1 call
- CV experience edit: 1 call  
- Motivation letter edit: 1 call
- Auto-apply form filling: 3-8 calls (multi-step)
- **Total per app**: ~8-16 LLM calls
- **10 apps/day**: ~80-160 calls — well within 1,500/day free limit

### Adzuna API (Free Tier)

| Metric | Free Tier Limit | Our Usage |
|---|---|---|
| Calls per day | 250 | ~10-30 |
| Calls per month | 2,500 | ~300-900 |
| Cost | $0 | $0 |

### Total Monthly Cost: $0

If you ever exceed free tiers, Gemini Flash pay-as-you-go is ~$0.10 per 1M input tokens — for 10 apps/day, that's less than $1/month.

---

## 11. Risk Matrix & Mitigations

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | **LinkedIn account ban** from automation | Medium | High | Never auto-apply initially. Scrape-only with human delays. Easy Apply as opt-in in Phase 3. Use your real browser session (not headless). |
| 2 | **Gemini free tier removed/changed** | Low | High | Abstract LLM client. Can swap to Gemini pay-as-you-go ($0.30/1M tokens) or local Ollama models. |
| 3 | **LaTeX corruption** from LLM edits | Medium | Medium | LLM returns JSON diff (not raw LaTeX). Validate with chktex. Show diff to user. Original file never modified. Fallback: skip editing, use base CV. |
| 4 | **browser-use reliability** on complex sites | Medium | Medium | Fallback to manual mode if agent fails after 3 retries. Log failures for prompt tuning. Adzuna API as reliable backbone. |
| 5 | **Site HTML changes** breaking scraping | High | Low | LLM-driven scraping is inherently adaptive. Prompt tweaks, not code changes. Only site_prompts.py needs updating. |
| 6 | **Tectonic binary issues** on some systems | Low | Medium | Auto-download correct platform binary. Fallback to user's local `pdflatex`/`xelatex` if available. |
| 7 | **Playwright install fails** on restricted systems | Low | High | Clear error message + manual install instructions. Docker as escape hatch. |
| 8 | **CAPTCHA/anti-bot** on job sites | High | Medium | Headful browser (not headless). Random delays. Use authenticated sessions. For CAPTCHAs: pause and alert user to solve manually. |
| 9 | **Scope creep** into full ATS features | Medium | Medium | Strict MVP phases. Track features in backlog. Phase 1-2 are functional without auto-apply. |
| 10 | **Data loss** (SQLite corruption) | Low | High | Daily auto-backup of `jobpilot.db`. Export/import settings feature. |

---

## 12. Legal & Ethical Considerations

### What's Legal (Low Risk)
- ✅ Using Adzuna API (explicitly permitted, free tier with TOS)
- ✅ Scraping your own lab URL list (public pages, no login)
- ✅ Using Google Jobs search results (public data)
- ✅ Generating tailored CVs with LLM (your own content)
- ✅ Opening job application URLs in your browser

### Gray Area (Medium Risk)
- ⚠️ Scraping LinkedIn/Indeed job **listings** while logged in (your own account, for personal use). LinkedIn TOS prohibits automated access, but enforcement is primarily against commercial scrapers.
- ⚠️ Auto-filling application forms (you're the applicant, using your own data). This is analogous to browser autofill.

### What to Avoid (High Risk)
- ❌ Scraping LinkedIn at scale or on behalf of others
- ❌ Creating fake accounts or using others' credentials
- ❌ Submitting applications with fabricated information
- ❌ Circumventing CAPTCHAs programmatically
- ❌ Reselling scraped job data

### Our Approach
1. **Personal use only** — single user, your own accounts
2. **API-first** — use Adzuna (legal API) as primary source
3. **Human-in-the-loop** — all applications require your review/approval
4. **Respectful scraping** — human-like delays, rate limits, no aggressive crawling
5. **Manual login** — never store credentials, use your authenticated browser session
6. **Transparent** — the app generates real applications with your real qualifications

---

## 13. Future Enhancements

### Post-MVP (Backlog)

| Feature | Description | Phase |
|---|---|---|
| **AI Interview Prep** | Given a job you applied to, generate likely interview questions | Future |
| **Email Tracking** | Monitor inbox for application responses (with IMAP) | Future |
| **Multi-profile** | Different CV/letter bases for different job types | Future |
| **Chrome Extension** | "Apply with JobPilot" button on any job page | Future |
| **Resume Parser** | Import existing PDF resume → extract to LaTeX template | Future |
| **Salary Intelligence** | Cross-reference salary data from Glassdoor/Levels.fyi | Future |
| **Application Follow-up** | Auto-generate follow-up emails after N days of silence | Future |
| **A/B Testing** | Track which CV variants get more callbacks | Future |
| **Collaborative** | Share job leads with friends (multi-user mode) | Future |
| **Mobile View** | Responsive UI for checking applications on phone | Future |

---

## Appendix A: Key Dependencies & Links

| Dependency | URL | License |
|---|---|---|
| browser-use | https://github.com/browser-use/browser-use | MIT |
| Google Gemini API | https://ai.google.dev/ | Proprietary (free tier) |
| FastAPI | https://fastapi.tiangolo.com/ | MIT |
| SvelteKit | https://kit.svelte.dev/ | MIT |
| shadcn-svelte | https://www.shadcn-svelte.com/ | MIT |
| TexSoup | https://github.com/alvinwan/TexSoup | BSD-2 |
| Tectonic | https://tectonic-typesetting.github.io/ | MIT |
| Adzuna API | https://developer.adzuna.com/ | Free tier |
| Playwright | https://playwright.dev/python/ | Apache-2.0 |
| uv | https://github.com/astral-sh/uv | MIT/Apache-2.0 |
| SQLAlchemy | https://www.sqlalchemy.org/ | MIT |
| APScheduler | https://github.com/agronholm/apscheduler | MIT |

## Appendix B: Reference Projects (Inspiration)

| Project | Stars | Approach | Relevance |
|---|---|---|---|
| [Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) | ~1.8k | Selenium + AI | LinkedIn automation patterns |
| [LinkedIn-AI-Job-Applier-Ultimate](https://github.com/beatwad/LinkedIn-AI-Job-Applier-Ultimate) | ~500 | browser-use + Gemini/Claude | Closest to our architecture |
| [browser-use/template-library (job-application)](https://github.com/browser-use/template-library/tree/main/job-application) | — | browser-use + ChatGoogle | Official job apply example |
| [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) | ~100 | AI agent multi-board | Multi-site approach |

## Appendix C: Environment Variables

```env
# .env file (never commit this)

# Google Gemini API (get from https://aistudio.google.com/apikey)
GOOGLE_API_KEY=your_gemini_api_key

# Adzuna API (get from https://developer.adzuna.com/)
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key

# Optional: SerpApi for Google Jobs (https://serpapi.com/)
SERPAPI_KEY=

# App settings
JOBPILOT_HOST=127.0.0.1
JOBPILOT_PORT=8000
JOBPILOT_LOG_LEVEL=info
JOBPILOT_DATA_DIR=./data
```
