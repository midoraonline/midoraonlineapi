"""Event publisher helpers for domain status changes."""
from __future__ import annotations

from typing import Any

from common.events.bus import get_event_bus
from common.events.names import Events
from common.events.payloads import ProductStatusChangedEvent, ShopVerificationChangedEvent


async def publish_product_status_changed(
    product: dict[str, Any],
    new_status: str,
    *,
    previous_status: str | None = None,
    seller_id: str | None = None,
    reason: str | None = None,
) -> ProductStatusChangedEvent:
    """Emit PRODUCT_STATUS_CHANGED event when a product status transitions."""
    payload = ProductStatusChangedEvent(
        product_id=str(product.get("id") or ""),
        shop_id=str(product.get("shop_id") or "") if product.get("shop_id") else None,
        seller_id=seller_id,
        previous_status=previous_status or product.get("status"),
        new_status=new_status,
        title=str(product.get("title") or ""),
        reason=reason or product.get("review_notes"),
        product=product,
    )
    await get_event_bus().emit(Events.PRODUCT_STATUS_CHANGED, payload)
    return payload


async def publish_shop_verification_changed(
    shop_id: str,
    new_status: str,
    *,
    owner_id: str | None = None,
    stage: int = 2,
    previous_status: str | None = None,
    reason: str | None = None,
    shop: dict[str, Any] | None = None,
) -> ShopVerificationChangedEvent:
    """Emit SHOP_VERIFICATION_CHANGED event when a shop verification status updates."""
    payload = ShopVerificationChangedEvent(
        shop_id=shop_id,
        owner_id=owner_id,
        stage=stage,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        shop=shop or {},
    )
    await get_event_bus().emit(Events.SHOP_VERIFICATION_CHANGED, payload)
    return payload
