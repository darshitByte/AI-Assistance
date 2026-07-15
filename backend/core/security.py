"""JWT helpers — stateless auth tokens."""
import time

import jwt

from core import config


def make_token(username: str) -> str:
    return jwt.encode(
        {"sub": username, "iat": int(time.time())}, config.JWT_SECRET, algorithm="HS256"
    )


def decode_token(token: str) -> str | None:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"]).get("sub")
    except Exception:  # noqa: BLE001 — any decode failure = not authenticated
        return None
