"""User storage + password hashing (PBKDF2). Backed by the `users` collection."""
import hashlib
import hmac
import os
from datetime import datetime, timezone

from core.log import logger
from db.mongo import users


def _now():
    return datetime.now(timezone.utc)


def _hash(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
    return salt, dk


def create_user(username: str, password: str) -> bool:
    """Create a user; returns False if the username is taken or input is empty."""
    username = username.strip()
    if not username or not password:
        return False
    if users.find_one({"username": username}):
        return False
    salt, dk = _hash(password)
    now = _now()
    users.insert_one(
        {"username": username, "salt": salt, "hash": dk, "created_at": now, "updated_at": now}
    )
    return True


def verify(username: str, password: str) -> bool:
    u = users.find_one({"username": username.strip()})
    if not u:
        return False
    _, dk = _hash(password, u["salt"])
    ok = hmac.compare_digest(dk, u["hash"])
    if ok:
        users.update_one({"_id": u["_id"]}, {"$set": {"updated_at": _now()}})
    return ok


def seed_admin() -> None:
    """Ensure the default admin user exists."""
    try:
        users.create_index("username", unique=True)
        create_user("admin", "admin@123")
    except Exception as e:  # noqa: BLE001 — startup best-effort
        logger.warning("seed_admin skipped: %s", e)
