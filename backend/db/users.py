"""User storage. Identity + auth live in Magento (commerce/customer.py), and so
do addresses (commerce/address.py); this module only keeps the app-side order
list + chat key. No password hash, no addresses."""
from datetime import datetime, timezone

from commerce import customer
from core.log import logger
from db.mongo import users


def _now():
    return datetime.now(timezone.utc)


def create_user(email: str, password: str) -> None:
    """Create the Magento customer + an app-side Mongo doc. Raises customer.CustomerError
    (duplicate email / password policy) — the caller maps it to an HTTP status."""
    email = email.strip()
    if not email or not password:
        raise customer.CustomerError("Email and password are required.")
    customer.create(email, password)  # raises on failure (duplicate / policy)
    now = _now()
    users.update_one(
        {"username": email},
        {"$setOnInsert": {"username": email, "created_at": now}, "$set": {"updated_at": now}},
        upsert=True,
    )


def verify(email: str, password: str) -> bool:
    """Authenticate against Magento; caches the pw+token for later cart/checkout re-mint."""
    try:
        customer.authenticate(email.strip(), password)
        return True
    except customer.CustomerError:
        return False


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
    """Ensure the demo Magento customer exists and its password is cached (so checkout
    works right after a restart, before anyone logs in)."""
    email, pw = "demo@grocerzy-poc.com", "Demo@123"
    try:
        users.create_index("username", unique=True)
        try:
            create_user(email, pw)
        except customer.CustomerError:
            pass  # already exists → fine
        customer.remember_password(email, pw)  # ensure re-mint works even if create was skipped
        users.update_one(
            {"username": email},
            {"$setOnInsert": {"username": email, "created_at": _now()}, "$set": {"updated_at": _now()}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001 — startup best-effort
        logger.warning("seed_admin skipped: %s", e)
