"""Health / status route."""
import asyncio

from fastapi import APIRouter

from ai.runtime import runtime
from commerce.magento import total_product_count
from core import config

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    count = await asyncio.to_thread(total_product_count)
    return {
        "status": "ok",
        "model": config.LLM_MODEL,
        "product_count": count,
        "tools": runtime.mcp.tool_names() if runtime.mcp else [],
    }
