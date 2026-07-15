"""Magento admin-token provider: mints from admin credentials, caches in memory,
re-mints when stale. Kills the ~1h admin-JWT expiry — no static token anywhere.

The token lives only in this process's memory (like the guest-cart map); on
restart we just log in again. Store is behind Cloudflare (403s without a
browser User-Agent).
"""
import json
import threading
import time
import urllib.request

from core import config

_API = config.MAGENTO_BASE_URL.rstrip("/")
_lock = threading.Lock()
_token: str | None = None
_minted_at = 0.0  # time.monotonic() of the last successful mint


def _stale(token, minted_at, now, ttl) -> bool:
    return token is None or (now - minted_at) > ttl


def _mint() -> str:
    body = json.dumps(
        {"username": config.MAGENTO_ADMIN_USER, "password": config.MAGENTO_ADMIN_PASSWORD}
    ).encode()
    req = urllib.request.Request(
        _API + "/integration/admin/token",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)  # Magento returns the token as a bare JSON string


def is_stale() -> bool:
    return _stale(_token, _minted_at, time.monotonic(), config.MAGENTO_AUTH_TTL)


def get_token(force: bool = False) -> str:
    """Current admin token, re-minting if forced or older than the TTL."""
    global _token, _minted_at
    with _lock:
        if force or is_stale():
            _token = _mint()
            _minted_at = time.monotonic()
        return _token


def mcp_env() -> dict[str, str]:
    """Env for the MCP subprocess, carrying a fresh token (read once at spawn)."""
    return {"MAGENTO_BASE_URL": config.MAGENTO_BASE_URL, "MAGENTO_API_TOKEN": get_token()}


if __name__ == "__main__":  # self-check: staleness boundary (no network)
    assert _stale(None, 0, 0, 100)          # no token yet → stale
    assert not _stale("t", 0, 50, 100)      # within TTL → fresh
    assert _stale("t", 0, 150, 100)         # past TTL → stale
    print("ok")
