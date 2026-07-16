"""Checkout routes — quote (address + shipping), place (order + invoice), invoice PDF."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from api.deps import current_user
from commerce import checkout as checkoutmod
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
async def saved_addresses(user: str = Depends(current_user)):
    """The customer's saved delivery addresses for the in-chat address picker."""
    return await asyncio.to_thread(users_db.get_addresses, user)


@router.post("/addresses")
async def add_address(req: AddressRequest, user: str = Depends(current_user)):
    logger.info("CHECKOUT add-address user=%s label=%s", user, req.label)
    return await asyncio.to_thread(users_db.add_address, user, req.model_dump())


@router.post("/quote")
async def quote(req: AddressRequest, user: str = Depends(current_user)):
    logger.info("CHECKOUT quote user=%s city=%s", user, req.city)
    return await asyncio.to_thread(checkoutmod.quote, user, req.model_dump())


@router.post("/place")
async def place(req: PlaceRequest, user: str = Depends(current_user)):
    logger.info("CHECKOUT place user=%s method=%s", user, req.payment_method_code)
    return await asyncio.to_thread(checkoutmod.place, user, req.payment_method_code)


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
