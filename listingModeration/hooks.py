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

import logging
from typing import Any, Optional
from uuid import UUID

from . import service
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
) -> None:
    """Enqueue a product row (as returned from the products table) for moderation.

    Safe to call from a hot request path — never raises. The cron drain picks
    up the row within a minute.
    """
    try:
        payload = SubmitListingRequest(
            product_id=_coerce_uuid(product_row.get("id")),
            seller_id=_coerce_uuid(seller_id),
            title=str(product_row.get("title", "") or ""),
            description=str(product_row.get("description", "") or ""),
            image_urls=_extract_image_urls(product_row.get("image_urls")),
        )
        service.enqueue(payload)
    except Exception as exc:
        logger.warning(
            "moderation enqueue failed for product %s: %s",
            product_row.get("id"),
            exc,
        )
