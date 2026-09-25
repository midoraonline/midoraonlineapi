"""Listing location rules: physical places and Online (remote)."""

from __future__ import annotations

import math
from typing import Any

# Stored display value for a remote listing. No coordinates.
ONLINE_LOCATION_NAME = "Online"

_ONLINE_NAMES = frozenset({
    "online",
    "online shop",
    "remote",
    "remote work",
    "remote / online",
})

_COUNTRY_ONLY = frozenset({"uganda", "ug"})

_COORD_KEYS = ("lat", "lng", "latitude", "longitude")


def _clean(value: str | None) -> str:
    return (value or "").strip().strip(" ,.").lower()


def is_online_location(location_name: str | None) -> bool:
    return _clean(location_name) in _ONLINE_NAMES


def listing_is_online(location_name: str | None, listing_meta: Any = None) -> bool:
    if is_online_location(location_name):
        return True
    if isinstance(listing_meta, dict) and (
        listing_meta.get("is_online") is True or listing_meta.get("remote") is True
    ):
        return True
    return False


def apply_online_location(
    location_name: str | None,
    listing_meta: dict | None,
    *,
    is_online: bool | None = None,
) -> tuple[str | None, dict | None]:
    """Normalize the frontend location pair.

    Online: ``location_name="Online"`` and ``is_online=true`` (no coordinates).
    Physical: ``location_name="<place>"`` and ``is_online=false``.
    Older names (``online``, ``Online Shop``) count as Online when the flag is omitted.
    """
    if is_online is None and location_name is None:
        return None, listing_meta
    online = is_online if is_online is not None else is_online_location(location_name)
    meta = dict(listing_meta) if isinstance(listing_meta, dict) else {}
    if online:
        meta["is_online"] = True
        meta["remote"] = True
        for key in _COORD_KEYS:
            meta.pop(key, None)
        return ONLINE_LOCATION_NAME, meta
    name = location_name.strip() if isinstance(location_name, str) else location_name
    if isinstance(listing_meta, dict) or is_online is False:
        meta["is_online"] = False
        meta["remote"] = False
        return name, meta
    return name, listing_meta


def shop_coordinates(location: Any) -> tuple[float, float] | None:
    """Lat/lng for a physical shop. Online and unusable values return None."""
    if not isinstance(location, dict):
        return None
    display = ""
    for key in ("display", "label", "name", "city"):
        raw = location.get(key)
        if isinstance(raw, str) and raw.strip():
            display = raw
            break
    if location.get("is_online") or location.get("remote") or is_online_location(display):
        return None
    try:
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            return None
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def listing_within_radius(
    listing: dict[str, Any],
    shop_location: Any,
    lat: float,
    lng: float,
    radius_km: float,
) -> bool:
    """Near-me match. Online listings never match, even if the shop has coordinates."""
    if listing_is_online(listing.get("location_name"), listing.get("listing_meta")):
        return False
    coords = shop_coordinates(shop_location)
    if coords is None:
        meta = listing.get("listing_meta")
        if isinstance(meta, dict):
            try:
                mlat = meta.get("lat")
                mlng = meta.get("lng")
                if mlat is None or mlng is None:
                    return False
                coords = (float(mlat), float(mlng))
            except (TypeError, ValueError):
                return False
        else:
            return False
    return _haversine_km(lat, lng, coords[0], coords[1]) <= radius_km


def shops_matching_radius(
    shops: list[dict[str, Any]],
    lat: float,
    lng: float,
    radius_km: float,
) -> list[str]:
    """Shop ids inside radius. Missing or Online locations are skipped, never raised."""
    out: list[str] = []
    for row in shops:
        coords = shop_coordinates(row.get("location"))
        if coords is None:
            continue
        if _haversine_km(lat, lng, coords[0], coords[1]) <= radius_km:
            sid = row.get("id")
            if sid:
                out.append(str(sid))
    return out
