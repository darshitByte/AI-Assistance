"""Thin async client for a stdio MCP server (the Bold magento2-mcp Node server).

Owns the Node subprocess and exposes the live MCP session; the agent adapts the
session's tools via langchain's load_mcp_tools.
"""
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        self._params = StdioServerParameters(command=command, args=args, env=env)
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self._tools = []

    async def connect(self) -> None:
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self._tools = (await self.session.list_tools()).tools

    async def reconnect(self, env: dict[str, str]) -> None:
        """Respawn the subprocess with a new env (the Node server reads the token
        once at spawn, so a refreshed token needs a fresh process)."""
        await self.close()
        self._params = StdioServerParameters(
            command=self._params.command, args=self._params.args, env=env
        )
        await self.connect()

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
