from __future__ import annotations

from typing import Any

from feed.stages import lane_of
from shop.publish_gates import count_photos


def _text_len(value: Any) -> int:
    return len(str(value or "").strip())


def _meta(product: dict[str, Any]) -> dict[str, Any]:
    meta = product.get("listing_meta")
    return meta if isinstance(meta, dict) else {}


def _photos(product: dict[str, Any]) -> int:
    images = product.get("image_urls")
    return count_photos(images if isinstance(images, list) else [])


def _scaled(length: int, full: int, half: int, points: float) -> float:
    return points if length >= full else points / 2 if length >= half else 0.0


def _has(meta: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, list) and any(str(v).strip() for v in value):
            return True
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _product(p: dict[str, Any], meta: dict[str, Any]) -> float:
    photos = _photos(p)
    score = 30.0 if photos >= 2 else 20.0 if photos == 1 else 0.0
    score += _scaled(_text_len(p.get("title")), 10, 3, 10.0) + _scaled(_text_len(p.get("description")), 80, 30, 10.0)
    score += 20.0 if float(p.get("price_ugx") or 0) > 0 else 0.0
    score += 15.0 if _text_len(p.get("location_name")) or _has(meta, "delivery_areas", "nationwide_delivery") else 0.0
    stock = p.get("stock_quantity")
    score += 15.0 if stock is None or int(stock or 0) > 0 else 0.0
    return score


def _service(p: dict[str, Any], meta: dict[str, Any]) -> float:
    description = _text_len(p.get("description"))
    score = 20.0 if _photos(p) >= 1 else 10.0 if description >= 150 else 0.0
    score += _scaled(_text_len(p.get("title")), 10, 3, 10.0) + _scaled(description, 80, 30, 15.0)
    if _has(meta, "service_areas", "service_area", "remote_service"):
        score += 20.0
    elif _text_len(p.get("location_name")):
        score += 10.0
    score += 15.0 if float(p.get("price_ugx") or 0) > 0 or _has(meta, "pricing_model") else 0.0
    score += 10.0 + (10.0 if _has(meta, "availability") else 0.0)
    return score


def _opportunity(p: dict[str, Any], meta: dict[str, Any]) -> float:
    score = _scaled(_text_len(p.get("title")), 10, 3, 10.0) + _scaled(_text_len(p.get("description")), 80, 30, 15.0)
    score += 10.0 + (10.0 if _has(meta, "opportunity_kind", "employment_type", "employment", "organisation") else 0.0)
    score += 15.0 if _text_len(p.get("location_name")) or _has(meta, "work_mode") else 0.0
    score += 15.0 if _has(meta, "deadline") else 0.0
    score += 15.0 if _text_len(meta.get("requirements")) >= 10 else 0.0
    score += 10.0
    return score


def listing_quality(product: dict[str, Any]) -> float:
    lane = lane_of(product.get("item_type"))
    meta = _meta(product)
    if lane == "service":
        return _service(product, meta)
    if lane == "opportunity":
        return _opportunity(product, meta)
    return _product(product, meta)
