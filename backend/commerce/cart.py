"""Guest-cart operations — one Magento guest cart per user.

Guest carts are anonymous (no auth). We lazily create a cart on first add and
remember its masked id per user. Cloudflare requires a browser User-Agent.
"""
import json
import urllib.error
import urllib.request

from commerce.magento import fetch_images_by_sku
from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_UA = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}
_carts: dict[str, str] = {}  # username -> guest cart (masked) id
_FALLBACK_CCY = "BD"


def _call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_API + path, data=data, headers=_UA, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _err(e: urllib.error.HTTPError) -> str:
    try:
        return json.loads(e.read().decode()).get("message", "Sorry, that couldn't be added.")
    except Exception:
        return "Sorry, that couldn't be added."


def empty() -> dict:
    return {"items": [], "items_qty": 0, "grand_total": 0, "currency": _FALLBACK_CCY}


def _cart_id(username: str, create: bool = False) -> str | None:
    cid = _carts.get(username)
    if cid is None and create:
        cid = _call("POST", "/guest-carts")
        _carts[username] = cid
    return cid


def add_item(username: str, sku: str, qty: int = 1) -> dict:
    cid = _cart_id(username, create=True)
    body = {"cartItem": {"sku": sku, "qty": qty, "quote_id": cid}}
    try:
        _call("POST", f"/guest-carts/{cid}/items", body)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e), "cart": view(username)}
    return {"ok": True, "cart": view(username)}


def remove_item(username: str, item_id: int) -> dict:
    cid = _cart_id(username)
    if not cid:
        return {"ok": True, "cart": empty()}
    try:
        _call("DELETE", f"/guest-carts/{cid}/items/{item_id}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e), "cart": view(username)}
    return {"ok": True, "cart": view(username)}


def view(username: str) -> dict:
    cid = _cart_id(username)
    if not cid:
        return empty()
    try:
        cart = _call("GET", f"/guest-carts/{cid}")
        totals = _call("GET", f"/guest-carts/{cid}/totals")
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
