from __future__ import annotations

from typing import Any

from feed.stages import lane_of
from shop.publish_gates import count_photos


def _text_len(value: Any) -> int:
    return len(str(value or "").strip())


def _media_points(product: dict[str, Any], lane: str) -> float:
    if lane != "product":
        return 30.0
    images = product.get("image_urls")
    photos = count_photos(images if isinstance(images, list) else [])
    if photos >= 2:
        return 30.0
    return 20.0 if photos == 1 else 0.0


def _value_points(product: dict[str, Any], lane: str) -> float:
    price = float(product.get("price_ugx") or 0)
    meta = product.get("listing_meta") if isinstance(product.get("listing_meta"), dict) else {}
    if lane == "product":
        return 15.0 if price > 0 else 0.0
    if lane == "service":
        return 15.0 if price > 0 or meta.get("pricing_model") == "quote" else 8.0
    return 15.0 if price > 0 or meta.get("deadline") or meta.get("compensation") else 8.0


def listing_quality(product: dict[str, Any]) -> float:
    lane = lane_of(product.get("item_type"))
    title = _text_len(product.get("title"))
    description = _text_len(product.get("description"))
    score = _media_points(product, lane) + _value_points(product, lane)
    score += 20.0 if title >= 10 else 10.0 if title >= 3 else 0.0
    score += 20.0 if description >= 80 else 10.0 if description >= 30 else 0.0
    score += 10.0 if _text_len(product.get("location_name")) else 0.0
    score += 5.0 if _text_len(product.get("category")) else 0.0
    return score
