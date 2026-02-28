from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/")
async def get_analytics():
    return {}
