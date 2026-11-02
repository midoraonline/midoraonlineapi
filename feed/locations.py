from __future__ import annotations

import logging
import time
from typing import Any

from feed.catalog import _scan

logger = logging.getLogger(__name__)

_TTL_S = 600.0
_SHARED_KEY = "midora:feed:locations"
_cache: dict[str, Any] = {"at": 0.0, "rows": None}


def _display(loc: Any) -> str | None:
    raw = loc.get("display") if isinstance(loc, dict) else loc
    text = str(raw or "").strip()
    return text or None


def count_locations(products: list[dict[str, Any]], shop_places: dict[str, str]) -> list[dict[str, Any]]:
    from shop.locations import listing_is_online

    counts: dict[str, int] = {}
    for row in products:
        if listing_is_online(row.get("location_name"), row.get("listing_meta")):
            continue
        name = str(row.get("location_name") or "").strip() or shop_places.get(str(row.get("shop_id") or ""))
        if name:
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [{"name": name, "count": n} for name, n in ranked]


def _load(client: Any) -> list[dict[str, Any]]:
    products = _scan(
        client,
        "products",
        "location_name,shop_id,listing_meta",
        lambda q: q.eq("status", "active").eq("is_published", True),
    )
    missing = list({str(p["shop_id"]) for p in products if p.get("shop_id") and not p.get("location_name")})
    places: dict[str, str] = {}
    for i in range(0, len(missing), 150):
        rows = client.table("shops").select("id,location").in_("id", missing[i : i + 150]).execute().data or []
        for s in rows:
            name = _display(s.get("location"))
            if name:
                places[str(s["id"])] = name
    return count_locations(products, places)


def popular_locations(client: Any, limit: int) -> list[dict[str, Any]]:
    from core import cache

    rows = _cache["rows"]
    if rows is None or time.monotonic() - _cache["at"] > _TTL_S:
        shared = cache.get_json(_SHARED_KEY)
        if isinstance(shared, list):
            rows = shared
        else:
            try:
                rows = _load(client)
                cache.set_json(_SHARED_KEY, rows, ttl_seconds=_TTL_S)
            except Exception as exc:  # noqa: BLE001
                logger.warning("popular locations failed: %s", exc)
                rows = rows or []
        _cache.update({"at": time.monotonic(), "rows": rows})
    return rows[:limit]
