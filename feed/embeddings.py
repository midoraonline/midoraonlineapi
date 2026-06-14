"""Product text vectorization for semantic feed ranking."""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768

# Fields that contribute to the embedding document string.
EMBEDDING_SOURCE_FIELDS = frozenset(
    {"title", "description", "category", "item_type", "ai_seo_tags"}
)


def build_product_embedding_text(product: dict[str, Any]) -> str:
    """Compose a single document string from product fields for embedding."""
    parts: list[str] = []
    title = (product.get("title") or "").strip()
    if title:
        parts.append(title)

    category = (product.get("category") or "").strip()
    if category:
        parts.append(f"Category: {category}")

    item_type = (product.get("item_type") or "").strip()
    if item_type:
        parts.append(f"Type: {item_type}")

    description = (product.get("description") or "").strip()
    if description:
        parts.append(description)

    tags = (product.get("ai_seo_tags") or "").strip()
    if tags:
        parts.append(tags)

    return "\n".join(parts)


def embedding_source_hash(product: dict[str, Any]) -> str:
    text = build_product_embedding_text(product)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_genai_client():
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai  # type: ignore

        return genai.Client(api_key=settings.gemini_api_key)
    except Exception as exc:
        logger.warning("Failed to init Gemini client for embeddings: %s", exc)
        return None


def embed_text(text: str, *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float] | None:
    """Generate an embedding vector for the given text."""
    cleaned = text.strip()
    if not cleaned:
        return None

    client = _get_genai_client()
    if client is None:
        return None

    settings = get_settings()
    model = settings.gemini_embedding_model

    try:
        from google.genai import types  # type: ignore

        response = client.models.embed_content(
            model=model,
            contents=cleaned,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIM,
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            return None
        values = getattr(embeddings[0], "values", None)
        if not values:
            return None
        return [float(v) for v in values]
    except Exception as exc:
        logger.warning("embed_text failed: %s", exc)
        return None


def embed_query(text: str) -> list[float] | None:
    """Embed a search query (optimized for retrieval matching)."""
    return embed_text(text, task_type="RETRIEVAL_QUERY")


def parse_embedding(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        try:
            vec = [float(v) for v in raw]
            return vec if vec else None
        except (TypeError, ValueError):
            return None
    return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def weighted_average(vectors: list[tuple[list[float], float]]) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0][0])
    acc = [0.0] * dim
    total_w = 0.0
    for vec, weight in vectors:
        if len(vec) != dim or weight <= 0:
            continue
        total_w += weight
        for i, value in enumerate(vec):
            acc[i] += value * weight
    if total_w <= 0:
        return None
    return [value / total_w for value in acc]


def vectorize_product(client: Any, product_id: str) -> bool:
    """Compute and persist the embedding for a single product."""
    try:
        resp = client.table("products").select("*").eq("id", product_id).limit(1).execute()
    except Exception as exc:
        logger.warning("vectorize_product fetch failed (%s): %s", product_id, exc)
        return False

    if not resp.data:
        return False

    product = resp.data[0]
    text = build_product_embedding_text(product)
    if not text:
        return False

    source_hash = embedding_source_hash(product)
    if product.get("embedding_source_hash") == source_hash and product.get("embedding"):
        return True

    vector = embed_text(text, task_type="RETRIEVAL_DOCUMENT")
    if vector is None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table("products").update(
            {
                "embedding": vector,
                "embedding_source_hash": source_hash,
                "embedding_updated_at": now,
            }
        ).eq("id", product_id).execute()
        return True
    except Exception as exc:
        logger.warning("vectorize_product save failed (%s): %s", product_id, exc)
        return False


def schedule_vectorize_product(product_id: str) -> None:
    """Fire-and-forget embedding refresh so API handlers stay fast."""

    def _run() -> None:
        try:
            from db.supabase import get_supabase_admin

            vectorize_product(get_supabase_admin(), product_id)
        except Exception as exc:
            logger.warning("background vectorize failed (%s): %s", product_id, exc)

    threading.Thread(target=_run, daemon=True).start()


def refresh_product_embedding(product_id: str) -> None:
    """Refresh a product embedding after create, update, or publish.

    Safe to call on every mutation — ``vectorize_product`` skips the Gemini
    API call when ``embedding_source_hash`` already matches the current text.
    """
    schedule_vectorize_product(product_id)


def backfill_missing_embeddings(client: Any, *, limit: int = 50) -> dict[str, int]:
    """Embed active products that are missing or stale. Returns counts."""
    try:
        resp = (
            client.table("products")
            .select("id,title,description,category,item_type,ai_seo_tags,embedding,embedding_source_hash")
            .eq("status", "active")
            .eq("is_published", True)
            .limit(limit * 3)
            .execute()
        )
    except Exception as exc:
        logger.warning("backfill fetch failed: %s", exc)
        return {"processed": 0, "updated": 0, "skipped": 0}

    processed = 0
    updated = 0
    skipped = 0

    for row in resp.data or []:
        if processed >= limit:
            break
        processed += 1
        source_hash = embedding_source_hash(row)
        if row.get("embedding") and row.get("embedding_source_hash") == source_hash:
            skipped += 1
            continue
        if vectorize_product(client, str(row["id"])):
            updated += 1
        else:
            skipped += 1

    return {"processed": processed, "updated": updated, "skipped": skipped}
