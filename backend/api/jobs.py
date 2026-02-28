from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/")
async def list_jobs():
    return []
