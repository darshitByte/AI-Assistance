"""In-memory guest cart — holds chat-added items during anonymous browsing.

This store disallows guest checkout, so there's no anonymous Magento cart; a
guest's items live here (keyed by a browser-generated guest id) until the
checkout login, when /cart/merge pushes them into the customer's /carts/mine.

In-memory only (like the token/password caches + the chat checkpointer): a
backend restart empties it. Product name/price/image are looked up once at
add-time via the admin token (no customer token needed). The view mirrors
commerce/cart.py's shape so callers dispatch without caring which cart they hit;
`item_id` is the sku (a guest line has no Magento item id) so the same UI
remove-by-id path works.
"""
import threading

from commerce.magento import fetch_products_by_sku

_lock = threading.Lock()
_carts: dict[str, dict[str, dict]] = {}   # guest_id -> {sku: {qty, name, price, image}}
_FALLBACK_CCY = "BD"


def _enrich(sku: str) -> dict:
    info = fetch_products_by_sku([sku]).get(sku, {})
    return {"name": info.get("name") or sku, "price": info.get("price"), "image": info.get("image")}


def add_item(guest_id: str, sku: str, qty: int = 1) -> dict:
    with _lock:
        exists = sku in _carts.get(guest_id, {})
    # ponytail: enrich outside the lock (a Magento call under a global lock would
    # stall every guest); a rare double-enrich of the same new sku is harmless.
    enriched = {} if exists else _enrich(sku)
    with _lock:
        cart = _carts.setdefault(guest_id, {})
        if sku in cart:
            cart[sku]["qty"] += qty
        else:
            cart[sku] = {"qty": qty, **enriched}
    return {"ok": True, "cart": view(guest_id)}


def remove_item(guest_id: str, sku: str) -> dict:
    with _lock:
        _carts.get(guest_id, {}).pop(sku, None)
    return {"ok": True, "cart": view(guest_id)}


def view(guest_id: str) -> dict:
    with _lock:
        lines = list(_carts.get(guest_id, {}).items())
    items = [
        {"item_id": sku, "sku": sku, "name": l["name"], "qty": l["qty"],
         "price": l["price"], "image": l["image"]}
        for sku, l in lines
    ]
    return {
        "items": items,
        "items_qty": sum(int(i["qty"]) for i in items),
        "grand_total": sum((i["price"] or 0) * i["qty"] for i in items),
        "currency": _FALLBACK_CCY,
    }


def items(guest_id: str) -> list[tuple[str, int]]:
    """(sku, qty) pairs for merging into the customer cart at checkout login."""
    with _lock:
        return [(sku, l["qty"]) for sku, l in _carts.get(guest_id, {}).items()]


def clear(guest_id: str) -> None:
    with _lock:
        _carts.pop(guest_id, None)


if __name__ == "__main__":  # network-free self-check (seeds _carts directly, bypassing _enrich)
    with _lock:
        _carts["g1"] = {"S-1": {"qty": 2, "name": "Tee", "price": 5.0, "image": None}}
    add_item("g1", "S-1", 3)  # accumulates onto the existing line (sku already present → no network)
    v = view("g1")
    assert v["items_qty"] == 5 and v["grand_total"] == 25.0, v
    assert v["items"][0]["item_id"] == "S-1", v
    assert items("g1") == [("S-1", 5)], items("g1")
    remove_item("g1", "S-1")
    assert view("g1")["items_qty"] == 0, view("g1")
    _carts["g2"] = {"X": {"qty": 1, "name": "X", "price": None, "image": None}}
    assert view("g2")["grand_total"] == 0, "missing price must not crash totals"
    clear("g2")
    assert items("g2") == []
    print("guest_cart self-check ok")
