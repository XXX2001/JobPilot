from __future__ import annotations
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db

# Convenience type alias for route handlers
DBSession = Annotated[AsyncSession, Depends(get_db)]
