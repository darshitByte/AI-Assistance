"""Chat message persistence. Backed by the `messages` collection."""
from datetime import datetime, timezone

from core.log import logger
from db.mongo import messages


def save_message(username: str, session_id: str, role: str, content: str) -> None:
    try:
        now = datetime.now(timezone.utc)
        messages.insert_one(
            {
                "username": username,
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": now,
                "updated_at": now,
            }
        )
    except Exception as e:  # noqa: BLE001 — persistence is best-effort for the POC
        logger.warning("message save failed: %s", e)


def delete_messages(username: str, session_id: str) -> None:
    try:
        messages.delete_many({"username": username, "session_id": session_id})
    except Exception as e:  # noqa: BLE001
        logger.warning("message delete failed: %s", e)


def has_messages(username: str, session_id: str) -> bool:
    return messages.count_documents({"username": username, "session_id": session_id}, limit=1) > 0


def get_messages(username: str, session_id: str, limit: int = 200) -> list[dict]:
    docs = (
        messages.find({"username": username, "session_id": session_id}, {"_id": 0})
        .sort("created_at", 1)
        .limit(limit)
    )
    return list(docs)
