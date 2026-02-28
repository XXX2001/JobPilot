from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("/")
async def list_applications():
    return []
