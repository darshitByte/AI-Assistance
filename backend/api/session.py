"""Session routes — list the user's chats, register a new one."""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import current_user
from core.log import logger
from db import messages as messages_store
from db import sessions as sessions_store

router = APIRouter(tags=["session"])


class SessionCreate(BaseModel):
    session_id: str


@router.get("/sessions")
async def get_sessions(user: str = Depends(current_user)):
    rows = await asyncio.to_thread(sessions_store.list_sessions, user)
    return {"sessions": rows}


@router.post("/sessions")
async def create_session(req: SessionCreate, user: str = Depends(current_user)):
    logger.info("SESSION new user=%s session=%s", user, req.session_id)
    await asyncio.to_thread(sessions_store.create_session, user, req.session_id)
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: str = Depends(current_user)):
    """Delete a chat and its messages. In-memory agent history for this session_id
    is left as-is — the id is never reused, so it's just dead weight until restart."""
    logger.info("SESSION delete user=%s session=%s", user, session_id)
    await asyncio.to_thread(sessions_store.delete_session, user, session_id)
    await asyncio.to_thread(messages_store.delete_messages, user, session_id)
    return {"ok": True}
