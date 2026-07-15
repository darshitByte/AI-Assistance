"""Session routes — list the user's chats, register a new one."""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import current_user
from core.log import logger
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
