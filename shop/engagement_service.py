from typing import Any

from core.postgrest_compat import is_undefined_column_error
from postgrest.exceptions import APIError
from feed.service import invalidate_user_feed_cache


def _parse_rpc_int(data: Any) -> int | None:
    if data is None:
        return None
    if isinstance(data, (int, float)):
        return int(data)
    if isinstance(data, list) and data:
        return _parse_rpc_int(data[0])
    if isinstance(data, dict) and len(data) == 1:
        return _parse_rpc_int(next(iter(data.values())))
    return None


def _shop_view_count(client: Any, shop_id: str) -> int:
    try:
        r = client.table("shops").select("view_count").eq("id", shop_id).limit(1).execute()
    except APIError as exc:
        if is_undefined_column_error(exc):
            return 0
        raise
    if not r.data:
        return 0
    return int(r.data[0].get("view_count") or 0)


def _product_view_count(client: Any, product_id: str) -> int:
    try:
        r = client.table("products").select("view_count").eq("id", product_id).limit(1).execute()
    except APIError as exc:
        if is_undefined_column_error(exc):
            return 0
        raise
    if not r.data:
        return 0
    return int(r.data[0].get("view_count") or 0)


def _count_by_shop(client: Any, table: str, shop_id: str) -> int:
    r = client.table(table).select("shop_id", count="exact").eq("shop_id", shop_id).limit(1).execute()
    return int(r.count or 0)


def _count_by_product(client: Any, product_id: str) -> int:
    r = (
        client.table("product_likes")
        .select("product_id", count="exact")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )
    return int(r.count or 0)


_EXISTS_CACHE: dict[str, tuple[float, bool]] = {}


def shop_exists(client: Any, shop_id: str) -> bool:
    cache_key = f"shop:{shop_id}"
    now = time.time()
    if cache_key in _EXISTS_CACHE:
        ts, exists = _EXISTS_CACHE[cache_key]
        if now - ts < 15.0:
            return exists
    r = client.table("shops").select("id").eq("id", shop_id).limit(1).execute()
    exists = bool(r.data)
    _EXISTS_CACHE[cache_key] = (now, exists)
    return exists


def product_exists(client: Any, product_id: str) -> bool:
    cache_key = f"prod:{product_id}"
    now = time.time()
    if cache_key in _EXISTS_CACHE:
        ts, exists = _EXISTS_CACHE[cache_key]
        if now - ts < 15.0:
            return exists
    r = client.table("products").select("id").eq("id", product_id).limit(1).execute()
    exists = bool(r.data)
    _EXISTS_CACHE[cache_key] = (now, exists)
    return exists




def _count_shop_listing_events(client: Any, shop_id: str, event_type: str) -> int:
    try:
        count = 0
        
        # 1. Product events
        pr = client.table("products").select("id").eq("shop_id", shop_id).execute()
        product_ids = [str(r["id"]) for r in (pr.data or [])]
        if product_ids:
            r1 = (
                client.table("listing_events")
                .select("id", count="exact")
                .in_("listing_id", product_ids)
                .eq("event_type", event_type)
                .execute()
            )
            count += int(r1.count or 0)
            
        # 2. Shop events
        shop_r = client.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
        if shop_r.data:
            seller_id = shop_r.data[0]["owner_id"]
            r2 = (
                client.table("listing_events")
                .select("id", count="exact")
                .eq("seller_id", seller_id)
                .is_("listing_id", "null")
                .eq("event_type", event_type)
                .filter("metadata->>shop_id", "eq", shop_id)
                .execute()
            )
            count += int(r2.count or 0)
            
        return count
    except Exception:
        return 0


def get_shop_engagement(
    client: Any,
    shop_id: str,
    viewer_user_id: str | None,
    *,
    include_lead_counts: bool = False,
) -> dict[str, Any]:
    """Shop social counters.

    Lead counts (whatsapp/messages) scan listing_events across every product
    in the shop — expensive. Skip them on public SSR slug lookups; load via
    the dedicated engagement endpoint or analytics when needed.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_followers = pool.submit(_count_by_shop, client, "shop_follows", shop_id)
        fut_likes = pool.submit(_count_by_shop, client, "shop_likes", shop_id)
        fut_views = pool.submit(_shop_view_count, client, shop_id)
        fut_wa = (
            pool.submit(_count_shop_listing_events, client, shop_id, "whatsapp_clicked")
            if include_lead_counts
            else None
        )
        fut_msg = (
            pool.submit(_count_shop_listing_events, client, shop_id, "messaged")
            if include_lead_counts
            else None
        )
        follower_count = fut_followers.result()
        like_count = fut_likes.result()
        view_count = fut_views.result()
        whatsapp_clicks = fut_wa.result() if fut_wa else 0
        messages = fut_msg.result() if fut_msg else 0

    viewer_following: bool | None = None
    viewer_liked_shop: bool | None = None
    if viewer_user_id:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_f = pool.submit(
                lambda: client.table("shop_follows")
                .select("user_id")
                .eq("shop_id", shop_id)
                .eq("user_id", viewer_user_id)
                .limit(1)
                .execute()
            )
            fut_l = pool.submit(
                lambda: client.table("shop_likes")
                .select("user_id")
                .eq("shop_id", shop_id)
                .eq("user_id", viewer_user_id)
                .limit(1)
                .execute()
            )
            viewer_following = bool(fut_f.result().data)
            viewer_liked_shop = bool(fut_l.result().data)

    return {
        "follower_count": follower_count,
        "like_count": like_count,
        "view_count": view_count,
        "viewer_following": viewer_following,
        "viewer_liked_shop": viewer_liked_shop,
        "whatsapp_clicks": whatsapp_clicks,
        "messages": messages,
    }


def _count_listing_events(client: Any, product_id: str, event_type: str) -> int:
    try:
        r = (
            client.table("listing_events")
            .select("id", count="exact")
            .eq("listing_id", product_id)
            .eq("event_type", event_type)
            .limit(1)
            .execute()
        )
        return int(r.count or 0)
    except Exception:
        return 0


import time

_PRODUCT_ENGAGEMENT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 5.0


def get_product_engagement(client: Any, product_id: str, viewer_user_id: str | None) -> dict[str, Any]:
    """Retrieve product social engagement counters using parallel queries."""
    cache_key = f"{product_id}:{viewer_user_id or ''}"
    now = time.time()
    if cache_key in _PRODUCT_ENGAGEMENT_CACHE:
        ts, data = _PRODUCT_ENGAGEMENT_CACHE[cache_key]
        if now - ts < _CACHE_TTL_SECONDS:
            return data

    from concurrent.futures import ThreadPoolExecutor

    def _check_viewer_liked() -> bool | None:
        if not viewer_user_id:
            return None
        try:
            r = (
                client.table("product_likes")
                .select("user_id")
                .eq("product_id", product_id)
                .eq("user_id", viewer_user_id)
                .limit(1)
                .execute()
            )
            return bool(r.data)
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_likes = pool.submit(_count_by_product, client, product_id)
        fut_views = pool.submit(_product_view_count, client, product_id)
        fut_viewer = pool.submit(_check_viewer_liked)
        fut_wa = pool.submit(_count_listing_events, client, product_id, "whatsapp_clicked")
        fut_msg = pool.submit(_count_listing_events, client, product_id, "messaged")

        result = {
            "like_count": fut_likes.result(),
            "view_count": fut_views.result(),
            "viewer_liked": fut_viewer.result(),
            "whatsapp_clicks": fut_wa.result(),
            "messages": fut_msg.result(),
        }

    _PRODUCT_ENGAGEMENT_CACHE[cache_key] = (now, result)
    # Evict old cache entries if cache size grows large
    if len(_PRODUCT_ENGAGEMENT_CACHE) > 1000:
        cutoff = now - _CACHE_TTL_SECONDS
        keys_to_remove = [k for k, (t, _) in _PRODUCT_ENGAGEMENT_CACHE.items() if t < cutoff]
        for k in keys_to_remove:
            _PRODUCT_ENGAGEMENT_CACHE.pop(k, None)

    return result



def record_shop_view(client: Any, shop_id: str) -> int:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    r = client.rpc("increment_shop_view_count", {"p_shop_id": shop_id}).execute()
    v = _parse_rpc_int(r.data)
    if v is None:
        raise ValueError("Failed to record shop view (apply migration 20260213_shop_view_counts.sql)")
    return v


def _record_buyer_listing_event(product_id: str, buyer_id: str, event_type: str) -> None:
    """Persist a per-user listing event for feed personalization (best-effort)."""
    from db.supabase import get_supabase_admin

    admin = get_supabase_admin()
    try:
        product_r = admin.table("products").select("shop_id").eq("id", product_id).execute()
        if not product_r.data:
            return
        shop_id = str(product_r.data[0].get("shop_id", ""))
        seller_id: str | None = None
        if shop_id:
            shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
            if shop_r.data:
                seller_id = str(shop_r.data[0]["owner_id"])
        admin.table("listing_events").insert(
            {
                "listing_id": product_id,
                "seller_id": seller_id,
                "buyer_id": buyer_id,
                "event_type": event_type,
                "metadata": {},
            }
        ).execute()
    except Exception:
        pass


def record_product_view(client: Any, product_id: str, buyer_id: str | None = None) -> int:
    if not product_exists(client, product_id):
        raise ValueError("Product not found")
    r = client.rpc("increment_product_view_count", {"p_product_id": product_id}).execute()
    v = _parse_rpc_int(r.data)
    if v is None:
        raise ValueError("Failed to record product view (apply migration 20260213_shop_view_counts.sql)")
    if buyer_id:
        _record_buyer_listing_event(product_id, buyer_id, "viewed")
    return v


def follow_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    client.table("shop_follows").upsert({"user_id": user_id, "shop_id": shop_id}).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_shop_engagement(client, shop_id, user_id)


def unfollow_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    client.table("shop_follows").delete().eq("user_id", user_id).eq("shop_id", shop_id).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_shop_engagement(client, shop_id, user_id)


def like_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    client.table("shop_likes").upsert({"user_id": user_id, "shop_id": shop_id}).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_shop_engagement(client, shop_id, user_id)


def unlike_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    client.table("shop_likes").delete().eq("user_id", user_id).eq("shop_id", shop_id).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_shop_engagement(client, shop_id, user_id)


def like_product(client: Any, user_id: str, product_id: str) -> dict[str, Any]:
    if not product_exists(client, product_id):
        raise ValueError("Product not found")
    client.table("product_likes").upsert({"user_id": user_id, "product_id": product_id}).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_product_engagement(client, product_id, user_id)


def unlike_product(client: Any, user_id: str, product_id: str) -> dict[str, Any]:
    client.table("product_likes").delete().eq("user_id", user_id).eq("product_id", product_id).execute()
    invalidate_user_feed_cache(client, user_id)
    return get_product_engagement(client, product_id, user_id)
