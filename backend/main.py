import json
import logging
import platform
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from fastapi.staticfiles import StaticFiles  # type: ignore
from starlette.responses import FileResponse  # type: ignore
from starlette.staticfiles import NotModifiedResponse  # type: ignore

from backend.config import DATA_DIR, PROJECT_ROOT, settings

logger = logging.getLogger("jobpilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting JobPilot application")

    # Ensure data subdirectories exist
    data_dirs = [
        DATA_DIR / "cvs",
        DATA_DIR / "letters",
        DATA_DIR / "templates",
        DATA_DIR / "browser_sessions",
        DATA_DIR / "browser_profiles",
        DATA_DIR / "logs",
    ]
    for d in data_dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Initialize DB (create tables if not exists via SQLAlchemy)
    try:
        from backend.database import init_db

        await init_db()
        logger.info("Database initialized")
    except Exception:
        logger.exception("DB init failed (may already be up)")

    # ── Instantiate singletons ────────────────────────────────────────────
    try:
        from backend.applier.engine import ApplicationEngine
        from backend.latex.applicator import CVApplicator
        from backend.latex.pipeline import CVPipeline, LetterPipeline
        from backend.llm.cv_editor import CVEditor
        from backend.llm.cv_modifier import CVModifier
        from backend.llm.gemini_client import GeminiClient
        from backend.llm.job_analyzer import JobAnalyzer
        from backend.matching.matcher import JobMatcher
        from backend.scheduler.morning_batch import MorningBatchRunner
        from backend.scraping.adaptive_scraper import AdaptiveScraper
        from backend.scraping.adzuna_client import AdzunaClient
        from backend.scraping.deduplicator import JobDeduplicator
        from backend.scraping.orchestrator import ScrapingOrchestrator
        from backend.scraping.scrapling_fetcher import ScraplingFetcher
        from backend.scraping.session_manager import BrowserSessionManager

        gemini = GeminiClient()
        cv_editor = CVEditor(client=gemini)
        cv_pipeline = CVPipeline(
            job_analyzer=JobAnalyzer(),
            cv_modifier=CVModifier(),
            cv_applicator=CVApplicator(),
        )
        letter_pipeline = LetterPipeline(cv_editor=cv_editor)
        adzuna = AdzunaClient()
        dedup = JobDeduplicator()
        adaptive = AdaptiveScraper(gemini_api_key=settings.GOOGLE_API_KEY)
        session_mgr = BrowserSessionManager()
        scrapling = ScraplingFetcher(gemini_client=gemini) if settings.SCRAPLING_ENABLED else None
        orchestrator = ScrapingOrchestrator(
            adzuna_client=adzuna,
            adaptive_scraper=adaptive,
            session_mgr=session_mgr,
            deduplicator=dedup,
            scrapling_fetcher=scrapling,
        )
        matcher = JobMatcher()
        from backend.defaults import DAILY_LIMIT

        apply_engine = ApplicationEngine(
            api_key=settings.GOOGLE_API_KEY,
            daily_limit=DAILY_LIMIT,
        )

        # DB factory for the batch runner (creates a new session each call)
        from backend.database import AsyncSessionLocal

        batch_runner = MorningBatchRunner(
            scraper=orchestrator,
            matcher=matcher,
            cv_pipeline=cv_pipeline,
            db_factory=AsyncSessionLocal,
        )

        # Store on app.state for dependency injection
        app.state.gemini = gemini
        app.state.cv_pipeline = cv_pipeline
        app.state.letter_pipeline = letter_pipeline
        app.state.adzuna = adzuna
        app.state.adaptive_scraper = adaptive
        app.state.session_manager = session_mgr
        app.state.scraping_orchestrator = orchestrator
        app.state.matcher = matcher
        app.state.apply_engine = apply_engine
        app.state.batch_runner = batch_runner

        logger.info("All singletons initialised")
    except Exception as exc:
        logger.warning("Singleton init failed (non-fatal in test env): %s", exc)

    # ── Wire WS client message routing ──────────────────────────────────
    try:
        from backend.api import ws as ws_module

        def _handle_login_done(msg: dict) -> None:
            site = msg.get("site", "")
            sm = getattr(app.state, "session_manager", None)
            if sm:
                sm.confirm_login(site)

        def _handle_login_cancel(msg: dict) -> None:
            site = msg.get("site", "")
            sm = getattr(app.state, "session_manager", None)
            if sm:
                sm.cancel_login(site)

        def _handle_confirm_submit(msg: dict) -> None:
            job_id = msg.get("job_id", -1)
            engine = getattr(app.state, "apply_engine", None)
            if engine:
                engine.signal_confirm(job_id)

        def _handle_cancel_apply(msg: dict) -> None:
            job_id = msg.get("job_id", -1)
            engine = getattr(app.state, "apply_engine", None)
            if engine:
                engine.signal_cancel(job_id)

        ws_module.manager.register_handler("login_done", _handle_login_done)
        ws_module.manager.register_handler("login_cancel", _handle_login_cancel)
        ws_module.manager.register_handler("confirm_submit", _handle_confirm_submit)
        ws_module.manager.register_handler("cancel_apply", _handle_cancel_apply)

    except Exception as exc:
        logger.warning("WS handler registration failed (non-fatal): %s", exc)

    yield

    # Shutdown
    logger.info("Shutting down JobPilot application")
    # No scheduler to shut down — batch runs are on-demand only


app: Any = FastAPI(lifespan=lifespan, redirect_slashes=False)  # type: ignore[arg-type]

# Allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers (stubs implemented in backend/api/*.py)
try:
    import backend.api.analytics as analytics  # type: ignore
    import backend.api.applications as applications  # type: ignore
    import backend.api.documents as documents  # type: ignore
    import backend.api.jobs as jobs  # type: ignore
    import backend.api.queue as queue  # type: ignore
    import backend.api.settings as api_settings  # type: ignore
    import backend.api.ws as ws  # type: ignore

    app.include_router(jobs.router)
    app.include_router(queue.router)
    app.include_router(applications.router)
    app.include_router(documents.router)
    app.include_router(api_settings.router)
    app.include_router(analytics.router)
    # ws.py is present but may not register routes yet
    app.include_router(ws.router)
except Exception as e:
    # If api package or modules don't exist yet, continue — stubs will be added
    logger.debug("API routers not all available yet: %s", e)


@app.get("/api/health")
async def health():
    tectonic_name = "tectonic.exe" if platform.system() == "Windows" else "tectonic"
    tectonic_bin = PROJECT_ROOT / "bin" / tectonic_name
    tectonic = tectonic_bin.exists() or shutil.which("tectonic") is not None
    gemini_key_set = getattr(settings, "GOOGLE_API_KEY", "") not in (None, "", "placeholder")
    result: dict[str, Any] = {
        "status": "ok",
        "version": "0.1.0",
        "db": "connected",
        "tectonic": bool(tectonic),
        "gemini_key_set": bool(gemini_key_set),
    }
    if not tectonic:
        result["tectonic_hint"] = (
            "Tectonic not found. Run: uv run python scripts/download_tectonic.py"
        )
    return result


# ── Global exception handlers ────────────────────────────────────────────────
try:
    from backend.latex.compiler import LaTeXCompilationError as _LaTeXErr
except ImportError:
    _LaTeXErr = None  # type: ignore[assignment,misc]

try:
    from backend.llm.gemini_client import GeminiJSONError as _GeminiJSONErr
    from backend.llm.gemini_client import GeminiRateLimitError as _GeminiRateErr
except ImportError:
    _GeminiJSONErr = None  # type: ignore[assignment,misc]
    _GeminiRateErr = None  # type: ignore[assignment,misc]


if _LaTeXErr is not None:

    @app.exception_handler(_LaTeXErr)
    async def _latex_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("LaTeX compilation error: %s", exc)
        return JSONResponse(
            status_code=422,
            content={"error": str(exc), "code": "latex_compile_error"},
        )


if _GeminiJSONErr is not None:

    @app.exception_handler(_GeminiJSONErr)
    async def _gemini_json_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Gemini JSON error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "LLM response validation failed", "code": "gemini_json_error"},
        )


if _GeminiRateErr is not None:

    @app.exception_handler(_GeminiRateErr)
    async def _gemini_rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Gemini rate limit: %s", exc)
        return JSONResponse(
            status_code=429,
            content={
                "error": "LLM rate limit reached — please try again shortly",
                "code": "rate_limit",
            },
        )


@app.exception_handler(Exception)
async def _generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "internal_error"},
    )


# SPA fallback — serve index.html for any path not handled by the API
class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except Exception:
            # Fall back to index.html for client-side routing
            response = await super().get_response("index.html", scope)

        # Immutable assets (hashed filenames) can be cached forever.
        # Everything else (index.html, fallback) must revalidate so the
        # browser always picks up new builds.
        if "/_app/immutable/" in path:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        elif not isinstance(response, NotModifiedResponse):
            response.headers["cache-control"] = "no-cache"

        return response


# Serve frontend static files if built (don't crash if missing)
try:
    static_dir = PROJECT_ROOT / "frontend" / "build"
    if static_dir.exists():
        app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="static")
    else:
        logger.warning(
            "Frontend build not found at %s — run 'npm run build' in frontend/", static_dir
        )
except Exception as e:
    logger.warning("Could not mount static files: %s", e)
