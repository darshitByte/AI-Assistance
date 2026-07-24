"""Admin endpoint to (re)build the Qdrant product index from Magento."""
from fastapi import APIRouter

from vector import service

router = APIRouter(tags=["vector"])


@router.post("/reindex")
async def reindex() -> dict:
    """Pull the full catalogue, embed it, and upsert into Qdrant."""
    # ponytail: auth removed for now (testing) — re-add Depends(current_user) before shipping.
    n = await service.reindex_all()
    return {"indexed": n}
