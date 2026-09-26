"""Enqueue + inline-run helpers used by the product.pending_review listener.

Kept out of `service.py` so callers only need this narrow surface and avoid
pulling in the whole pipeline (Gemini, Pillow) at import time.

`enqueue_product` is fire-and-forget from the caller's perspective: any
failure is logged but never propagated. A dropped enqueue means the product
stays `pending_review` and a later drain / admin action recovers it.
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
    row id. The product route awaits this insert, then runs `moderate_now`
    after the response. If that task is dropped, the moderation cron drains
    this row (Hobby cron is once a day, and a missing CRON_SECRET returns 503).
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
    row is escalated to `needs_review` so admins can decide; product stays
    `pending_review`.

    Product routes schedule this after the response is sent. A timeout or
    error still escalates the row to manual review. If that task is
    dropped, the moderation cron drains the row enqueued on the request.
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
            "inline moderation timed out for row %s after %.1fs; escalating to manual review",
            row_id,
            config.inline_timeout_seconds,
        )
        row = service.get_by_id(row_id)
        service.escalate_to_manual_review(
            row_id,
            "auto-moderation timed out — awaiting admin review",
            product_id=row.product_id if row else None,
        )
    except Exception as exc:
        logger.warning("inline moderation failed for row %s: %s", row_id, exc)
        row = service.get_by_id(row_id)
        service.escalate_to_manual_review(
            row_id,
            f"auto-moderation failed — awaiting admin review ({type(exc).__name__})",
            product_id=row.product_id if row else None,
        )
