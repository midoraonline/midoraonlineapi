"""Subscribe to product write events instead of polling pending DB rows.

The product POST/PATCH route emits two events in order:

* `product.pending_review` — cheap: we insert a row into the moderation
  queue and return. Runs BEFORE the slow embedding refresh, so even when
  a serverless timeout kills the request mid-flight the row survives and
  the cron drain can recover it.
* `product.moderate_now` — expensive: we run the pipeline inline against
  the freshest pending row for the product. Runs LAST, so it only
  competes for whatever function budget mail/ranking/embed left behind.
"""
from __future__ import annotations

import logging

from common.events.payloads import ProductPostedEvent
from listingModeration.config import config
from listingModeration.hooks import enqueue_product, moderate_now
from listingModeration import service

logger = logging.getLogger(__name__)


async def on_product_pending_review(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return

    row_id = enqueue_product(event.product, seller_id=event.seller_id)
    if row_id is None:
        logger.warning("moderation enqueue returned no row for product %s", event.product_id)


async def on_product_moderate_now(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return
    if not config.inline_on_enqueue:
        return

    row = service.latest_pending_row_for_product(str(event.product_id))
    if row is None:
        # Enqueue must have failed earlier — cron drain / reconcile will
        # recover this later.
        logger.info(
            "no pending queue row for product %s; skipping inline moderation",
            event.product_id,
        )
        return

    await moderate_now(row.id)
