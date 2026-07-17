"""Shared API dependencies."""
from fastapi import Header, HTTPException

from core import security


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user = security.decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def identity(authorization: str = Header(default=""),
             x_guest_id: str = Header(default="")) -> tuple[str, bool]:
    """(key, is_guest): a valid JWT → (email, False); else an X-Guest-Id header →
    (guest_id, True); else 401. Used by chat + cart so a guest can browse without login."""
    token = authorization.removeprefix("Bearer ").strip()
    if token:
        user = security.decode_token(token)
        if user:
            return user, False
    gid = x_guest_id.strip()
    if gid:
        return gid, True
    raise HTTPException(status_code=401, detail="Not authenticated")
