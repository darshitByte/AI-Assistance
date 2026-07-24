"""Customer-cart checkout: address → shipping → place order → invoice → PDF.

Two auth models, same split as the rest of commerce/:
- Cart + order placement use the app user's Magento customer cart (`/carts/mine`,
  customer bearer token via cart.py's `_call`). This store disallows guest
  checkout, so the order must belong to a real customer.
- Invoice creation + order reads use the admin Bearer token (reuses magento.py).
"""
import urllib.error
from datetime import date, timedelta

from commerce import magento
from commerce.cart import _call, _err
from db import users as users_db


def _address(form: dict) -> dict:
    """Magento address object from the checkout form fields."""
    name = (form.get("name") or "").strip()
    first, _, last = name.partition(" ")
    return {
        "firstname": first or name or "Guest",
        "lastname": last or "Customer",
        "email": form.get("email", ""),
        "telephone": form.get("phone", ""),
        "street": [form.get("street", "")],
        "city": form.get("city", ""),
        "postcode": form.get("postcode", ""),
        "country_id": form.get("country_id") or "BH",
        "region": form.get("region", ""),
    }


def _pick_method(methods: list[dict]) -> dict | None:
    """Prefer free shipping, else the first available method."""
    avail = [m for m in methods if m.get("available", True)]
    if not avail:
        return None
    return next((m for m in avail if m.get("method_code") == "freeshipping"), avail[0])


def quote(username: str, form: dict) -> dict:
    """Set shipping info on the Magento cart, return the store's payment methods + total."""
    address = _address(form)
    if not _call(username, "GET", "/carts/mine").get("items"):
        return {"ok": False, "error": "Your cart is empty."}
    try:
        methods = _call(username, "POST", "/carts/mine/estimate-shipping-methods",
                        {"address": address})
        method = _pick_method(methods)
        if not method:
            return {"ok": False, "error": "No shipping method available for this address."}
        info = _call(username, "POST", "/carts/mine/shipping-information", {
            "addressInformation": {
                "shipping_address": address,
                "billing_address": address,
                "shipping_carrier_code": method["carrier_code"],
                "shipping_method_code": method["method_code"],
            }})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e)}
    return {
        "ok": True,
        "payment_methods": info.get("payment_methods", []),
        "totals": info.get("totals", {}),
    }


def place(username: str, payment_method_code: str) -> dict:
    """Place the order, then invoice it (admin token). Order stands even if invoicing fails.
    The cart already carries the billing address + customer email (set by quote's
    shipping-information), so we just submit the payment method — DB-free, no address here."""
    if not _call(username, "GET", "/carts/mine").get("items"):
        return {"ok": False, "error": "Your cart is empty."}
    # ponytail: payment seam — an offline method (COD/bank transfer) places the order
    # directly here; an online gateway (Phase 2) hooks in at this call once the Magento
    # payment module is known (may return a redirect URL instead of a bare order id).
    try:
        order_id = _call(username, "POST", "/carts/mine/payment-information", {
            "paymentMethod": {"method": payment_method_code},
        })
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": _err(e)}

    users_db.add_order(username, order_id)  # customer cart is consumed by the order

    result = {"ok": True, "order_id": order_id, "delivery_by": _delivery_estimate()}
    try:
        magento.post_json(f"/order/{order_id}/invoice", {"capture": False, "notify": True})
    except Exception as e:  # noqa: BLE001 — order is real regardless of the invoice
        result["invoice_error"] = str(e)
    result.update(order_summary(order_id))
    return result


def order_summary(order_id: int) -> dict:
    """Card/PDF payload from the placed order (admin token). Billing == shipping here."""
    o = magento.get_json(f"/orders/{order_id}")
    items = [
        {"name": it.get("name"), "qty": int(it.get("qty_ordered") or 0),
         "row_total": it.get("row_total")}
        for it in o.get("items", []) if not it.get("parent_item_id")
    ]
    b = o.get("billing_address", {})
    ship_to = ", ".join(x for x in (b.get("city"), b.get("region")) if x)
    return {
        "order_increment_id": o.get("increment_id"),
        "items": items,
        "total": o.get("grand_total"),
        "currency": o.get("order_currency_code") or "BD",
        "ship_to": ship_to,
    }


def invoice_pdf(order_id: int) -> bytes:
    return _render_pdf(order_summary(order_id))


def _render_pdf(summary: dict) -> bytes:
    """One-page invoice PDF. ponytail: naive single-page layout; richer template only
    if a branded invoice is ever needed."""
    from fpdf import FPDF

    cur = summary.get("currency", "BD")
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Grocerzy - Invoice"); pdf.ln(14)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Order #: {summary.get('order_increment_id', '')}"); pdf.ln(7)
    if summary.get("ship_to"):
        pdf.cell(0, 7, f"Ship to: {summary['ship_to']}"); pdf.ln(7)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(120, 8, "Item"); pdf.cell(20, 8, "Qty"); pdf.cell(40, 8, "Total"); pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    for it in summary.get("items", []):
        pdf.cell(120, 8, str(it.get("name", ""))[:60])
        pdf.cell(20, 8, str(it.get("qty", "")))
        pdf.cell(40, 8, f"{cur} {it.get('row_total', '')}"); pdf.ln(8)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(140, 8, "Total Paid"); pdf.cell(40, 8, f"{cur} {summary.get('total', '')}")
    return bytes(pdf.output())


def _delivery_estimate() -> str:
    return "Grocerzy team will notify you about the delivery date & time."


if __name__ == "__main__":  # network-free self-check
    a = _address({"name": "Jane Doe", "email": "j@x.com", "phone": "1",
                  "street": "1 St", "city": "Manama", "postcode": "000"})
    assert a["firstname"] == "Jane" and a["lastname"] == "Doe", a
    assert a["street"] == ["1 St"] and a["country_id"] == "BH", a
    assert _pick_method([{"carrier_code": "flatrate", "method_code": "flatrate"}])["method_code"] == "flatrate"
    assert _pick_method([{"method_code": "x", "available": False}]) is None
    try:
        pdf = _render_pdf({"order_increment_id": "000000001", "currency": "BD",
                           "items": [{"name": "Tee", "qty": 2, "row_total": 10}], "total": 10})
        assert pdf[:4] == b"%PDF", pdf[:8]
        print("checkout self-check ok (incl. PDF)")
    except ImportError:
        print("checkout self-check ok (fpdf2 not installed — PDF render skipped)")
