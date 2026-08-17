"""Subscribe to product write events instead of polling pending DB rows.

The product POST/PATCH route emits `product.pending_review` with the
product id. This listener enqueues that id and (when configured) runs
the pipeline inline — no `latest_pending_row_for_product` lookup.
"""
from __future__ import annotations

import logging

from common.events.payloads import ProductPostedEvent
from listingModeration.config import config
from listingModeration.hooks import enqueue_product, moderate_now

logger = logging.getLogger(__name__)


async def on_product_pending_review(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return

    row_id = enqueue_product(event.product, seller_id=event.seller_id)
    if row_id is None:
        logger.warning("moderation enqueue returned no row for product %s", event.product_id)
        return

    if not config.inline_on_enqueue:
        return

    await moderate_now(row_id)
