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
        invalidate_feed_caches()
    except Exception as exc:  # noqa: BLE001
        logger.info("feed invalidate failed: %s", exc)
    try:
        refresh_product_embedding(event.product_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh embedding failed for %s: %s", event.product_id, exc)


def on_product_status_changed(event: object) -> None:
    """Approval moves a listing in or out of the feed, so the ranking must rebuild now."""
    from feed.invalidate import invalidate_feed_caches

    try:
        invalidate_feed_caches()
    except Exception as exc:  # noqa: BLE001
        logger.info("feed invalidate on status change failed: %s", exc)
