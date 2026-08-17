"""Recalculate listing score when a product is written."""
from __future__ import annotations

import logging

from common.events.payloads import ProductPostedEvent

logger = logging.getLogger(__name__)


def on_product_posted(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return
    from ranking.service import calculate_listing_score
    try:
        calculate_listing_score(event.product_id)
    except Exception as exc:
        logger.warning("listing score failed for %s: %s", event.product_id, exc)
