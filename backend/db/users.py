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


def get_address(username: str) -> dict | None:
    """The *selected* address that checkout.place() bills against (set by quote)."""
    u = users.find_one({"username": username.strip()}, {"address": 1})
    return u.get("address") if u else None


def set_address(username: str, address: dict) -> None:
    users.update_one(
        {"username": username.strip()},
        {"$set": {"address": address, "updated_at": _now()}},
    )


def _addresses_from_doc(u: dict | None) -> list[dict]:
    """Address list from a user doc, falling back to the legacy single `address`."""
    if not u:
        return []
    return u.get("addresses") or ([u["address"]] if u.get("address") else [])


def get_addresses(username: str) -> list[dict]:
    """Saved delivery addresses for the address picker. Falls back to the legacy
    single `address` (from the retired checkout drawer) so old users aren't empty."""
    u = users.find_one({"username": username.strip()}, {"addresses": 1, "address": 1})
    return _addresses_from_doc(u)


def add_address(username: str, address: dict) -> list[dict]:
    """Append an address to the saved list; return the updated list."""
    users.update_one(
        {"username": username.strip()},
        {"$push": {"addresses": address}, "$set": {"updated_at": _now()}},
    )
    return get_addresses(username)


def add_order(username: str, order_id: int) -> None:
    users.update_one(
        {"username": username.strip()},
        {"$addToSet": {"orders": order_id}, "$set": {"updated_at": _now()}},
    )


def owns_order(username: str, order_id: int) -> bool:
    """Guard for the invoice PDF endpoint — a user may only fetch their own orders."""
    u = users.find_one({"username": username.strip()}, {"orders": 1})
    return bool(u and order_id in (u.get("orders") or []))


def seed_admin() -> None:
    """Ensure the default admin user exists."""
    try:
        users.create_index("username", unique=True)
        create_user("admin", "admin@123")
    except Exception as e:  # noqa: BLE001 — startup best-effort
        logger.warning("seed_admin skipped: %s", e)


if __name__ == "__main__":  # network-free self-check for the address-list fallback
    assert _addresses_from_doc(None) == []
    assert _addresses_from_doc({}) == []
    assert _addresses_from_doc({"address": {"city": "Manama"}}) == [{"city": "Manama"}]
    assert _addresses_from_doc({"addresses": [{"label": "Home"}]}) == [{"label": "Home"}]
    # explicit list wins over the legacy single address
    assert _addresses_from_doc({"addresses": [{"label": "Home"}], "address": {"city": "X"}}) == [{"label": "Home"}]
    print("users self-check ok")
