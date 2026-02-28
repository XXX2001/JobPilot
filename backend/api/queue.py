from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/")
async def get_queue():
    return []
