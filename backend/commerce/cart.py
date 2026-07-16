"""Cart operations — one Magento customer cart (`/carts/mine`) per app user.

This store disallows guest checkout, so the cart lives on the app user's own
Magento customer (see customer.py) rather than an anonymous guest cart. Every
call carries that customer's bearer token; on a 401 (expired token) we re-mint
once and retry. Cloudflare requires a browser User-Agent.
"""
import json
import urllib.error
import urllib.request

from commerce import customer
from commerce.magento import fetch_images_by_sku
from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_UA = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}
_FALLBACK_CCY = "BD"


def _call(username: str, method: str, path: str, body: dict | None = None, timeout: int = 30):
    """Customer-authenticated Magento call; re-mint the token once on 401 and retry."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in (1, 2):
        headers = dict(_UA)
        headers["Authorization"] = f"Bearer {customer.get_token(username, force=(attempt == 2))}"
        req = urllib.request.Request(_API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                continue
            raise


def _err(e: urllib.error.HTTPError) -> str:
    try:
        return json.loads(e.read().decode()).get("message", "Sorry, that couldn't be added.")
    except Exception:
        return "Sorry, that couldn't be added."


def empty() -> dict:
    return {"items": [], "items_qty": 0, "grand_total": 0, "currency": _FALLBACK_CCY}


def _cart_id(username: str) -> int:
    """Ensure the customer's active cart exists; return its quote id (idempotent)."""
    return _call(username, "POST", "/carts/mine")


def add_item(username: str, sku: str, qty: int = 1) -> dict:
    cid = _cart_id(username)
    body = {"cartItem": {"sku": sku, "qty": qty, "quote_id": cid}}
    try:
        _call(username, "POST", "/carts/mine/items", body)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e), "cart": view(username)}
    return {"ok": True, "cart": view(username)}


def remove_item(username: str, item_id: int) -> dict:
    try:
        _call(username, "DELETE", f"/carts/mine/items/{item_id}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e), "cart": view(username)}
    return {"ok": True, "cart": view(username)}


def view(username: str) -> dict:
    try:
        cart = _call(username, "GET", "/carts/mine")
        totals = _call(username, "GET", "/carts/mine/totals")
    except urllib.error.HTTPError:
        return empty()
    items = cart.get("items", [])
    images = fetch_images_by_sku([it["sku"] for it in items]) if items else {}
    return {
        "items": [
            {
                "item_id": it.get("item_id"),
                "sku": it.get("sku"),
                "name": it.get("name"),
                "qty": it.get("qty"),
                "price": it.get("price"),
                "image": images.get(it.get("sku")),
            }
            for it in items
        ],
        "items_qty": totals.get("items_qty", 0),
        "grand_total": totals.get("grand_total", 0),
        "currency": totals.get("quote_currency_code") or _FALLBACK_CCY,
    }
