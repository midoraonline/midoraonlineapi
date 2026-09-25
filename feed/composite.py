"""Composite endpoint that returns feed data with shop details and boost
status embedded — eliminating the N+1 pattern on the frontend.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from db.supabase import get_supabase_admin
from feed.catalog import ListingFilters, rating_map
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

MAX_CARDS = 72
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
) -> dict[str, Any]:
    """Return the ranked home algorithm feed with shop + boost data embedded.

    Home UI only renders `algorithm`. `trending` / `premium` / `fresh` stay
    empty so first paint does not hydrate unused sub-feeds.
    """
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

    algorithm_paged, algo_has_more, filtered_total = get_algorithm_feed(
        admin,
        user_id=user_id,
        page=page,
        limit=limit,
        exclude_ids=exclude_ids,
        session_id=session_id,
        category=active.category,
        filters=active,
    )
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
    ratings = rating_map(admin, product_ids) if product_ids and active.is_active() else {}
    boosted_ids: set[str] = set()
    viewer_liked_ids: set[str] = set()

    def _boosts() -> set[str]:
        if not product_ids:
            return set()
        try:
            now_iso = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            boosts_r = (
                admin.table("listing_boosts")
                .select("listing_id")
                .in_("listing_id", product_ids)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .execute()
            )
            return {
                str(b["listing_id"])
                for b in (boosts_r.data or [])
                if b.get("listing_id")
            }
        except Exception as exc:
            logger.warning("home feed batch boost fetch failed: %s", exc)
            return set()

    if product_ids:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fb = pool.submit(_boosts)
            if not is_guest and user_id:
                def _viewer_likes() -> set[str]:
                    try:
                        vr = (
                            admin.table("product_likes")
                            .select("product_id")
                            .eq("user_id", user_id)
                            .in_("product_id", product_ids)
                            .execute()
                        )
                        return {
                            str(row.get("product_id"))
                            for row in (vr.data or [])
                            if row.get("product_id")
                        }
                    except Exception as exc:
                        logger.warning("home feed viewer-liked fetch failed: %s", exc)
                        return set()

                fv = pool.submit(_viewer_likes)
                viewer_liked_ids = fv.result()
            boosted_ids = fb.result()

    def _embed(products: list) -> list[dict[str, Any]]:
        out = []
        from shop.locations import listing_is_online

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
                "updated_at": getattr(p, "updated_at", None) or p.created_at,
                "stock_quantity": int(getattr(p, "stock_quantity", 0) or 0),
                "shop": shop,
                "boosted": str(p.id) in boosted_ids,
                "average_rating": ratings.get(str(p.id), (0.0, 0))[0],
                "review_count": ratings.get(str(p.id), (0.0, 0))[1],
                "is_negotiable": getattr(p, "is_negotiable", True) is not False,
            })
        return out

    payload = {
        "algorithm": _embed(algorithm_paged),
        "trending": [],
        "premium": [],
        "fresh": [],
        "page": page,
        "limit": limit,
        "total": filtered_total if filtered_total is not None else ((page - 1) * limit) + len(algorithm_paged),
        "has_more": algo_has_more,
        "next_cursor": f"p:{page + 1}" if algo_has_more else None,
    }
    if guest_cache_key:
        _guest_home_cache[guest_cache_key] = (time.monotonic(), payload)
    return payload
