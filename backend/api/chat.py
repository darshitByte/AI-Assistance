"""Chat routes — send a message, fetch stored history."""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ai.orchestrator import run_turn
from ai.title import generate_session_name
from api.deps import current_user
from core.log import logger
from db import messages as messages_store
from db import sessions as sessions_store

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class Product(BaseModel):
    sku: str
    name: str | None = None
    price: float | None = None
    image: str | None = None


class ChatResponse(BaseModel):
    reply: str
    products: list[Product] = []
    cart: dict = {}
    cart_added: bool = False  # → UI shows "keep shopping / checkout" buttons


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: str = Depends(current_user)):
    session = req.session_id or user  # fall back to user key if no session sent
    logger.info("CHAT user=%s session=%s msg=%r", user, session, req.message)
    first = not await asyncio.to_thread(messages_store.has_messages, user, session)
    result = await run_turn(user, req.message, session)
    await asyncio.to_thread(messages_store.save_message, user, session, "user", req.message)
    await asyncio.to_thread(messages_store.save_message, user, session, "assistant", result["reply"])
    # Name the chat from its first message; otherwise just bump it to the top.
    if first:
        await asyncio.to_thread(sessions_store.create_session, user, session)
        name = await generate_session_name(req.message)
        await asyncio.to_thread(sessions_store.set_name, user, session, name)
    else:
        await asyncio.to_thread(sessions_store.touch, user, session)
    logger.info(
        "CHAT reply user=%s len=%d products=%d cart_items=%d",
        user, len(result["reply"]), len(result["products"]), result["cart"].get("items_qty", 0),
    )
    return ChatResponse(reply=result["reply"], products=result["products"],
                        cart=result["cart"], cart_added=result["cart_added"])


@router.get("/allmessage")
async def all_messages(user: str = Depends(current_user), session_id: str | None = None):
    session = session_id or user
    msgs = await asyncio.to_thread(messages_store.get_messages, user, session)
    logger.info("ALLMESSAGE user=%s session=%s count=%d", user, session, len(msgs))
    return {"messages": msgs}
