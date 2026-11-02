from __future__ import annotations

import logging
import time
from typing import Any

from db.supabase import get_supabase_admin
from feed.catalog import ListingFilters

logger = logging.getLogger(__name__)

MAX_CARDS = 40
_GUEST_HOME_TTL_S = 60.0
_guest_home_cache: dict[tuple[int, int, str], tuple[float, dict[str, Any]]] = {}


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


def get_home_feed(
    limit: int = MAX_CARDS,
    page: int = 1,
    user_id: str | None = None,
    exclude_ids: list[str] | None = None,
    session_id: str | None = None,
    category: str | None = None,
    filters: ListingFilters | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    from feed.service import get_algorithm_feed

    admin = get_supabase_admin()
    is_guest = not user_id
    active = filters or ListingFilters(category=(category or "").strip() or None)
    guest_cache_key = (
        (page, limit, active.cache_token())
        if is_guest and page == 1 and not exclude_ids
        else None
    )
    if guest_cache_key:
        hit = _guest_home_cache.get(guest_cache_key)
        if hit and (time.monotonic() - hit[0]) < _GUEST_HOME_TTL_S:
            return hit[1]

    feed = get_algorithm_feed(
        user_id=user_id,
        page=page,
        limit=limit,
        exclude_ids=exclude_ids,
        session_id=session_id,
        category=active.category,
        filters=active,
        version=version,
    )
    algorithm_paged = feed.items
    shop_ids = list({str(p.shop_id) for p in algorithm_paged if p.shop_id})

    shops_map: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            from shop.locations import shop_coordinates
            from shop.seller_display import overlay_personal_sellers

            shop_rows = None
            for cols in (
                "id,name,slug,logo_url,owner_id,whatsapp_number,"
                "is_active,category,trust_score,trust_badges,available_now,location,"
                "is_personal,created_at,last_seen_at",
                "id,name,slug,logo_url,owner_id,whatsapp_number,"
                "is_active,category,trust_score,trust_badges,available_now,location",
            ):
                try:
                    shop_rows = (
                        admin.table("shops").select(cols).in_("id", shop_ids).execute()
                    ).data or []
                    break
                except Exception as exc:
                    logger.warning("home feed shop select failed: %s", exc)
                    shop_rows = None
            if shop_rows is None:
                shop_rows = []
            for s in shop_rows:
                sid = str(s["id"])
                loc = s.get("location")
                badges = s.get("trust_badges") or []
                if not isinstance(badges, list):
                    badges = []
                loc_display = loc.get("display") if isinstance(loc, dict) else loc
                coords = shop_coordinates(loc)
                loc_lat = coords[0] if coords else None
                loc_lng = coords[1] if coords else None
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
                    "trust_badges": badges if badges else ["shop_listed"],
                    "available_now": bool(s.get("available_now", False)),
                    "location": loc_display,
                    "location_lat": loc_lat,
                    "location_lng": loc_lng,
                    "is_personal": bool(s.get("is_personal")),
                    "created_at": s.get("created_at"),
                    "last_seen_at": s.get("last_seen_at"),
                }
            overlay_personal_sellers(admin, shops_map)
        except Exception as exc:
            logger.warning("home feed batch shop fetch failed: %s", exc)

    product_ids = [str(p.id) for p in algorithm_paged if p.id]
    viewer_liked_ids: set[str] = set()
    if product_ids and user_id:
        try:
            vr = (
                admin.table("product_likes")
                .select("product_id")
                .eq("user_id", user_id)
                .in_("product_id", product_ids)
                .execute()
            )
            viewer_liked_ids = {str(row["product_id"]) for row in (vr.data or []) if row.get("product_id")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("home feed viewer-liked fetch failed: %s", exc)

    def _embed(products: list) -> list[dict[str, Any]]:
        out = []
        from feed.badges import card_statuses
        from shop.locations import listing_is_online
        from shop.serializers import clip_card_description

        statuses = card_statuses(
            admin,
            [
                {"id": str(p.id), "item_type": p.item_type, "listing_meta": getattr(p, "listing_meta", None),
                 "first_posted_at": p.first_posted_at, "created_at": p.created_at}
                for p in products
            ],
            placements=feed.placements,
            trending=feed.trending,
        )

        for p in products:
            shop = dict(shops_map.get(str(p.shop_id)) or {})
            meta = getattr(p, "listing_meta", None)
            online = listing_is_online(p.location_name, meta)
            if online:
                shop["location_lat"] = None
                shop["location_lng"] = None
                shop["location"] = "Online"
            imgs = _coerce_images(p.image_urls)[:1]
            out.append({
                "id": str(p.id),
                "shop_id": str(p.shop_id),
                "title": p.title,
                "description": clip_card_description(getattr(p, "description", None)),
                "slug": "",
                "price_ugx": _safe_float(p.price_ugx),
                "discount_price": _safe_float(p.discount_price) if getattr(p, "discount_price", None) is not None else None,
                "discount_expires_at": getattr(p, "discount_expires_at", None),
                "image_urls": imgs,
                "primary_image": imgs[0] if imgs else None,
                "category": p.category,
                "item_type": p.item_type,
                "is_published": p.is_published,
                "view_count": _safe_int(p.view_count),
                "like_count": 0,
                "viewer_liked": (str(p.id) in viewer_liked_ids) if not is_guest else None,
                "listing_score": _safe_int(p.listing_score),
                "location_name": p.location_name,
                "is_online": online,
                "listing_meta": (
                    getattr(p, "listing_meta", None)
                    if isinstance(getattr(p, "listing_meta", None), dict)
                    else {}
                ),
                "created_at": p.created_at,
                "first_posted_at": p.first_posted_at or p.created_at,
                "updated_at": getattr(p, "updated_at", None) or p.created_at,
                "stock_quantity": int(getattr(p, "stock_quantity", 0) or 0),
                "shop": shop,
                "placement": feed.placements.get(str(p.id), "organic"),
                "status": statuses.get(str(p.id)),
                "average_rating": p.average_rating if p.review_count else None,
                "review_count": int(p.review_count or 0),
                "is_negotiable": getattr(p, "is_negotiable", True) is not False,
            })
        return out

    next_cursor = None
    if feed.has_more:
        next_cursor = f"v:{feed.version}:p:{page + 1}" if feed.version else f"p:{page + 1}"
    payload = {
        "algorithm": _embed(algorithm_paged),
        "page": page,
        "limit": limit,
        "total": feed.total if feed.total is not None else ((page - 1) * limit) + len(algorithm_paged),
        "has_more": feed.has_more,
        "next_cursor": next_cursor,
    }
    if guest_cache_key:
        _guest_home_cache[guest_cache_key] = (time.monotonic(), payload)
    return payload
