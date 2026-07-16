"""Customer address book, backed by the Magento customer account (not our DB).

Addresses live on the Magento customer (GET/PUT /customers/me) so the same
addresses also appear on the storefront. We map between the app's flat form
shape and Magento's address object. Magento customer addresses have no label or
email, so the picker shows street/city text and the order email comes from the
account (username == email).

PUT /customers/me is slow through Cloudflare (>25s) and not idempotent, so
add_address uses a long timeout and skips the save when an identical address
already exists (a timed-out PUT may still have applied server-side).
"""
from commerce.cart import _call  # customer-token call with 401 re-mint

# The subset of the customer object Magento needs echoed back on PUT.
_SLIM_KEYS = ("id", "group_id", "email", "firstname", "lastname", "website_id", "store_id")


def _from_magento(a: dict, email: str) -> dict:
    """Magento address object -> the flat form shape the picker + quote expect."""
    region = a.get("region") or {}
    return {
        "id": a.get("id"),
        "name": " ".join(x for x in (a.get("firstname"), a.get("lastname")) if x).strip(),
        "email": email,  # Magento addresses carry no email; use the account email
        "phone": a.get("telephone", ""),
        "street": ", ".join(a.get("street") or []),
        "city": a.get("city", ""),
        "region": region.get("region") or "",
        "postcode": a.get("postcode", ""),
        "country_id": a.get("country_id") or "BH",
    }


def _to_magento(form: dict, make_default: bool) -> dict:
    """Flat form shape -> Magento customer address object."""
    name = (form.get("name") or "").strip()
    first, _, last = name.partition(" ")
    addr = {
        "firstname": first or name or "Guest",
        "lastname": last or "Customer",
        "street": [form.get("street", "")],
        "city": form.get("city", ""),
        "postcode": form.get("postcode", ""),
        "country_id": form.get("country_id") or "BH",
        "telephone": form.get("phone", ""),
        "default_shipping": make_default,
        "default_billing": make_default,
    }
    if form.get("region"):
        addr["region"] = {"region": form["region"]}  # BH has no directory regions → free text
    return addr


def _same(a: dict, b: dict) -> bool:
    """Cheap identity check to skip duplicate saves (PUT is not idempotent)."""
    return (a.get("street") == b.get("street")
            and a.get("city") == b.get("city")
            and a.get("postcode") == b.get("postcode"))


def get_addresses(username: str) -> list[dict]:
    """The Magento customer's saved addresses, in the app's form shape."""
    me = _call(username, "GET", "/customers/me")
    return [_from_magento(a, username) for a in (me.get("addresses") or [])]


def add_address(username: str, form: dict) -> list[dict]:
    """Append an address to the Magento customer account; return the refreshed list.
    Skips the write if an identical address already exists (idempotency guard)."""
    me = _call(username, "GET", "/customers/me")
    existing = me.get("addresses") or []
    new_addr = _to_magento(form, make_default=not existing)  # first address becomes default
    if not any(_same(a, new_addr) for a in existing):
        slim = {k: me[k] for k in _SLIM_KEYS if k in me}
        slim["addresses"] = existing + [new_addr]
        _call(username, "PUT", "/customers/me", {"customer": slim}, timeout=60)  # slow via Cloudflare
    return get_addresses(username)


if __name__ == "__main__":  # network-free self-check: the mapping round-trips
    m = _to_magento({"name": "Jane Doe", "phone": "1", "street": "1 St", "city": "Manama",
                     "region": "Capital", "postcode": "000", "country_id": "BH"}, make_default=True)
    assert m["firstname"] == "Jane" and m["lastname"] == "Doe", m
    assert m["street"] == ["1 St"] and m["region"] == {"region": "Capital"}, m
    assert m["default_shipping"] and m["default_billing"], m
    back = _from_magento({"id": 5, "firstname": "Jane", "lastname": "Doe", "telephone": "1",
                          "street": ["1 St"], "city": "Manama", "region": {"region": "Capital"},
                          "postcode": "000", "country_id": "BH"}, "jane@x.com")
    assert back["name"] == "Jane Doe" and back["street"] == "1 St" and back["region"] == "Capital", back
    assert back["email"] == "jane@x.com" and back["id"] == 5, back
    # dedupe guard
    assert _same({"street": ["1 St"], "city": "Manama", "postcode": "000"},
                 {"street": ["1 St"], "city": "Manama", "postcode": "000"})
    assert not _same({"street": ["1 St"], "city": "Manama", "postcode": "000"},
                     {"street": ["2 St"], "city": "Manama", "postcode": "000"})
    print("address self-check ok")
