"""The chat agent (LangChain `create_agent`), built once and reused.

History lives in a LangGraph checkpointer keyed by thread_id=username — so each
turn we pass only the new message and the checkpointer replays the rest. The
agent is user-agnostic and shared; per-request the username rides in through the
run context (UserContext) so cart tools know whose cart to touch.
"""
import json
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from ai.runtime import runtime
from commerce import cart as cartmod
from core import config
from core.log import logger
from prompts.system import SYSTEM_PROMPT


@dataclass
class UserContext:
    username: str


@tool
def add_to_cart(sku: str, runtime: ToolRuntime[UserContext], qty: int = 1) -> str:
    """Add a product to the shopping cart by its exact SKU (use a specific
    variant SKU like 'S-1001-Black', not a parent)."""
    return json.dumps(cartmod.add_item(runtime.context.username, sku, qty))


@tool
def view_cart(runtime: ToolRuntime[UserContext]) -> str:
    """Show the customer's current shopping cart and total."""
    return json.dumps(cartmod.view(runtime.context.username))


@tool
def remove_from_cart(item_id: int, runtime: ToolRuntime[UserContext]) -> str:
    """Remove a line item from the cart by its item_id."""
    return json.dumps(cartmod.remove_item(runtime.context.username, item_id))


CART_TOOLS = [add_to_cart, view_cart, remove_from_cart]


def _resilient(t):
    """Never let an MCP error crash the turn: surface it to the model as text so
    it can retry (mirrors the old mcp_client boundary). The agent's default
    handler only catches arg-binding errors and re-raises everything else."""
    orig = t.coroutine

    async def safe(*args, **kwargs):
        try:
            return await orig(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — any MCP/server error → model-visible text
            msg = f"[tool error] {e}"
            # MCP adapter tools use response_format='content_and_artifact' → (content, artifact).
            return (msg, None) if t.response_format == "content_and_artifact" else msg

    t.coroutine = safe
    return t


_agent = None


async def get_agent():
    """Lazily build the agent on first use, then reuse the same instance."""
    global _agent
    if _agent is None:
        model = ChatOpenAI(
            model=config.LLM_MODEL,
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            temperature=1,
            top_p=0.95,
            max_tokens=16384,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
                "reasoning_budget": 16384,
            },
        )
        mcp_tools = [_resilient(t) for t in await load_mcp_tools(runtime.mcp.session)]
        tools = mcp_tools + CART_TOOLS
        _agent = create_agent(
            model,
            tools=tools,
            context_schema=UserContext,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )
        logger.info("agent built: %d tools (checkpointer=InMemorySaver)", len(tools))
    return _agent
