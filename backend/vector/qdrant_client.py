"""The single Qdrant client, created in main.py's lifespan and reused app-wide.

Mirrors the MCP client's lifecycle: connect on startup, close on shutdown. Kept
tolerant at the call site — if Qdrant is down the app still boots (semantic
search just errors gracefully), same spirit as the guest-cart / MCP fallbacks.
"""
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

from core import config
from core.log import logger


class QdrantClientManager:
    def __init__(self) -> None:
        self.client: AsyncQdrantClient | None = None

    async def connect(self) -> None:
        client = AsyncQdrantClient(url=config.QDRANT_URL)
        existing = {c.name for c in (await client.get_collections()).collections}
        if config.QDRANT_COLLECTION not in existing:
            await client.create_collection(
                config.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=config.EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
            )
            logger.info("qdrant: created collection %r", config.QDRANT_COLLECTION)
        self.client = client
        logger.info("qdrant: connected at %s", config.QDRANT_URL)

    async def disconnect(self) -> None:
        if self.client:
            await self.client.close()
            self.client = None


qdrant_manager = QdrantClientManager()
