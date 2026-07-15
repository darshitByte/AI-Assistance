"""Auth routes — signup / login."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import security
from core.log import logger
from db import users

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str


@router.post("/signup", response_model=AuthResponse)
async def signup(req: AuthRequest):
    logger.info("SIGNUP attempt: username=%s", req.username)
    if not users.create_user(req.username, req.password):
        logger.warning("SIGNUP failed (username taken): %s", req.username)
        raise HTTPException(status_code=409, detail="Username already taken")
    name = req.username.strip()
    logger.info("SIGNUP ok: %s", name)
    return AuthResponse(token=security.make_token(name), username=name)


@router.post("/login", response_model=AuthResponse)
async def login(req: AuthRequest):
    logger.info("LOGIN attempt: username=%s", req.username)
    if not users.verify(req.username, req.password):
        logger.warning("LOGIN failed: %s", req.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    name = req.username.strip()
    logger.info("LOGIN ok: %s", name)
    return AuthResponse(token=security.make_token(name), username=name)
