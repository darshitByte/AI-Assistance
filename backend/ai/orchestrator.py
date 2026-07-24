"""The chat loop: run one turn through the LangChain agent, reply + cards + cart.

History is handled by the agent's checkpointer (thread_id=username), so we only
send the new message. We stream the run's updates to harvest product results
from the search tools (for the UI cards) and to grab the final reply; then we
return a fresh cart snapshot.
"""
import asyncio
import json
import re
from functools import cache

from ai.agent import UserContext, get_agent
from commerce import cart as cartmod
from commerce import guest_cart
from commerce.magento import fetch_images_by_sku
from core import config
from core.log import logger
from vector import service as vector_service


@cache
def _tracer():
    """Langfuse LangChain callback, built once. Reads keys from os.environ
    (loaded by config). Returns None when creds are absent → tracing is a no-op."""
    if not config.LANGFUSE_ENABLED:
        return None
    from langfuse.langchain import CallbackHandler
    return CallbackHandler()

# Tools whose output we harvest into product cards: catalogue searches (many
# items) plus single-product detail lookups (one flat object). Semantic search is
# NOT here — it runs as a deterministic function in run_turn, not as a tool.
SEARCH_TOOLS = {"search_products", "advanced_product_search",
                "get_product_by_sku", "get_product_by_id",
                "search_within_budget"}
MAX_CARDS = 8
RETRIEVE_K = 24   # retrieve wider than we show, so the model sees the real variety of kinds

# ponytail: in-memory per-session count of consecutive clarify turns — same throwaway
# state pattern as the guest-cart map; wiped on restart. Only feeds MAX_CLARIFY, the
# loop-breaker; it does NOT cap normal narrowing (that's the model's data-driven call).
MAX_CLARIFY = 5
_clarify_rounds: dict[str, int] = {}

# ponytail: cheap keyword gate — is this turn a product search (→ run semantic
# retrieval) or a cart/greeting/checkout turn (→ let the agent handle it)? A
# heuristic with a known ceiling; upgrade to an intent classifier if it misfires.
_NON_SEARCH_RE = re.compile(r"\b(cart|checkout|check\s*out|basket|invoice|order status)\b", re.I)
_CART_VERB_RE = re.compile(r"^\s*(add|remove|delete|buy|pay|checkout)\b", re.I)
# A short message STARTING with a greeting/pleasantry (e.g. "hello there", "good
# morning", "thanks!") isn't a product search. Bounded to ≤3 words so "hey do you
# have milk" still searches.
_GREETING_RE = re.compile(
    r"^(hi|hello|hey|hiya|yo|sup|howdy|greetings|thanks|thank you|thankyou|bye|goodbye|"
    r"good (morning|afternoon|evening|day)|ok|okay|yes|no)\b", re.I)


def _is_product_search(msg: str) -> bool:
    m = msg.strip().lower()
    if not m:
        return False
    if _GREETING_RE.match(m) and len(m.split()) <= 3:
        return False
    if _NON_SEARCH_RE.search(m) or _CART_VERB_RE.match(m):
        return False
    return True


def _cards_from_retrieved(retrieved: list[dict]) -> list[dict]:
    """Turn Qdrant payloads straight into cards (image is already in the payload,
    so no extra Magento call). Dedup by SKU, cap at MAX_CARDS."""
    seen, out = set(), []
    for p in retrieved:
        sku = p.get("sku")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        out.append({"sku": sku, "name": p.get("name"),
                    "price": p.get("price"), "image": p.get("image")})
        if len(out) >= MAX_CARDS:
            break
    return out


async def run_turn(username: str, user_message: str, session_id: str, guest: bool = False) -> dict:
    """Run one turn for a user's chat session. Returns {reply, products, cart}.
    Guests have no Magento token, so their cart lives app-side (guest_cart)."""
    agent = await get_agent()
    collected: dict[str, dict] = {}
    suggestions: list[str] = []  # tappable choices the agent offered (suggest_options)
    reply = "I wasn't able to finish that — could you try rephrasing?"
    cart_added = False  # did an add_to_cart run this turn? → UI shows next-step buttons

    # Deterministic product retrieval (RAG): on a product-search turn we run
    # semantic search ourselves and hand the model the results as grounding — no
    # trusting it to call a search tool. The model then either narrows (broad,
    # diverse results → ask a question + chips, per rule #0) or recommends
    # (coherent results → show cards). See rule #0 in prompts/system.py.
    key = f"{username}:{session_id}"
    is_search = _is_product_search(user_message)
    retrieved: list[dict] = []
    message_for_agent = user_message
    if is_search:
        try:
            retrieved = await vector_service.search_similar(user_message, top_k=RETRIEVE_K)
        except Exception as e:  # noqa: BLE001 — search down must not crash the turn
            logger.warning("semantic search failed: %s", e)
        if retrieved:
            catalog = json.dumps(
                [{"sku": p.get("sku"), "name": p.get("name"), "price": p.get("price")}
                 for p in retrieved], ensure_ascii=False)
            forced = _clarify_rounds.get(key, 0) >= MAX_CLARIFY
            note = ("[Retrieved products for this query — follow rule #0. "
                    + ("You've narrowed enough: SHOW products from these now, do NOT ask "
                       "another question.]" if forced
                       else "Ask a narrowing question ONLY if these are clearly different "
                            "kinds; otherwise recommend from them.]"))
            message_for_agent = f"{user_message}\n\n{note}\n{catalog}"
        logger.info("run_turn: product search, retrieved %d products (rounds=%d)",
                    len(retrieved), _clarify_rounds.get(key, 0))

    run_config = {"configurable": {"thread_id": f"{username}:{session_id}"}}
    if handler := _tracer():
        # Groups traces by user + chat session in the Langfuse UI.
        run_config["callbacks"] = [handler]
        run_config["metadata"] = {"langfuse_user_id": username,
                                  "langfuse_session_id": session_id}

    async for update in agent.astream(
        {"messages": [{"role": "user", "content": message_for_agent}]},
        # thread_id scopes the checkpointer's history: per user, per chat session.
        config=run_config,
        context=UserContext(username=username, guest=guest),
        stream_mode="updates",
    ):
        for payload in update.values():
            for msg in (payload or {}).get("messages", []):
                mtype = getattr(msg, "type", None)
                if mtype == "tool" and getattr(msg, "name", None) in SEARCH_TOOLS:
                    _collect_products(_text(msg.content), collected)
                elif mtype == "tool" and getattr(msg, "name", None) == "add_to_cart":
                    cart_added = True
                elif mtype == "tool" and getattr(msg, "name", None) == "suggest_options":
                    try:  # last call this turn wins; ignore a [tool error] string
                        suggestions = json.loads(_text(msg.content)).get("options", []) or suggestions
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass
                elif mtype == "ai" and msg.content and not getattr(msg, "tool_calls", None):
                    reply = _text(msg.content).strip()

    # A clarify turn (agent offered chips) shows NO cards — the chips ARE the turn.
    # Otherwise cards come from our deterministic retrieval, or from any tool the
    # agent called (cart flows, exact-SKU lookups, fallbacks).
    if suggestions:
        products = []
    elif retrieved:
        products = _cards_from_retrieved(retrieved)
    else:
        products = await _enrich(collected)

    # Track consecutive clarify turns for the MAX_CLARIFY loop-breaker only.
    if is_search:
        _clarify_rounds[key] = _clarify_rounds.get(key, 0) + 1 if suggestions else 0

    cart = await asyncio.to_thread((guest_cart if guest else cartmod).view, username)
    return {"reply": reply, "products": products,
            "cart": cart, "cart_added": cart_added, "suggestions": suggestions}


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
