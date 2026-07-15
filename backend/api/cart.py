"""Cart routes — view / add / remove."""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import current_user
from commerce import cart as cartmod
from core.log import logger

router = APIRouter(prefix="/cart", tags=["cart"])


class CartAddRequest(BaseModel):
    sku: str
    qty: int = 1


class CartRemoveRequest(BaseModel):
    item_id: int


@router.get("")
async def get_cart(user: str = Depends(current_user)):
    return await asyncio.to_thread(cartmod.view, user)


@router.post("/add")
async def cart_add(req: CartAddRequest, user: str = Depends(current_user)):
    logger.info("CART add user=%s sku=%s qty=%d", user, req.sku, req.qty)
    return await asyncio.to_thread(cartmod.add_item, user, req.sku, req.qty)


@router.post("/remove")
async def cart_remove(req: CartRemoveRequest, user: str = Depends(current_user)):
    logger.info("CART remove user=%s item_id=%s", user, req.item_id)
    return await asyncio.to_thread(cartmod.remove_item, user, req.item_id)
