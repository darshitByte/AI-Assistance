"""The chat loop: run one turn through the LangChain agent, reply + cards + cart.

History is handled by the agent's checkpointer (thread_id=username), so we only
send the new message. We stream the run's updates to harvest product results
from the search tools (for the UI cards) and to grab the final reply; then we
return a fresh cart snapshot.
"""
import asyncio
import json
from functools import cache

from ai.agent import UserContext, ensure_fresh_mcp, get_agent
from commerce import cart as cartmod
from commerce.magento import fetch_images_by_sku
from core import config


@cache
def _tracer():
    """Langfuse LangChain callback, built once. Reads keys from os.environ
    (loaded by config). Returns None when creds are absent → tracing is a no-op."""
    if not config.LANGFUSE_ENABLED:
        return None
    from langfuse.langchain import CallbackHandler
    return CallbackHandler()

# Tools whose output we harvest into product cards: catalogue searches (many
# items) plus single-product detail lookups (one flat object).
SEARCH_TOOLS = {"search_products", "advanced_product_search",
                "get_product_by_sku", "get_product_by_id",
                "search_within_budget"}
MAX_CARDS = 8


async def run_turn(username: str, user_message: str, session_id: str) -> dict:
    """Run one turn for a user's chat session. Returns {reply, products, cart}."""
    await ensure_fresh_mcp()  # re-mint the Magento token + respawn MCP if expired
    agent = await get_agent()
    collected: dict[str, dict] = {}
    reply = "I wasn't able to finish that — could you try rephrasing?"
    cart_added = False  # did an add_to_cart run this turn? → UI shows next-step buttons

    run_config = {"configurable": {"thread_id": f"{username}:{session_id}"}}
    if handler := _tracer():
        # Groups traces by user + chat session in the Langfuse UI.
        run_config["callbacks"] = [handler]
        run_config["metadata"] = {"langfuse_user_id": username,
                                  "langfuse_session_id": session_id}

    async for update in agent.astream(
        {"messages": [{"role": "user", "content": user_message}]},
        # thread_id scopes the checkpointer's history: per user, per chat session.
        config=run_config,
        context=UserContext(username=username),
        stream_mode="updates",
    ):
        for payload in update.values():
            for msg in (payload or {}).get("messages", []):
                mtype = getattr(msg, "type", None)
                if mtype == "tool" and getattr(msg, "name", None) in SEARCH_TOOLS:
                    _collect_products(_text(msg.content), collected)
                elif mtype == "tool" and getattr(msg, "name", None) == "add_to_cart":
                    cart_added = True
                elif mtype == "ai" and msg.content and not getattr(msg, "tool_calls", None):
                    reply = _text(msg.content).strip()

    cart = await asyncio.to_thread(cartmod.view, username)
    return {"reply": reply, "products": await _enrich(collected),
            "cart": cart, "cart_added": cart_added}


def _text(content) -> str:
    """Message content is a str, or a list of content blocks — flatten to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content or "")


def _collect_products(tool_output: str, collected: dict[str, dict]) -> None:
    try:
        data = json.loads(tool_output)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return
    # search tools return {"items": [...]}; detail tools return one flat product.
    items = data.get("items", []) if "items" in data else [data]
    for it in items:
        sku = it.get("sku")
        if not sku or sku in collected or it.get("type_id") == "configurable":
            continue
        collected[sku] = {"sku": sku, "name": it.get("name"), "price": it.get("price")}


async def _enrich(collected: dict[str, dict]) -> list[dict]:
    skus = list(collected)[:MAX_CARDS]
    if not skus:
        return []
    images = await asyncio.to_thread(fetch_images_by_sku, skus)
    return [{**collected[sku], "image": images.get(sku)} for sku in skus]
