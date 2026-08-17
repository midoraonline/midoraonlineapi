"""Helpers for publishing product lifecycle events from the shop routes."""
from __future__ import annotations

from typing import Any

from common.events import Events, ProductPostedEvent, get_event_bus
from shop.schemas import ProductUpdate

# Edits to these fields reset the listing to pending_review. Kept here so
# the service (status reset) and the route (which event to emit) cannot drift.
CONTENT_MODERATION_FIELDS = frozenset({"title", "description", "image_urls", "category"})


def product_update_requires_moderation(body: ProductUpdate) -> bool:
    return bool(CONTENT_MODERATION_FIELDS & body.model_dump(exclude_unset=True).keys())


async def publish_product_created(product: dict[str, Any], *, seller_id: str | None) -> None:
    # Order matters on Vercel: enqueue the moderation row first (cheap DB
    # insert) so it survives even if a later listener burns the whole
    # function budget. Then fan out to fast subscribers (mail, ranking,
    # embeddings). Finally trigger the inline moderation pipeline — it
    # gets whatever budget remains, and if it's killed the cron drain
    # recovers the already-enqueued row.
    payload = ProductPostedEvent.from_product(product, seller_id=seller_id)
    bus = get_event_bus()
    if payload.status == "pending_review":
        await bus.emit(Events.PRODUCT_PENDING_REVIEW, payload)
    await bus.emit(Events.PRODUCT_CREATED, payload)
    if payload.status == "pending_review":
        await bus.emit(Events.PRODUCT_MODERATE_NOW, payload)


async def publish_product_updated(
    product: dict[str, Any],
    *,
    seller_id: str | None,
    requires_moderation: bool,
) -> None:
    payload = ProductPostedEvent.from_product(product, seller_id=seller_id)
    bus = get_event_bus()
    if requires_moderation and payload.status == "pending_review":
        await bus.emit(Events.PRODUCT_PENDING_REVIEW, payload)
    await bus.emit(Events.PRODUCT_UPDATED, payload)
    if requires_moderation and payload.status == "pending_review":
        await bus.emit(Events.PRODUCT_MODERATE_NOW, payload)
