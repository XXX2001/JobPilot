from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db

# Convenience type alias for route handlers — the only public symbol in this module.
DBSession = Annotated[AsyncSession, Depends(get_db)]


# NOTE: The earlier ``get_session_manager`` / ``get_apply_engine`` /
# ``get_cv_pipeline`` / ``get_scraping_orchestrator`` / ``get_batch_runner``
# helpers were removed in the 2026-05-24 dead-code purge (T9). None of the
# routers ever called them — singletons are read directly off
# ``request.app.state`` inside each endpoint. Re-add them if a future router
# adopts Depends-based access to the same singletons.
