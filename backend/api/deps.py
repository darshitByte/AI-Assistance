"""Shared API dependencies."""
from fastapi import Header, HTTPException

from core import security


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user = security.decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
