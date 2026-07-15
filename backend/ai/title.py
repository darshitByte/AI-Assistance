"""Generate a short chat title from the first user message.

A fast, standalone LLM call with reasoning OFF — kept off the agent path so it
doesn't inherit the ~20s thinking latency. Best-effort: falls back to "New chat".
"""
from openai import AsyncOpenAI

from core import config
from core.log import logger

_client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

_SYSTEM = (
    "You name chat threads. Given the user's first message, reply with a 3-5 word "
    "title in Title Case that captures the topic. Plain text only: no quotes, no "
    "trailing punctuation, no explanation."
)


async def generate_session_name(message: str) -> str:
    try:
        resp = await _client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": message},
            ],
            temperature=0.3,
            max_tokens=24,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        name = (resp.choices[0].message.content or "").strip().strip('"').strip()
        return name[:60] or "New chat"
    except Exception as e:  # noqa: BLE001 — title is best-effort, never break the chat
        logger.warning("title gen failed: %s", e)
        return "New chat"
