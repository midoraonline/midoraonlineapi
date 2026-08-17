"""Typed payloads for in-process events."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProductPostedEvent(BaseModel):
    """Emitted from the product create/update API route.

    `product_id` is the stable handle subscribers need. `product` is the
    row-shaped dict (`id`, `title`, `description`, `image_urls`, …) so
    moderation can enqueue without a second DB read of pending status.
    """

    product_id: str
    shop_id: str
    seller_id: str | None = None
    status: str = "pending_review"
    title: str = ""
    description: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    price_ugx: float = 0
    category: str | None = None
    product: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_product(
        cls,
        product: dict[str, Any],
        *,
        seller_id: str | None = None,
    ) -> ProductPostedEvent:
        raw_images = product.get("image_urls") or []
        if isinstance(raw_images, str):
            image_urls = [raw_images] if raw_images.strip() else []
        elif isinstance(raw_images, list):
            image_urls = [str(u) for u in raw_images if u]
        else:
            image_urls = []

        product_id = str(product.get("id") or "")
        shop_id = str(product.get("shop_id") or "")
        return cls(
            product_id=product_id,
            shop_id=shop_id,
            seller_id=seller_id,
            status=str(product.get("status") or "pending_review"),
            title=str(product.get("title") or ""),
            description=product.get("description"),
            image_urls=image_urls,
            price_ugx=float(product.get("price_ugx") or 0),
            category=product.get("category"),
            product=dict(product),
        )


class ProductStatusChangedEvent(BaseModel):
    """Emitted when a product's status changes (approved/active, rejected, pending_review, hidden, etc.)."""

    product_id: str
    shop_id: str | None = None
    seller_id: str | None = None
    previous_status: str | None = None
    new_status: str
    title: str = ""
    reason: str | None = None
    product: dict[str, Any] = Field(default_factory=dict)


class ShopVerificationChangedEvent(BaseModel):
    """Emitted when a shop's verification stage status changes (verified, rejected, pending)."""

    shop_id: str
    owner_id: str | None = None
    stage: int = 2
    previous_status: str | None = None
    new_status: str
    reason: str | None = None
    shop: dict[str, Any] = Field(default_factory=dict)

