"""Publish-time trust gates for listings (Phase 1 Must).

Enforced on create/update/toggle when `is_published` is true so clients cannot
bypass the FE form. Draft (unpublished) listings stay loose.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

# Still photos required to publish; videos do not count toward the minimum.
MIN_PUBLISH_PHOTOS = 2
MAX_LISTING_MEDIA = 3

# Quote-style (price 0) allowed only for these kinds.
QUOTE_OK_ITEM_TYPES = frozenset({"service", "job", "opportunity"})

_VIDEO_HINTS = (".mp4", ".webm", ".mov", ".m4v", ".avi", "/video/")


def _http_gate(message: str, code: str) -> None:
    raise HTTPException(status_code=400, detail={"detail": message, "code": code})


def is_video_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return any(h in u for h in _VIDEO_HINTS)


def count_photos(image_urls: list[str] | None) -> int:
    if not image_urls:
        return 0
    return sum(1 for u in image_urls if isinstance(u, str) and u.strip() and not is_video_url(u))


def count_media(image_urls: list[str] | None) -> int:
    if not image_urls:
        return 0
    return sum(1 for u in image_urls if isinstance(u, str) and u.strip())


def _location_display(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("display", "label", "city", "name"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        city = (raw.get("city") or "").strip() if isinstance(raw.get("city"), str) else ""
        country = (raw.get("country") or "").strip() if isinstance(raw.get("country"), str) else ""
        if city and country:
            return f"{city}, {country}"
        return city or country
    return str(raw).strip()


def location_is_usable(location_name: str | None, shop_location: Any) -> bool:
    """Require a real place — bare country 'Uganda' alone is not enough for publish."""
    loc = (location_name or "").strip()
    if not loc:
        loc = _location_display(shop_location)
    if not loc:
        return False
    normalized = loc.lower().strip(" ,.")
    if normalized in {"uganda", "ug", "online", "online shop"}:
        return False
    return True


def assert_media_limits(image_urls: list[str] | None, *, publishing: bool) -> None:
    media = count_media(image_urls)
    if media > MAX_LISTING_MEDIA:
        _http_gate(
            f"At most {MAX_LISTING_MEDIA} photos or videos per listing.",
            "too_many_media",
        )
    if publishing and count_photos(image_urls) < MIN_PUBLISH_PHOTOS:
        _http_gate(
            f"Add at least {MIN_PUBLISH_PHOTOS} photos before publishing (videos alone are not enough).",
            "photos_required",
        )


def assert_price_for_publish(price_ugx: float | None, item_type: str | None) -> None:
    kind = (item_type or "product").strip().lower()
    price = float(price_ugx or 0)
    if kind in QUOTE_OK_ITEM_TYPES:
        if price < 0:
            _http_gate("Price cannot be negative.", "invalid_price")
        return
    if price <= 0:
        _http_gate(
            "Set a price greater than 0 to publish this listing (or switch to a quote-style category).",
            "price_required",
        )


def assert_owner_phone_verified(client: Any, user_id: str) -> None:
    r = (
        client.table("users")
        .select("phone_verified")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not r.data or not bool(r.data[0].get("phone_verified")):
        _http_gate(
            "Verify your phone number before publishing a listing.",
            "phone_verification_required",
        )


def assert_can_publish(
    client: Any,
    *,
    user_id: str,
    shop_id: str,
    image_urls: list[str] | None,
    price_ugx: float | None,
    item_type: str | None,
    location_name: str | None,
    category: str | None,
    description: str | None,
) -> None:
    """Hard gates for a listing that will be publicly published."""
    assert_owner_phone_verified(client, user_id)
    assert_media_limits(image_urls, publishing=True)
    assert_price_for_publish(price_ugx, item_type)

    if not (category or "").strip():
        _http_gate("Pick a category before publishing.", "category_required")

    desc = (description or "").strip()
    if len(desc) < 40:
        _http_gate(
            "Write a fuller description (at least ~40 characters) before publishing.",
            "description_required",
        )
    sentences = [s.strip() for s in re.split(r"[.!?]+", desc) if len(s.strip()) >= 8]
    if len(sentences) < 2:
        _http_gate(
            "Use at least two clear sentences in the description before publishing.",
            "description_required",
        )

    shop_r = (
        client.table("shops")
        .select("location")
        .eq("id", shop_id)
        .limit(1)
        .execute()
    )
    shop_location = shop_r.data[0].get("location") if shop_r.data else None
    if not location_is_usable(location_name, shop_location):
        _http_gate(
            "Add a real location (city/area) on the listing or shop before publishing — country-only is not enough.",
            "location_required",
        )


def assert_owner_phone_for_whatsapp(client: Any, owner_id: str | None) -> bool:
    """Return True when the seller's phone is verified (WhatsApp CTA allowed)."""
    if not owner_id:
        return False
    try:
        r = (
            client.table("users")
            .select("phone_verified")
            .eq("id", owner_id)
            .limit(1)
            .execute()
        )
        return bool(r.data and r.data[0].get("phone_verified"))
    except Exception:
        return False
