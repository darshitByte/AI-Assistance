"""Cart routes — view / add / remove (guest or customer) + merge on checkout login."""
import asyncio

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from api.deps import current_user, identity
from commerce import cart as cartmod
from commerce import guest_cart
from core.log import logger

router = APIRouter(prefix="/cart", tags=["cart"])


class CartAddRequest(BaseModel):
    sku: str
    qty: int = 1


class CartRemoveRequest(BaseModel):
    item_id: int | str  # customer cart: Magento item id (int); guest cart: sku (str)


def _mod(guest: bool):
    return guest_cart if guest else cartmod


@router.get("")
async def get_cart(ident: tuple[str, bool] = Depends(identity)):
    user, guest = ident
    return await asyncio.to_thread(_mod(guest).view, user)


@router.post("/add")
async def cart_add(req: CartAddRequest, ident: tuple[str, bool] = Depends(identity)):
    user, guest = ident
    logger.info("CART add user=%s guest=%s sku=%s qty=%d", user, guest, req.sku, req.qty)
    return await asyncio.to_thread(_mod(guest).add_item, user, req.sku, req.qty)


@router.post("/remove")
async def cart_remove(req: CartRemoveRequest, ident: tuple[str, bool] = Depends(identity)):
    user, guest = ident
    logger.info("CART remove user=%s guest=%s item_id=%s", user, guest, req.item_id)
    return await asyncio.to_thread(_mod(guest).remove_item, user, req.item_id)


@router.post("/merge")
async def cart_merge(user: str = Depends(current_user), x_guest_id: str = Header(default="")):
    """After the checkout login: push the guest cart into the customer's /carts/mine,
    then drop the guest cart. Best-effort — a sku that fails to add is reported, not fatal."""
    gid = x_guest_id.strip()
    if not gid:
        return {"ok": True, "cart": await asyncio.to_thread(cartmod.view, user), "failed": []}
    failed = []
    for sku, qty in guest_cart.items(gid):
        res = await asyncio.to_thread(cartmod.add_item, user, sku, qty)
        if not res.get("ok"):
            failed.append(sku)
    guest_cart.clear(gid)
    logger.info("CART merge user=%s guest=%s failed=%s", user, gid, failed)
    return {"ok": True, "cart": await asyncio.to_thread(cartmod.view, user), "failed": failed}
