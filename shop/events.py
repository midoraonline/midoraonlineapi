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


async def publish_product_event(
    event: str,
    product: dict[str, Any],
    *,
    seller_id: str | None,
) -> ProductPostedEvent:
    payload = ProductPostedEvent.from_product(product, seller_id=seller_id)
    await get_event_bus().emit(event, payload)
    return payload


async def publish_product_created(product: dict[str, Any], *, seller_id: str | None) -> None:
    payload = await publish_product_event(Events.PRODUCT_CREATED, product, seller_id=seller_id)
    if payload.status == "pending_review":
        await get_event_bus().emit(Events.PRODUCT_PENDING_REVIEW, payload)


async def publish_product_updated(
    product: dict[str, Any],
    *,
    seller_id: str | None,
    requires_moderation: bool,
) -> None:
    payload = await publish_product_event(Events.PRODUCT_UPDATED, product, seller_id=seller_id)
    if requires_moderation and payload.status == "pending_review":
        await get_event_bus().emit(Events.PRODUCT_PENDING_REVIEW, payload)
