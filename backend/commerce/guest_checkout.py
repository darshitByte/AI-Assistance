"""Guest checkout — place an order without an account, using Magento's anonymous
guest-cart REST (`/guest-carts`). No bearer token; Cloudflare still needs a browser UA.

Mirrors commerce/checkout.py (the customer path) and reuses its address/shipping/order
helpers. The guest's in-memory browsing cart (guest_cart.py) is materialised into a real
Magento guest cart the first time we quote, then reused at place-time.

Requires "Allow Guest Checkout = Yes" in the Magento store config — without it the
shipping-information call returns "Sorry, guest checkout is not available."

In-memory session cache (guest_id -> cart_id/address/email), same model as the other
caches here: a backend restart empties it, and the user re-runs checkout.
"""
import json
import threading
import urllib.error
import urllib.request

from commerce import guest_cart, magento
from commerce.checkout import _address, _delivery_estimate, _pick_method, order_summary
from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_UA = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}
_lock = threading.Lock()
_sessions: dict[str, dict] = {}   # guest_id -> {"cart_id": str, "address": dict, "email": str}


def _post(path: str, body: dict | None = None, timeout: int = 30):
    """Anonymous guest-cart POST → parsed JSON. Raises urllib.error.HTTPError on failure."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_API + path, data=data, headers=dict(_UA), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _err(e: urllib.error.HTTPError) -> str:
    try:
        return json.loads(e.read().decode()).get("message", "Checkout failed.")
    except Exception:  # noqa: BLE001
        return "Checkout failed."


def _ensure_cart(guest_id: str) -> str | None:
    """Materialise the in-memory guest cart into a Magento guest cart (once). Returns the
    masked cart id, or None if the browsing cart is empty."""
    with _lock:
        sess = _sessions.get(guest_id)
    if sess and sess.get("cart_id"):
        return sess["cart_id"]
    lines = guest_cart.items(guest_id)
    if not lines:
        return None
    cid = _post("/guest-carts")  # masked quote id (string)
    for sku, qty in lines:
        _post(f"/guest-carts/{cid}/items", {"cartItem": {"sku": sku, "qty": qty, "quote_id": cid}})
    with _lock:
        _sessions.setdefault(guest_id, {})["cart_id"] = cid
    return cid


def quote(guest_id: str, form: dict) -> dict:
    """Set shipping on the guest cart; return payment methods + total. Caches the address +
    email so place() can reuse them (guest payment-information requires both in the body)."""
    cid = _ensure_cart(guest_id)
    if not cid:
        return {"ok": False, "error": "Your cart is empty."}
    address = _address(form)
    try:
        methods = _post(f"/guest-carts/{cid}/estimate-shipping-methods", {"address": address})
        method = _pick_method(methods)
        if not method:
            return {"ok": False, "error": "No shipping method available for this address."}
        info = _post(f"/guest-carts/{cid}/shipping-information", {
            "addressInformation": {
                "shipping_address": address,
                "billing_address": address,
                "shipping_carrier_code": method["carrier_code"],
                "shipping_method_code": method["method_code"],
            }})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e)}
    with _lock:
        _sessions.setdefault(guest_id, {}).update(address=address, email=form.get("email", ""))
    return {"ok": True, "payment_methods": info.get("payment_methods", []), "totals": info.get("totals", {})}


def place(guest_id: str, payment_method_code: str) -> dict:
    """Place the guest order, then invoice it (admin token). The order stands even if
    invoicing fails. Clears the guest cart + session on success."""
    with _lock:
        sess = _sessions.get(guest_id) or {}
    cid, address, email = sess.get("cart_id"), sess.get("address"), sess.get("email")
    if not (cid and address):
        return {"ok": False, "error": "Please choose a delivery address first."}
    try:
        order_id = _post(f"/guest-carts/{cid}/payment-information", {
            "email": email,
            "paymentMethod": {"method": payment_method_code},
            "billingAddress": address,
        })
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e)}

    guest_cart.clear(guest_id)
    with _lock:
        _sessions.pop(guest_id, None)  # cart is consumed by the order

    result = {"ok": True, "order_id": order_id, "delivery_by": _delivery_estimate()}
    try:
        magento.post_json(f"/order/{order_id}/invoice", {"capture": False, "notify": True})
    except Exception as e:  # noqa: BLE001 — order is real regardless of the invoice
        result["invoice_error"] = str(e)
    result.update(order_summary(order_id))
    return result


if __name__ == "__main__":  # network-free self-check (seeds the session cache directly)
    with _lock:
        _sessions["g1"] = {"cart_id": "abc"}
    # empty browsing cart → no address cached yet → place refuses before any network call
    assert place("g1", "checkmo")["ok"] is False, "place must refuse without an address"
    with _lock:
        _sessions["g2"] = {"cart_id": "abc", "address": {"city": "Manama"}, "email": "g@x.com"}
    assert _ensure_cart("g2") == "abc", "cached cart id must be reused (no re-create)"
    print("guest_checkout self-check ok")
