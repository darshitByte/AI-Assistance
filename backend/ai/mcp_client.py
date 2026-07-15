"""Thin async client for a stdio MCP server (the Bold magento2-mcp Node server).

Commerce Connector boundary: the AI layer only calls list_openai_tools() /
call_tool(), so swapping platforms later means changing config, not this code.
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

    def list_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in self._tools
        ]

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    async def call_tool(self, name: str, arguments: dict) -> str:
        # Never raise into the chat loop: bad/missing args or server errors are
        # returned as a tool_result so the model can see the problem and retry.
        try:
            result = await self.session.call_tool(name, arguments)
        except Exception as e:  # noqa: BLE001 — surface any MCP error to the model
            return f"[tool error] {e}"
        text = "\n".join(
            block.text for block in result.content if getattr(block, "type", None) == "text"
        )
        if result.isError:
            return f"[tool error] {text or 'unknown error'}"
        return text or "[no content returned]"

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
