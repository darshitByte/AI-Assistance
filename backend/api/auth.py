"""Auth routes — signup / login. Identity is a Magento customer (see commerce/customer.py)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from commerce import customer
from core import security
from core.log import logger
from db import users

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    email: str      # the app identity value; also the Magento customer email
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str   # = the email; kept as `username` so the frontend/cart/chat keys are unchanged


@router.post("/signup", response_model=AuthResponse)
async def signup(req: AuthRequest):
    email = req.email.strip()
    logger.info("SIGNUP attempt: %s", email)
    try:
        users.create_user(email, req.password)
    except customer.CustomerError as e:
        msg = str(e)
        code = 409 if "already exists" in msg.lower() else 400
        logger.warning("SIGNUP failed (%s): %s", code, email)
        raise HTTPException(status_code=code, detail=msg)
    logger.info("SIGNUP ok: %s", email)
    return AuthResponse(token=security.make_token(email), username=email)


@router.post("/login", response_model=AuthResponse)
async def login(req: AuthRequest):
    email = req.email.strip()
    logger.info("LOGIN attempt: %s", email)
    if not users.verify(email, req.password):
        logger.warning("LOGIN failed: %s", email)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    logger.info("LOGIN ok: %s", email)
    return AuthResponse(token=security.make_token(email), username=email)
