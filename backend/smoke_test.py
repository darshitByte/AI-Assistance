"""Smoke test: prove the Python->Node MCP bridge works end to end.

Spawns the Bold magento2-mcp server and lists its tools (no Magento call, so it
passes with a dummy token). Run from the repo root: `python backend/smoke_test.py`
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai.mcp_client import MCPClient  # noqa: E402
from core import config  # noqa: E402


async def main():
    env = dict(config.MCP_ENV)
    env["MAGENTO_API_TOKEN"] = env.get("MAGENTO_API_TOKEN") or "dummy-token-for-listing"
    env["MAGENTO_BASE_URL"] = env.get("MAGENTO_BASE_URL") or "https://example.com/rest/V1"

    mcp = MCPClient(config.MCP_COMMAND, config.MCP_ARGS, env)
    await mcp.connect()
    names = mcp.tool_names()
    print(f"Connected. {len(names)} tools exposed:")
    for name in names:
        print(f"  - {name}")
    await mcp.close()
    assert "search_products" in names, "search_products missing"
    print("\nOK: search_products is available.")


if __name__ == "__main__":
    asyncio.run(main())
