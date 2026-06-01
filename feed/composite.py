"""Composite endpoint that returns feed data with shop details and boost
status embedded — eliminating the N+1 pattern on the frontend.
"""

from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

MAX_CARDS = 72


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _coerce_images(image_urls: Any) -> list[str]:
    if isinstance(image_urls, list):
        return [str(x) for x in image_urls if x]
    if isinstance(image_urls, str):
        return [s.strip() for s in image_urls.split(",") if s.strip()]
    return []


def get_home_feed(limit: int = MAX_CARDS) -> dict[str, Any]:
    """Return all 4 home-page feeds in one call with shop + boost data embedded.

    This eliminates ~N/3 + N frontend API round-trips (shop details + boost
    checks per product).
    """
    from feed.service import get_algorithm_feed, get_latest_feed
    from db.supabase import get_supabase_client

    admin = get_supabase_admin()
    client = get_supabase_client(None)

    algorithm_raw = get_algorithm_feed(client, limit=limit)
    trending_raw = get_algorithm_feed(client, limit=8)
    premium_raw = get_algorithm_feed(client, limit=8)
    fresh_raw = get_latest_feed(client, limit=12)

    # Collect unique shop IDs from all feeds
    all_products = algorithm_raw + trending_raw + premium_raw + fresh_raw
    shop_ids = list({str(p.shop_id) for p in all_products if p.shop_id})

    # Batch-fetch shops (1 query instead of N)
    shops_map: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            shops_r = (
                admin.table("shops")
                .select(
                    "id,name,slug,logo_url,owner_id,whatsapp_number,"
                    "is_active,category,trust_score,available_now,location"
                )
                .in_("id", shop_ids)
                .execute()
            )
            for s in (shops_r.data or []):
                sid = str(s["id"])
                loc = s.get("location")
                shops_map[sid] = {
                    "id": sid,
                    "name": s.get("name", ""),
                    "slug": s.get("slug", ""),
                    "logo_url": s.get("logo_url"),
                    "owner_id": str(s.get("owner_id", "")) if s.get("owner_id") else None,
                    "whatsapp_number": s.get("whatsapp_number"),
                    "is_active": bool(s.get("is_active", False)),
                    "category": s.get("category"),
                    "trust_score": _safe_int(s.get("trust_score")),
                    "available_now": bool(s.get("available_now", False)),
                    "location": loc.get("display") if isinstance(loc, dict) else loc,
                }
        except Exception as exc:
            logger.warning("home feed batch shop fetch failed: %s", exc)

    # Batch-fetch boost status (1 query instead of N)
    product_ids = [str(p.id) for p in all_products if p.id]
    boosted_ids: set[str] = set()
    if product_ids:
        try:
            now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            boosts_r = (
                admin.table("listing_boosts")
                .select("listing_id")
                .in_("listing_id", product_ids)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .execute()
            )
            boosted_ids = {str(b["listing_id"]) for b in (boosts_r.data or []) if b.get("listing_id")}
        except Exception as exc:
            logger.warning("home feed batch boost fetch failed: %s", exc)

    def _embed(products: list) -> list[dict[str, Any]]:
        out = []
        for p in products:
            shop = shops_map.get(str(p.shop_id)) or {}
            imgs = _coerce_images(p.image_urls)
            out.append({
                "id": str(p.id),
                "shop_id": str(p.shop_id),
                "title": p.title,
                "slug": "",  # frontend computes from title+id
                "price_ugx": _safe_float(p.price_ugx),
                "image_urls": imgs,
                "primary_image": imgs[0] if imgs else None,
                "category": p.category,
                "item_type": p.item_type,
                "is_published": p.is_published,
                "view_count": _safe_int(p.view_count),
                "like_count": _safe_int(p.like_count),
                "listing_score": _safe_int(p.listing_score),
                "location_name": p.location_name,
                "created_at": p.created_at,
                "updated_at": getattr(p, "updated_at", None),
                "shop": shop,
                "boosted": str(p.id) in boosted_ids,
            })
        return out

    return {
        "algorithm": _embed(algorithm_raw),
        "trending": _embed(trending_raw),
        "premium": _embed(premium_raw),
        "fresh": _embed(fresh_raw),
    }
