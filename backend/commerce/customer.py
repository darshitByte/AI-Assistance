"""Per app-user Magento customer + auth token.

This store disallows guest checkout, so an order must belong to a real Magento
customer. The app has its own accounts (Mongo + JWT) with no Magento side, so we
lazily create ONE Magento customer per app user with deterministic synthetic
credentials (derived from JWT_SECRET, never stored — re-derivable on demand) and
cache the customer token in memory with a TTL — same model as the admin token in
magento_token.py. Customer JWTs expire ~hourly like admin ones.

Store is behind Cloudflare (403s without a browser User-Agent).
"""
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request

from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_UA = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}
_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}  # username -> (token, minted_at from time.monotonic())


def _creds(username: str) -> tuple[str, str]:
    """Synthetic Magento email+password for an app user. Deterministic (re-derivable,
    never persisted); the password satisfies Magento's 3-character-class policy."""
    digest = hmac.new(config.JWT_SECRET.encode(), username.encode(), hashlib.sha256).hexdigest()
    return f"{username}@grocerzy-poc.com", "Aa1!" + digest[:16]


def _post(path: str, body: dict, token: str | None = None):
    headers = dict(_UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(_API + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _ensure_customer(email: str, password: str, username: str) -> None:
    """Create the Magento customer; a 400 means the email already exists — fine."""
    try:
        _post("/customers", {"customer": {"email": email, "firstname": username, "lastname": "Customer"},
                             "password": password})
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise


def _mint(username: str) -> str:
    email, pw = _creds(username)
    try:
        return _post("/integration/customer/token", {"username": email, "password": pw})
    except urllib.error.HTTPError as e:
        if e.code == 401:  # customer doesn't exist yet → create, then retry once
            _ensure_customer(email, pw, username)
            return _post("/integration/customer/token", {"username": email, "password": pw})
        raise


def get_token(username: str, force: bool = False) -> str:
    """Current customer token for an app user, re-minting if forced or past the TTL."""
    with _lock:
        cached = _tokens.get(username)
        now = time.monotonic()
        if force or cached is None or (now - cached[1]) > config.MAGENTO_AUTH_TTL:
            token = _mint(username)
            _tokens[username] = (token, now)
            return token
        return cached[0]


if __name__ == "__main__":  # self-check: creds are deterministic + policy-compliant (no network)
    e1, p1 = _creds("alice")
    assert (e1, p1) == _creds("alice"), "creds must be deterministic"
    assert e1 == "alice@grocerzy-poc.com" and p1.startswith("Aa1!") and len(p1) == 20, (e1, p1)
    assert _creds("bob")[1] != p1, "different users → different passwords"
    print("customer self-check ok")
