"""Holds the connected MCP client + LLM provider for the app's lifetime.

Populated in main.py's lifespan and read by the API routers.
"""


class Runtime:
    mcp = None  # ai.mcp_client.MCPClient


runtime = Runtime()
