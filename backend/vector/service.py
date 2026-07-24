"""Embed products and store / search them in Qdrant.

Embeddings come from Ollama (OpenAI-compatible `/v1/embeddings`, `mxbai-embed-large`,
1024-dim); endpoint / key / model all from env (config.EMBED_*). One vector per
product — texts are short, well under the model's limit, so no chunking.

Embed text = name + short_description + specification. `brand`/`category_ids`
come back from Magento as numeric option IDs (useless as text), and the brand
word + product type already appear in the name/specification, so they're skipped.
"""
import asyncio
import json
import re
import urllib.request

from qdrant_client.models import PointStruct

from commerce import magento
from core import config
from core.log import logger
from vector.qdrant_client import qdrant_manager

EMBED_BATCH = 20    # texts per NVIDIA /embeddings call
UPSERT_BATCH = 128  # points per Qdrant upsert
_TAG_RE = re.compile(r"<[^>]+>")


def _ca(p: dict, code: str):
    """Read a Magento custom attribute value by code (None if absent)."""
    for c in p.get("custom_attributes", []):
        if c.get("attribute_code") == code:
            return c.get("value")
    return None


def _strip(html) -> str:
    return _TAG_RE.sub(" ", html).strip() if isinstance(html, str) else ""


def _product_text(p: dict) -> str:
    parts = [p.get("name") or "",
             _strip(_ca(p, "short_description")),
             _strip(_ca(p, "specification"))]
    return "\n".join(x for x in parts if x).strip()


# mxbai wants this retrieval prompt on the QUERY only (not on stored passages).
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _embed(texts: list[str], is_query: bool) -> list[list[float]]:
    """Blocking POST to the Ollama OpenAI-compatible embeddings endpoint → one
    vector per text. Callers wrap this in asyncio.to_thread. Endpoint / key /
    model all come from env (config.EMBED_*)."""
    inputs = [_QUERY_PREFIX + t for t in texts] if is_query else texts
    body = json.dumps({"model": config.EMBED_MODEL, "input": inputs}).encode()
    req = urllib.request.Request(
        config.EMBED_BASE_URL.rstrip("/") + "/embeddings", data=body,
        headers={"Authorization": f"Bearer {config.EMBED_API_KEY}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return [d["embedding"] for d in data["data"]]


async def reindex_all() -> int:
    """Pull the full catalogue, embed it, and (re)upsert every product into Qdrant.
    Point id = Magento product id, so re-runs overwrite instead of duplicating."""
    client = qdrant_manager.client
    if client is None:
        raise RuntimeError("Qdrant is not connected")

    products = await asyncio.to_thread(magento.fetch_all_products)
    total = len(products)
    logger.info("reindex: fetched %d products — embedding in batches of %d", total, EMBED_BATCH)

    count = 0
    buffer: list[PointStruct] = []
    for i in range(0, total, EMBED_BATCH):
        batch = products[i:i + EMBED_BATCH]
        texts = [_product_text(p) for p in batch]
        vectors = await asyncio.to_thread(_embed, texts, False)
        for p, text, vec in zip(batch, texts, vectors):
            payload = {
                "document": text,
                "id": int(p["id"]),
                "sku": p.get("sku"),
                "name": p.get("name"),
                "price": p.get("price"),
                "status": p.get("status"),
                "image": magento._image_url(p.get("media_gallery_entries")),
            }
            count += 1
            # what actually goes into Qdrant for this record (payload JSON + vector size)
            logger.info("reindex: [%d/%d] point id=%s vec=%dd payload=%s",
                        count, total, payload["id"], len(vec), json.dumps(payload, ensure_ascii=False))
            buffer.append(PointStruct(id=int(p["id"]), vector=vec, payload=payload))
        if len(buffer) >= UPSERT_BATCH:
            await client.upsert(config.QDRANT_COLLECTION, points=buffer)
            logger.info("reindex: upserted %d/%d into Qdrant", count, total)
            buffer = []
    if buffer:
        await client.upsert(config.QDRANT_COLLECTION, points=buffer)
        logger.info("reindex: upserted %d/%d into Qdrant", count, total)

    logger.info("reindex: done — %d products indexed", count)
    return count


async def search_similar(query: str, top_k: int = 8) -> list[dict]:
    """Embed the query and return the closest products' payloads (shaped
    {sku,name,price,image,...} so the orchestrator harvests them as cards)."""
    client = qdrant_manager.client
    if client is None:
        raise RuntimeError("Qdrant is not connected")
    vector = (await asyncio.to_thread(_embed, [query], True))[0]
    res = await client.query_points(
        config.QDRANT_COLLECTION, query=vector, limit=top_k, with_payload=True)
    return [pt.payload for pt in res.points]
