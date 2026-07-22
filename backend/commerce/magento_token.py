"""Magento admin-token provider.

A permanent Integration token (System > Integrations) lives in MAGENTO_API_TOKEN
and never expires, so there's nothing to mint or refresh — just hand it out.
Store is behind Cloudflare (403s without a browser User-Agent).
"""
from core import config


def get_token() -> str:
    return config.MAGENTO_API_TOKEN


def mcp_env() -> dict[str, str]:
    """Env for the MCP subprocess (reads the token once at spawn)."""
    return {"MAGENTO_BASE_URL": config.MAGENTO_BASE_URL, "MAGENTO_API_TOKEN": config.MAGENTO_API_TOKEN}
