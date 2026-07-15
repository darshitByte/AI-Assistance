"""Chat session metadata. Backed by the `sessions` collection.

One doc per chat: {username, session_id, session_name, created_at, updated_at}.
A row is created (placeholder name) when the user starts a new chat, then renamed
from an AI summary of their first message.
"""
from datetime import datetime, timezone

from core.log import logger
from db.mongo import sessions


def create_session(username: str, session_id: str) -> None:
    """Register a new chat, idempotently — never overwrites an existing one."""
    try:
        now = datetime.now(timezone.utc)
        sessions.update_one(
            {"username": username, "session_id": session_id},
            {
                "$setOnInsert": {
                    "username": username,
                    "session_id": session_id,
                    "session_name": "New chat",
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001 — session metadata is best-effort for the POC
        logger.warning("session create failed: %s", e)


def set_name(username: str, session_id: str, name: str) -> None:
    try:
        sessions.update_one(
            {"username": username, "session_id": session_id},
            {"$set": {"session_name": name, "updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("session rename failed: %s", e)


def touch(username: str, session_id: str) -> None:
    """Bump updated_at so the chat sorts to the top of the list."""
    try:
        sessions.update_one(
            {"username": username, "session_id": session_id},
            {"$set": {"updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("session touch failed: %s", e)


def delete_session(username: str, session_id: str) -> None:
    try:
        sessions.delete_one({"username": username, "session_id": session_id})
    except Exception as e:  # noqa: BLE001
        logger.warning("session delete failed: %s", e)


def list_sessions(username: str, limit: int = 100) -> list[dict]:
    docs = (
        sessions.find({"username": username}, {"_id": 0, "username": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return list(docs)
