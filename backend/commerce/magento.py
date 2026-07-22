"""Direct Magento REST helpers (image enrichment, product count).

The store is behind Cloudflare, which 403s requests without a browser User-Agent.
"""
import json
import urllib.parse
import urllib.request

from commerce import magento_token
from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_STORE_ROOT = _API.removesuffix("/rest/V1")
MEDIA_BASE = _STORE_ROOT + "/media/catalog/product"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {magento_token.get_token()}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def _request(method: str, url: str, body: dict | None, timeout: int):
    """Admin-token request → parsed JSON (permanent Integration token)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_json(url: str, timeout: int):
    return _request("GET", url, None, timeout)


def get_json(path: str, timeout: int = 30):
    """Admin-token GET by REST path (e.g. '/orders/5')."""
    return _request("GET", _API + path, None, timeout)


def post_json(path: str, body: dict, timeout: int = 30):
    """Admin-token POST by REST path (e.g. '/order/5/invoice'). Used by checkout."""
    return _request("POST", _API + path, body, timeout)


def _image_url(entries: list | None) -> str | None:
    if not entries:
        return None
    main = next((e for e in entries if "image" in (e.get("types") or [])), entries[0])
    file = main.get("file")
    return MEDIA_BASE + file if file else None


def total_product_count() -> int | None:
    url = _API + "/products?searchCriteria[pageSize]=1&fields=total_count"
    try:
        return _get_json(url, 20).get("total_count")
    except Exception:
        return None


def fetch_images_by_sku(skus: list[str]) -> dict[str, str | None]:
    """Return {sku: image_url} for the given SKUs (one REST call)."""
    if not skus:
        return {}
    params = {
        "searchCriteria[filterGroups][0][filters][0][field]": "sku",
        "searchCriteria[filterGroups][0][filters][0][value]": ",".join(skus),
        "searchCriteria[filterGroups][0][filters][0][conditionType]": "in",
        "searchCriteria[pageSize]": str(len(skus)),
        "fields": "items[sku,media_gallery_entries[file,types]]",
    }
    url = _API + "/products?" + urllib.parse.urlencode(params)
    try:
        data = _get_json(url, 30)
    except Exception:
        return {}
    return {p["sku"]: _image_url(p.get("media_gallery_entries")) for p in (data.get("items") or [])}


def fetch_products_by_sku(skus: list[str]) -> dict[str, dict]:
    """Return {sku: {name, price, image}} for the given SKUs (one REST call).
    Admin-token read — no customer token needed. Used to enrich the guest cart."""
    if not skus:
        return {}
    params = {
        "searchCriteria[filterGroups][0][filters][0][field]": "sku",
        "searchCriteria[filterGroups][0][filters][0][value]": ",".join(skus),
        "searchCriteria[filterGroups][0][filters][0][conditionType]": "in",
        "searchCriteria[pageSize]": str(len(skus)),
        "fields": "items[sku,name,price,media_gallery_entries[file,types]]",
    }
    url = _API + "/products?" + urllib.parse.urlencode(params)
    try:
        data = _get_json(url, 30)
    except Exception:
        return {}
    return {
        p["sku"]: {
            "name": p.get("name"),
            "price": p.get("price"),
            "image": _image_url(p.get("media_gallery_entries")),
        }
        for p in (data.get("items") or [])
    }
