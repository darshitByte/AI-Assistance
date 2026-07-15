"""Direct Magento REST helpers (image enrichment, product count).

The store is behind Cloudflare, which 403s requests without a browser User-Agent.
"""
import json
import urllib.parse
import urllib.request

from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_STORE_ROOT = _API.removesuffix("/rest/V1")
MEDIA_BASE = _STORE_ROOT + "/media/catalog/product"

_HEADERS = {
    "Authorization": f"Bearer {config.MAGENTO_API_TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}


def _image_url(entries: list | None) -> str | None:
    if not entries:
        return None
    main = next((e for e in entries if "image" in (e.get("types") or [])), entries[0])
    file = main.get("file")
    return MEDIA_BASE + file if file else None


def total_product_count() -> int | None:
    url = _API + "/products?searchCriteria[pageSize]=1&fields=total_count"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("total_count")
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
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        return {}
    return {p["sku"]: _image_url(p.get("media_gallery_entries")) for p in data.get("items", [])}
