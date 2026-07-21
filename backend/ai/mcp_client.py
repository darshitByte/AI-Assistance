"""Thin async client for a stdio MCP server (the Bold magento2-mcp Node server).

Owns the Node subprocess and exposes the live MCP session; the agent adapts the
session's tools via langchain's load_mcp_tools.

The MCP stdio transport binds anyio cancel scopes to the task that opened it,
so connect/reconnect/close must run in one dedicated supervisor task. Request
handlers queue reconnect/stop ops instead of calling aclose() directly.
"""
import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.log import logger


class MCPClient:
    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        self._params = StdioServerParameters(command=command, args=args, env=env)
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self._tools = []
        self._queue: asyncio.Queue | None = None
        self._supervisor: asyncio.Task | None = None
        self._startup: asyncio.Future | None = None

    async def start(self) -> None:
        """Launch the MCP supervisor and wait until the first connection is ready."""
        if self._supervisor is not None:
            return
        self._queue = asyncio.Queue()
        self._startup = asyncio.get_running_loop().create_future()
        self._supervisor = asyncio.create_task(self._supervisor_loop(), name="mcp-supervisor")
        await self._startup

    async def connect(self) -> None:
        """Standalone connect for tests — caller must reconnect/close from the same task."""
        await self._connect_impl()

    async def reconnect(self, env: dict[str, str]) -> None:
        """Respawn the subprocess with a new env (the Node server reads the token
        once at spawn, so a refreshed token needs a fresh process)."""
        if self._supervisor is not None:
            await self._enqueue("reconnect", env)
            return
        await self._close_impl()
        self._params = StdioServerParameters(
            command=self._params.command, args=self._params.args, env=env
        )
        await self._connect_impl()

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    async def close(self) -> None:
        if self._supervisor is not None:
            await self._enqueue("stop")
            await self._supervisor
            self._supervisor = None
            self._queue = None
            return
        await self._close_impl()

    async def _enqueue(self, kind: str, payload: dict[str, str] | None = None) -> None:
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((kind, payload, fut))
        await fut

    async def _supervisor_loop(self) -> None:
        try:
            await self._connect_impl()
            self._startup.set_result(None)
        except Exception as exc:
            self._startup.set_exception(exc)
            raise

        while True:
            kind, payload, fut = await self._queue.get()
            try:
                if kind == "stop":
                    await self._close_impl()
                    fut.set_result(None)
                    return
                if kind == "reconnect":
                    logger.info("MCP reconnect: respawning Node server with fresh token")
                    await self._close_impl()
                    self._params = StdioServerParameters(
                        command=self._params.command,
                        args=self._params.args,
                        env=payload,
                    )
                    await self._connect_impl()
                    fut.set_result(None)
            except Exception as exc:
                fut.set_exception(exc)

    async def _connect_impl(self) -> None:
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self._tools = (await self.session.list_tools()).tools

    async def _close_impl(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self.session = None
        self._tools = []
