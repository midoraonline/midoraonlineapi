"""Integration hook other modules use to submit products for moderation.

Kept out of `service.py` so callers only need this narrow surface and avoid
pulling in the whole pipeline (Gemini, Pillow) at import time in the hot
request path.

`enqueue_product` is fire-and-forget from the caller's perspective: any
failure is logged but never propagated. A dropped enqueue means the product
stays `pending_review` and a later manual re-enqueue (or an admin action)
recovers it — that's the same failure mode as our mail queue.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import UUID

from . import service
from .config import config
from .schemas import SubmitListingRequest

logger = logging.getLogger(__name__)


def _coerce_uuid(value: Any) -> Optional[UUID]:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _extract_image_urls(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(u) for u in raw if u and str(u).strip()]


def enqueue_product(
    product_row: dict[str, Any],
    seller_id: str | UUID | None = None,
) -> Optional[UUID]:
    """Enqueue a product row (as returned from the products table) for moderation.

    Safe to call from a hot request path — never raises. Returns the queue
    row id when the enqueue succeeded so an async caller can drive the
    pipeline synchronously via `moderate_now`. On Vercel this is the only
    way to guarantee the pipeline completes: `BackgroundTasks` /
    `loop.create_task` are dropped as soon as the response ships, and cron
    is unreliable on Hobby (once-per-day cap) or when `CRON_SECRET` is
    missing (drain returns 503).
    """
    try:
        payload = SubmitListingRequest(
            product_id=_coerce_uuid(product_row.get("id")),
            seller_id=_coerce_uuid(seller_id),
            title=str(product_row.get("title", "") or ""),
            description=str(product_row.get("description", "") or ""),
            image_urls=_extract_image_urls(product_row.get("image_urls")),
        )
        row = service.enqueue(payload)
    except Exception as exc:
        logger.warning(
            "moderation enqueue failed for product %s: %s",
            product_row.get("id"),
            exc,
        )
        return None
    return row.id


async def moderate_now(row_id: UUID) -> None:
    """Run the moderation pipeline synchronously for a single queued row.

    Bounded by `config.inline_timeout_seconds` so a slow Gemini call cannot
    blow the caller's serverless function budget. On timeout / error the
    row stays `pending` and the cron drain retries it.

    Callers must `await` this from an async context (typically a FastAPI
    route handler). Do NOT wrap this in `BackgroundTasks` or
    `loop.create_task` — see the module docstring for why.
    """
    # Local import so callers that only enqueue don't pay for Pillow /
    # google-genai / httpx at import time.
    from . import pipeline

    try:
        await asyncio.wait_for(
            pipeline.process_row(row_id),
            timeout=config.inline_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.info(
            "inline moderation timed out for row %s after %.1fs; cron drain will retry",
            row_id,
            config.inline_timeout_seconds,
        )
    except Exception as exc:
        logger.warning("inline moderation failed for row %s: %s", row_id, exc)
