"""Helpers for publishing product lifecycle events from the shop routes."""
from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks

from common.events import Events, ProductPostedEvent, get_event_bus
from shop.schemas import ProductUpdate

# Edits to these fields reset the listing to pending_review. Kept here so
# the service (status reset) and the route (which event to emit) cannot drift.
CONTENT_MODERATION_FIELDS = frozenset({"title", "description", "image_urls", "category"})


def product_update_requires_moderation(body: ProductUpdate) -> bool:
    return bool(CONTENT_MODERATION_FIELDS & body.model_dump(exclude_unset=True).keys())


async def _emit_followups(
    payload: ProductPostedEvent,
    *,
    created: bool,
    requires_moderation: bool,
) -> None:
    """Mail, ranking, embeddings, then moderation.

    Scheduled after the response so a slow pipeline cannot burn the 60s
    Vercel limit and make the client show a false failure. moderate_now
    still escalates to manual review on timeout. The queue row is inserted
    before this runs; the moderation cron drains it if the task is dropped.
    """
    bus = get_event_bus()
    await bus.emit(Events.PRODUCT_CREATED if created else Events.PRODUCT_UPDATED, payload)
    if requires_moderation and payload.status == "pending_review":
        await bus.emit(Events.PRODUCT_MODERATE_NOW, payload)


async def publish_product_created(
    product: dict[str, Any],
    *,
    seller_id: str | None,
    background: BackgroundTasks | None = None,
) -> None:
    payload = ProductPostedEvent.from_product(product, seller_id=seller_id)
    if payload.status == "pending_review":
        await get_event_bus().emit(Events.PRODUCT_PENDING_REVIEW, payload)
    if background is not None:
        background.add_task(_emit_followups, payload, created=True, requires_moderation=True)
        return
    await _emit_followups(payload, created=True, requires_moderation=True)


async def publish_product_updated(
    product: dict[str, Any],
    *,
    seller_id: str | None,
    requires_moderation: bool,
    background: BackgroundTasks | None = None,
) -> None:
    payload = ProductPostedEvent.from_product(product, seller_id=seller_id)
    if requires_moderation and payload.status == "pending_review":
        await get_event_bus().emit(Events.PRODUCT_PENDING_REVIEW, payload)
    if background is not None:
        background.add_task(
            _emit_followups,
            payload,
            created=False,
            requires_moderation=requires_moderation,
        )
        return
    await _emit_followups(payload, created=False, requires_moderation=requires_moderation)
