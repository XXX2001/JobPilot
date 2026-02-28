from fastapi import FastAPI  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.staticfiles import StaticFiles  # type: ignore
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import shutil
import logging

from backend.config import settings


logger = logging.getLogger("jobpilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting JobPilot application")

    # Ensure data subdirectories exist
    data_dirs = [
        Path(settings.jobpilot_data_dir) / "cvs",
        Path(settings.jobpilot_data_dir) / "letters",
        Path(settings.jobpilot_data_dir) / "templates",
        Path(settings.jobpilot_data_dir) / "browser_sessions",
        Path(settings.jobpilot_data_dir) / "logs",
    ]
    for d in data_dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Initialize DB (create tables if not exists via SQLAlchemy)
    try:
        from backend.database import init_db
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.warning("DB init failed (may already be up): %s", e)
    yield

    # Shutdown
    logger.info("Shutting down JobPilot application")


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
    import backend.api.jobs as jobs  # type: ignore
    import backend.api.queue as queue  # type: ignore
    import backend.api.applications as applications  # type: ignore
    import backend.api.documents as documents  # type: ignore
    import backend.api.settings as api_settings  # type: ignore
    import backend.api.analytics as analytics  # type: ignore
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
    tectonic = Path("bin/tectonic").exists() or shutil.which("tectonic") is not None
    gemini_key_set = getattr(settings, "GOOGLE_API_KEY", "") not in (None, "", "placeholder")
    return {
        "status": "ok",
        "version": "0.1.0",
        "db": "connected",
        "tectonic": bool(tectonic),
        "gemini_key_set": bool(gemini_key_set),
    }


# Serve frontend static files if built (don't crash if missing)
try:
    static_dir = Path("frontend/build")
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    else:
        # attempt mount anyway, but guard in case missing
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
except Exception as e:
    logger.warning("Could not mount static files: %s", e)
