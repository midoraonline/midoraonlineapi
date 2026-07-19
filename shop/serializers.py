"""Row → response mappers for shop-related endpoints.

Centralises the field projection that used to be inlined in every
`/products/trending`, `/products/premium`, `/products/me/liked` handler.

Keeping this in a single place means the frontend contract can't drift
between endpoints (e.g. "why does trending have `shop_name` but liked
returns `owner_name`?"). If a new card field is needed, add it here once.
"""
from __future__ import annotations

from typing import Any

from shop.schemas import ProductCard


def _normalise_image_urls(raw: Any) -> list[str]:
    """Accept list[str] or comma-separated str; return a clean list."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x]
    if isinstance(raw, str) and raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def serialize_product_card(
    product_row: dict[str, Any],
    *,
    shop_row: dict[str, Any] | None = None,
    average_rating: float = 0.0,
    review_count: int = 0,
    first_image_only: bool = False,
) -> ProductCard:
    """Project a raw Supabase `products` row (+ optional shop row) into a `ProductCard`.

    Args:
        product_row: The raw dict returned by Supabase for the product.
        shop_row: The raw shop row when embedding shop context; may be ``None``.
        average_rating: Pre-computed average, or ``0.0`` if not requested.
        review_count: Pre-computed count, or ``0`` if not requested.
        first_image_only: Trending/liked lists only need a thumbnail — pass
            ``True`` to include just the first image.
    """
    shop = shop_row or {}
    imgs = _normalise_image_urls(product_row.get("image_urls"))
    if first_image_only:
        imgs = imgs[:1]

    return ProductCard(
        id=str(product_row.get("id", "")),
        shop_id=str(product_row.get("shop_id", "")),
        title=str(product_row.get("title") or ""),
        description=product_row.get("description"),
        price_ugx=float(product_row.get("price_ugx") or 0),
        discount_price=(
            float(product_row["discount_price"])
            if product_row.get("discount_price") is not None
            else None
        ),
        discount_expires_at=(
            str(product_row["discount_expires_at"])
            if product_row.get("discount_expires_at")
            else None
        ),
        image_urls=imgs or None,
        category=product_row.get("category"),
        item_type=str(product_row.get("item_type") or "product"),
        status=str(product_row.get("status") or "active"),
        listing_score=int(product_row.get("listing_score") or 0),
        location_name=product_row.get("location_name"),
        is_published=bool(product_row.get("is_published", True)),
        is_negotiable=product_row.get("is_negotiable", True) is not False,
        view_count=int(product_row.get("view_count") or 0),
        created_at=str(product_row["created_at"]) if product_row.get("created_at") else None,
        average_rating=average_rating,
        review_count=review_count,
        shop_name=shop.get("name"),
        shop_slug=shop.get("slug"),
        shop_whatsapp=shop.get("whatsapp_number") or None,
        owner_id=str(shop["owner_id"]) if shop.get("owner_id") else None,
        shop_is_active=bool(shop.get("is_active", True)),
        shop_trust_badges=list(shop.get("trust_badges") or []),
        shop_available_now=bool(shop.get("available_now", False)),
    )
