from fastapi import APIRouter  # type: ignore

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("/")
async def list_documents():
    return []
