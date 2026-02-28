from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/")
async def get_settings():
    return {}
