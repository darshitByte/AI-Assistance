"""Per app-user Magento customer + auth token.

This store disallows guest checkout, so an order must belong to a real Magento
customer. The app's identity IS the Magento customer: signup creates one with the
user's real email+password (POST /customers, anonymous), login authenticates via
the customer-token endpoint. The app's `username` value is that email.

The customer password is NOT derivable (it's the user's own), so we cache it in
process memory — same model as the token caches. On restart the cache is empty:
get_token() raises CustomerError, which the API maps to 401 → the frontend logs
the user out and they sign in again. Store is behind Cloudflare (403 without a
browser User-Agent).
"""
import json
import threading
import time
import urllib.error
import urllib.request

from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_UA = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}
_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}   # email -> (token, minted_at from time.monotonic())
_passwords: dict[str, str] = {}              # email -> plaintext pw (in-memory only; ponytail ceiling)


class CustomerError(Exception):
    """Any Magento customer create/auth failure, carrying a user-facing message."""


def remember_password(email: str, password: str) -> None:
    with _lock:
        _passwords[email] = password


def _creds(username: str) -> tuple[str, str | None]:
    """username IS the email; password comes from the in-memory cache (None after restart)."""
    return username, _passwords.get(username)


def _msg(e: urllib.error.HTTPError) -> str:
    try:
        return json.loads(e.read().decode()).get("message", "Request failed.")
    except Exception:  # noqa: BLE001
        return "Request failed."


def _post(path: str, body: dict, token: str | None = None):
    headers = dict(_UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(_API + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _mint(email: str, password: str) -> str:
    """Mint a customer token; 401 -> bad creds (CustomerError), other errors propagate as CustomerError."""
    try:
        return _post("/integration/customer/token", {"username": email, "password": password})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise CustomerError("Invalid email or password.") from e
        raise CustomerError(_msg(e)) from e


def create(email: str, password: str) -> None:
    """Create the Magento customer (anonymous). Raises CustomerError on policy failure or
    duplicate email (message contains 'already exists'). Caches the password on success."""
    try:
        _post("/customers", {
            "customer": {"email": email, "firstname": email.split("@")[0] or "Customer", "lastname": "Customer"},
            "password": password,
        })
    except urllib.error.HTTPError as e:
        raise CustomerError(_msg(e)) from e
    remember_password(email, password)


def authenticate(email: str, password: str) -> str:
    """Login path: verify creds by minting a token, cache password + token. Raises CustomerError."""
    tok = _mint(email, password)
    remember_password(email, password)
    with _lock:
        _tokens[email] = (tok, time.monotonic())
    return tok


def get_token(username: str, force: bool = False) -> str:
    """Cart/checkout path: current customer token, re-minting from the cached password past the TTL.
    Raises CustomerError if no password is cached (backend restarted → re-login needed)."""
    with _lock:
        cached = _tokens.get(username)
        now = time.monotonic()
        if not force and cached and (now - cached[1]) <= config.MAGENTO_AUTH_TTL:
            return cached[0]
    email, pw = _creds(username)
    if not pw:
        raise CustomerError("Session expired — please log in again.")
    tok = _mint(email, pw)
    with _lock:
        _tokens[username] = (tok, time.monotonic())
    return tok


if __name__ == "__main__":  # network-free self-check: password cache + email passthrough
    remember_password("a@b.com", "Secret@1")
    assert _creds("a@b.com") == ("a@b.com", "Secret@1"), _creds("a@b.com")
    assert _creds("missing@x.com") == ("missing@x.com", None)
    print("customer self-check ok")
