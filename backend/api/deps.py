from __future__ import annotations
from typing import Annotated, TYPE_CHECKING

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db

# Convenience type alias for route handlers
DBSession = Annotated[AsyncSession, Depends(get_db)]


# ── Singleton dependency getters ──────────────────────────────────────────────
# These pull module-level singletons from ``app.state`` (set in main.py lifespan).

if TYPE_CHECKING:  # pragma: no cover
    from backend.scraping.session_manager import BrowserSessionManager
    from backend.applier.engine import ApplicationEngine
    from backend.latex.pipeline import CVPipeline, LetterPipeline
    from backend.scraping.orchestrator import ScrapingOrchestrator
    from backend.scheduler.morning_batch import MorningBatchScheduler


def get_session_manager(request: Request) -> "BrowserSessionManager":
    return request.app.state.session_manager


def get_apply_engine(request: Request) -> "ApplicationEngine":
    return request.app.state.apply_engine


def get_cv_pipeline(request: Request) -> "CVPipeline":
    return request.app.state.cv_pipeline


def get_scraping_orchestrator(request: Request) -> "ScrapingOrchestrator":
    return request.app.state.scraping_orchestrator


def get_morning_scheduler(request: Request) -> "MorningBatchScheduler":
    return request.app.state.morning_scheduler
