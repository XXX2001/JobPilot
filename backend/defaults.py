# ── LLM / Gemini ─────────────────────────────────────────────────────────────
GEMINI_FALLBACK_MODEL: str = "gemini-2.0-flash"

# ── Scraping ──────────────────────────────────────────────────────────────────
MAX_SCRAPLING_CONTENT_CHARS: int = 50_000

# ── Field length limits ──────────────────────────────────────────────────────
MAX_LEN_TITLE: int = 200
MAX_LEN_COMPANY: int = 200
MAX_LEN_LOCATION: int = 200
MAX_LEN_DESCRIPTION: int = 20_000
MAX_LEN_SALARY_TEXT: int = 200
MAX_LEN_APPLY_URL: int = 2_048
MAX_LEN_EMAIL: int = 254
MAX_LEN_PHONE: int = 30
MAX_LEN_ADDITIONAL_ANSWERS: int = 10_000

# ── Scheduler / Batch ───────────────────────────────────────────────────────
CONCURRENCY_GEMINI: int = 3       # max concurrent Gemini calls in morning batch
DAILY_LIMIT: int = 10             # fallback daily application limit
MIN_MATCH_SCORE: float = 30.0     # fallback minimum match score threshold

# ── ATS Gap Severity Engine ─────────────────────────────────────────────────
GAP_SEVERITY_THRESHOLD_CONSERVATIVE: float = 0.3
GAP_SEVERITY_THRESHOLD_BALANCED: float = 0.5
GAP_SEVERITY_THRESHOLD_AGGRESSIVE: float = 0.7
EMBEDDING_MODEL: str = "text-embedding-004"
SIMILARITY_FULL_MATCH: float = 0.82
SIMILARITY_PARTIAL_MATCH: float = 0.60
MIN_JOB_SKILLS_FOR_FIT_ENGINE: int = 2
