from __future__ import annotations

import logging

from common.events.payloads import ProductPostedEvent

logger = logging.getLogger(__name__)


def on_product_posted(event: ProductPostedEvent) -> None:
    if not event.product_id:
        return
    from feed.embeddings import refresh_product_embedding
    from feed.invalidate import invalidate_feed_caches

    try:
        invalidate_feed_caches(notify_next=False)
    except Exception as exc:  # noqa: BLE001
        logger.info("feed invalidate failed: %s", exc)
    try:
        refresh_product_embedding(event.product_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh embedding failed for %s: %s", event.product_id, exc)
