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
from commerce import guest_cart
from commerce import magento_token
from core import config
from core.log import logger
from prompts.system import SYSTEM_PROMPT


@dataclass
class UserContext:
    username: str
    guest: bool = False


def _cart(ctx: "UserContext"):
    """Guests have no Magento token → their cart lives app-side (guest_cart)."""
    return guest_cart if ctx.guest else cartmod


@tool
def add_to_cart(sku: str, runtime: ToolRuntime[UserContext], qty: int = 1) -> str:
    """Add a product to the shopping cart by its exact SKU (use a specific
    variant SKU like 'S-1001-Black', not a parent)."""
    return json.dumps(_cart(runtime.context).add_item(runtime.context.username, sku, qty))


@tool
def view_cart(runtime: ToolRuntime[UserContext]) -> str:
    """Show the customer's current shopping cart and total."""
    return json.dumps(_cart(runtime.context).view(runtime.context.username))


@tool
def remove_from_cart(item_id: int | str, runtime: ToolRuntime[UserContext]) -> str:
    """Remove a line item from the cart by its item_id (for a guest cart, the sku)."""
    return json.dumps(_cart(runtime.context).remove_item(runtime.context.username, item_id))


@tool
async def browse_kinds(query: str) -> str:
    """Peek at what KINDS of a product actually exist in the store (real brands /
    types / variants) as plain text, WITHOUT showing any product cards. Call this
    before asking the customer a narrowing question so the options you offer are
    real — never invent examples. Returns distinct product names, or says nothing
    matched. This does NOT render cards, so use it freely to look before you ask."""
    try:
        res = await runtime.mcp.session.call_tool(
            "search_products", {"query": query, "page_size": 25}
        )
    except Exception as e:  # noqa: BLE001 — never let a lookup crash the turn
        return f"[browse error] {e}"
    text = "".join(getattr(b, "text", "") for b in (res.content or []))
    try:
        items = json.loads(text).get("items", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return "no matches found"
    names = list(dict.fromkeys(it.get("name") for it in items if it.get("name")))[:20]
    return "; ".join(names) if names else "no matches found"


@tool
def suggest_options(labels: list[str]) -> str:
    """Offer the customer a short set of tappable choices to go with a narrowing
    question. The app renders each label as a button the customer taps instead of
    typing — so pass 2-4 short, plain labels built from what's really in stock
    (call `browse_kinds` first), and don't also list them in your reply. Renders
    buttons, NOT product cards."""
    return json.dumps({"options": labels})


@tool
async def search_within_budget(
    query: str, max_price: float | None = None, min_price: float | None = None
) -> str:
    """Search by name AND price together. Use this whenever the customer gives a
    keyword plus a price limit (e.g. "mops under 300", "snacks between 50 and 100").
    Prices are in the store currency (BD). It name-searches first, then keeps only
    the products within budget — so results genuinely match the keyword AND the
    price, unlike filtering by price alone. Renders product cards."""
    try:
        res = await runtime.mcp.session.call_tool(
            "search_products", {"query": query, "page_size": 100}
        )
    except Exception as e:  # noqa: BLE001 — never crash the turn
        return f"[search error] {e}"
    text = "".join(getattr(b, "text", "") for b in (res.content or []))
    try:
        items = json.loads(text).get("items", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return json.dumps({"items": []})

    def in_budget(it) -> bool:
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            return False  # no usable price → can't promise it's within budget
        if min_price is not None and price < min_price:
            return False
        if max_price is not None and price > max_price:
            return False
        return True

    return json.dumps({"items": [it for it in items if in_budget(it)]})


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


def reset_agent() -> None:
    """Drop the cached agent so the next get_agent() rebuilds against a fresh MCP
    session (its tools are bound to whatever session existed at build time)."""
    global _agent
    _agent = None


async def ensure_fresh_mcp() -> None:
    """Before a turn: if the admin token is stale, re-mint it, respawn the MCP
    server with the new token, and force an agent rebuild."""
    if magento_token.is_stale():
        logger.info("Magento admin token stale — refreshing MCP connection")
        magento_token.get_token(force=True)
        await runtime.mcp.reconnect(magento_token.mcp_env())
        reset_agent()


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
            # gpt-oss reasons in a separate channel (not content), so no leak;
            # "low" keeps replies fast while still driving tool calls reliably.
            extra_body={"reasoning_effort": "low"},
        )
        mcp_tools = [_resilient(t) for t in await load_mcp_tools(runtime.mcp.session)]
        tools = mcp_tools + CART_TOOLS + [browse_kinds, search_within_budget, suggest_options]
        _agent = create_agent(
            model,
            tools=tools,
            context_schema=UserContext,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )
        logger.info("agent built: %d tools (checkpointer=InMemorySaver)", len(tools))
    return _agent
