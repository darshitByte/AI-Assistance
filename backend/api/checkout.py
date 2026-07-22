"""Checkout routes — quote (address + shipping), place (order + invoice), invoice PDF."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from api.deps import current_user, identity
from commerce import address as address_book
from commerce import checkout as checkoutmod
from commerce import guest_checkout
from core.log import logger
from db import users as users_db

router = APIRouter(prefix="/checkout", tags=["checkout"])


class AddressRequest(BaseModel):
    name: str
    email: str
    phone: str
    street: str
    city: str
    postcode: str
    region: str = ""
    country_id: str = "BH"
    label: str = ""


class PlaceRequest(BaseModel):
    payment_method_code: str


@router.get("/addresses")
async def saved_addresses(ident: tuple[str, bool] = Depends(identity)):
    """The customer's saved delivery addresses. Guests have none (they enter one inline)."""
    user, guest = ident
    if guest:
        return []
    return await asyncio.to_thread(address_book.get_addresses, user)


@router.post("/addresses")
async def add_address(req: AddressRequest, user: str = Depends(current_user)):
    logger.info("CHECKOUT add-address user=%s city=%s", user, req.city)
    return await asyncio.to_thread(address_book.add_address, user, req.model_dump())


@router.post("/quote")
async def quote(req: AddressRequest, ident: tuple[str, bool] = Depends(identity)):
    user, guest = ident
    logger.info("CHECKOUT quote user=%s guest=%s city=%s", user, guest, req.city)
    mod = guest_checkout if guest else checkoutmod
    return await asyncio.to_thread(mod.quote, user, req.model_dump())


@router.post("/place")
async def place(req: PlaceRequest, ident: tuple[str, bool] = Depends(identity)):
    user, guest = ident
    logger.info("CHECKOUT place user=%s guest=%s method=%s", user, guest, req.payment_method_code)
    mod = guest_checkout if guest else checkoutmod
    return await asyncio.to_thread(mod.place, user, req.payment_method_code)


@router.get("/invoice/{order_id}")
async def invoice(order_id: int, user: str = Depends(current_user)):
    if not await asyncio.to_thread(users_db.owns_order, user, order_id):
        raise HTTPException(status_code=403, detail="Not your order")
    pdf = await asyncio.to_thread(checkoutmod.invoice_pdf, order_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="invoice-{order_id}.pdf"'},
    )
