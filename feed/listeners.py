"""Refresh product embeddings when a listing is written."""
from __future__ import annotations

import logging

from common.events.payloads import ProductPostedEvent

logger = logging.getLogger(__name__)


def on_product_posted(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return
    from feed.embeddings import refresh_product_embedding
    try:
        refresh_product_embedding(event.product_id)
    except Exception as exc:
        logger.warning("refresh embedding failed for %s: %s", event.product_id, exc)
