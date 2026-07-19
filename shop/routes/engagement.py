from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from db.supabase import get_supabase_admin, get_supabase_client
from core.security import get_current_user_id, get_optional_user_id
from shop import engagement_service
from shop.schemas import ShopEngagementState, ViewCountResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{shop_id}/engagement", response_model=ShopEngagementState)
async def get_shop_engagement(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    if not engagement_service.shop_exists(client, shop_id):
        raise HTTPException(status_code=404, detail="Shop not found")
    return engagement_service.get_shop_engagement(client, shop_id, viewer_id)


@router.post("/{shop_id}/follow", response_model=ShopEngagementState)
async def follow_shop(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.follow_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{shop_id}/follow", response_model=ShopEngagementState)
async def unfollow_shop(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unfollow_shop(client, user_id, shop_id)


@router.post("/{shop_id}/like", response_model=ShopEngagementState)
async def like_shop(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.like_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{shop_id}/views", response_model=ViewCountResponse)
async def record_shop_view(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
):
    """Increment shop page view (click) count. Call once when a customer opens the storefront."""
    try:
        n = engagement_service.record_shop_view(client, shop_id)
        return ViewCountResponse(view_count=n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{shop_id}/events")
async def record_shop_event(
    shop_id: str,
    event_type: str = Query(..., description="Event type: whatsapp_clicked, messaged"),
    current_user_id: str | None = Depends(get_optional_user_id),
):
    """Record a shop-level event (whatsapp click, message, etc.)."""
    valid_types = {"whatsapp_clicked", "messaged"}
    if event_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid event_type. Must be one of: {', '.join(sorted(valid_types))}",
        )

    admin = get_supabase_admin()

    shop_r = admin.table("shops").select("id,owner_id").eq("id", shop_id).limit(1).execute()
    if not shop_r.data:
        raise HTTPException(status_code=404, detail="Shop not found")
    seller_id = str(shop_r.data[0].get("owner_id", ""))

    try:
        payload = {
            "listing_id": None,
            "seller_id": seller_id,
            "buyer_id": current_user_id,
            "event_type": event_type,
            "metadata": {"source": "shop_page", "shop_id": shop_id},
        }
        admin.table("listing_events").insert(payload).execute()
    except Exception as exc:
        logger.warning("record_shop_event(%s, %s) failed: %s", shop_id, event_type, exc)
        raise HTTPException(status_code=502, detail="Failed to record event")

    return {"status": "recorded"}


@router.delete("/{shop_id}/like", response_model=ShopEngagementState)
async def unlike_shop(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unlike_shop(client, user_id, shop_id)


# ---------------------------------------------------------------------------
# Authenticated user – followed / liked shop lists
# ---------------------------------------------------------------------------

def _enrich_shop_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw shop DB row for the compact list responses."""
    return {
        "id": str(row.get("id", "")),
        "name": row.get("name", ""),
        "slug": row.get("slug", ""),
        "description": row.get("description"),
        "logo_url": row.get("logo_url"),
        "shop_type": row.get("shop_type") or "product",
        "is_active": bool(row.get("is_active")),
        "view_count": int(row.get("view_count") or 0),
        "follower_count": int(row.get("follower_count") or 0),
        "like_count": int(row.get("like_count") or 0),
    }


@router.get("/me/followed")
async def my_followed_shops(user_id: str = Depends(get_current_user_id)) -> dict:
    """Return the shops the authenticated user follows, enriched with counts."""
    admin = get_supabase_admin()
    # RLS on shop_follows allows the owner's row, but we use admin client here
    # so we never have to thread a cookie through a secondary query.
    fr = (
        admin.table("shop_follows")
        .select("shop_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    shop_ids = [str(r["shop_id"]) for r in (fr.data or []) if r.get("shop_id")]
    if not shop_ids:
        return {"items": [], "total": 0}

    sr = (
        admin.table("shops")
        .select(
            "id,name,slug,description,logo_url,shop_type,is_active,view_count,created_at"
        )
        .in_("id", shop_ids)
        .execute()
    )

    # Preserve follow order
    by_id = {str(r["id"]): r for r in (sr.data or [])}
    items = [_enrich_shop_row(by_id[sid]) for sid in shop_ids if sid in by_id]
    return {"items": items, "total": len(items)}


@router.get("/me/liked")
async def my_liked_shops(user_id: str = Depends(get_current_user_id)) -> dict:
    """Return the shops the authenticated user has liked."""
    admin = get_supabase_admin()
    lr = (
        admin.table("shop_likes")
        .select("shop_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    shop_ids = [str(r["shop_id"]) for r in (lr.data or []) if r.get("shop_id")]
    if not shop_ids:
        return {"items": [], "total": 0}

    sr = (
        admin.table("shops")
        .select(
            "id,name,slug,description,logo_url,shop_type,is_active,view_count,created_at"
        )
        .in_("id", shop_ids)
        .execute()
    )
    by_id = {str(r["id"]): r for r in (sr.data or [])}
    items = [_enrich_shop_row(by_id[sid]) for sid in shop_ids if sid in by_id]
    return {"items": items, "total": len(items)}


@router.get("/{shop_id}/dashboard")
async def shop_dashboard(
    shop_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Composite endpoint: shop profile + engagement + products + lead stats.

    Returns everything a merchant dashboard needs in a single call instead of
    the previous 4 separate round-trips.
    """
    admin = get_supabase_admin()

    shop_data: dict[str, Any] | None = None
    try:
        sr = admin.table("shops").select("*").eq("id", shop_id).limit(1).execute()
        if sr.data:
            shop_data = sr.data[0]
    except Exception as exc:
        logger.warning("shop_dashboard: shop lookup failed for %s: %s", shop_id, exc)

    if not shop_data:
        raise HTTPException(status_code=404, detail="Shop not found")

    # Engagement
    engagement = engagement_service.get_shop_engagement(admin, shop_id, user_id)

    # Products
    products_list: list[dict[str, Any]] = []
    product_event_counts: dict[str, dict[str, int]] = {}
    try:
        pr = (
            admin.table("products")
            .select(
                "id,title,description,price_ugx,image_urls,category,item_type,"
                "status,listing_score,location_name,is_published,view_count,"
                "like_count,created_at,updated_at"
            )
            .eq("shop_id", shop_id)
            .order("created_at", desc=True)
            .execute()
        )
        product_ids = [str(row["id"]) for row in (pr.data or []) if row.get("id")]
        if product_ids:
            try:
                ev_r = (
                    admin.table("listing_events")
                    .select("listing_id, event_type")
                    .in_("listing_id", product_ids)
                    .execute()
                )
                for ev in (ev_r.data or []):
                    lid = str(ev.get("listing_id"))
                    et = ev.get("event_type", "")
                    if lid not in product_event_counts:
                        product_event_counts[lid] = {"whatsapp_clicks": 0, "messages": 0}
                    if et == "whatsapp_clicked":
                        product_event_counts[lid]["whatsapp_clicks"] += 1
                    elif et == "messaged":
                        product_event_counts[lid]["messages"] += 1
            except Exception as exc:
                logger.warning("shop_dashboard: listing_events lookup failed: %s", exc)
        for row in (pr.data or []):
            imgs = row.get("image_urls")
            if isinstance(imgs, str):
                imgs = [s.strip() for s in imgs.split(",") if s.strip()]
            elif not isinstance(imgs, list):
                imgs = []
            pid = str(row["id"])
            ec = product_event_counts.get(pid, {})
            products_list.append({
                "id": pid,
                "shop_id": shop_id,
                "title": row.get("title", ""),
                "description": row.get("description"),
                "price_ugx": float(row.get("price_ugx", 0)),
                "image_urls": imgs[:1] if imgs else None,
                "category": row.get("category"),
                "item_type": row.get("item_type", "product"),
                "status": row.get("status", "active"),
                "listing_score": int(row.get("listing_score") or 0),
                "location_name": row.get("location_name"),
                "is_published": bool(row.get("is_published", True)),
                "view_count": int(row.get("view_count") or 0),
                "like_count": int(row.get("like_count") or 0),
                "whatsapp_clicks": ec.get("whatsapp_clicks", 0),
                "messages": ec.get("messages", 0),
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
                "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
            })
    except Exception as exc:
        logger.warning("shop_dashboard products failed: %s", exc)

    # Lead stats
    lead_stats: dict[str, Any] = {}
    try:
        from ranking.lead_service import get_lead_stats_for_seller
        lead_stats = get_lead_stats_for_seller(user_id)
    except Exception as exc:
        logger.warning("shop_dashboard leads failed: %s", exc)

    return {
        "shop": {
            "id": str(shop_data["id"]),
            "name": shop_data.get("name", ""),
            "slug": shop_data.get("slug", ""),
            "description": shop_data.get("description"),
            "about": shop_data.get("about"),
            "logo_url": shop_data.get("logo_url"),
            "shop_type": shop_data.get("shop_type", "product"),
            "is_active": bool(shop_data.get("is_active", False)),
            "category": shop_data.get("category"),
            "view_count": int(shop_data.get("view_count") or 0),
            "whatsapp_number": shop_data.get("whatsapp_number"),
            "location": shop_data.get("location"),
            "created_at": str(shop_data["created_at"]) if shop_data.get("created_at") else None,
        },
        "engagement": engagement,
        "products": products_list,
        "lead_stats": lead_stats,
    }


@router.get("/me/stats")
async def my_shops_stats(user_id: str = Depends(get_current_user_id)) -> dict:
    """Aggregate engagement metrics across all shops owned by the current user."""
    admin = get_supabase_admin()
    shops_r = (
        admin.table("shops")
        .select("id,name,slug,is_active,view_count,created_at")
        .eq("owner_id", user_id)
        .execute()
    )
    shops = shops_r.data or []
    shop_ids = [str(s["id"]) for s in shops if s.get("id")]

    total_views = sum(int(s.get("view_count") or 0) for s in shops)
    total_follows = 0
    total_likes = 0
    product_views = 0
    product_likes_count = 0
    product_count = 0

    if shop_ids:
        fol_r = (
            admin.table("shop_follows")
            .select("shop_id", count="exact")
            .in_("shop_id", shop_ids)
            .execute()
        )
        total_follows = int(fol_r.count or 0)

        lik_r = (
            admin.table("shop_likes")
            .select("shop_id", count="exact")
            .in_("shop_id", shop_ids)
            .execute()
        )
        total_likes = int(lik_r.count or 0)

        prods_r = (
            admin.table("products")
            .select("id,view_count")
            .in_("shop_id", shop_ids)
            .execute()
        )
        prods = prods_r.data or []
        product_count = len(prods)
        product_ids = [str(p["id"]) for p in prods if p.get("id")]
        product_views = sum(int(p.get("view_count") or 0) for p in prods)

        if product_ids:
            plik_r = (
                admin.table("product_likes")
                .select("product_id", count="exact")
                .in_("product_id", product_ids)
                .execute()
            )
            product_likes_count = int(plik_r.count or 0)

    total_whatsapp_clicks = 0
    total_messages = 0
    
    # 1. Product-level events
    if product_ids:
        try:
            ev_r = (
                admin.table("listing_events")
                .select("event_type", count="exact")
                .in_("listing_id", product_ids)
                .execute()
            )
            for ev in (ev_r.data or []):
                et = ev.get("event_type", "")
                if et == "whatsapp_clicked":
                    total_whatsapp_clicks += 1
                elif et == "messaged":
                    total_messages += 1
        except Exception as exc:
            logger.warning("listing_events aggregate failed: %s", exc)
            
    # 2. Shop-level events
    if shop_ids:
        try:
            # We must fetch the rows because we can't easily do a jsonb IN filter
            sev_r = (
                admin.table("listing_events")
                .select("event_type, metadata")
                .is_("listing_id", "null")
                .in_("event_type", ["whatsapp_clicked", "messaged"])
                .eq("seller_id", user_id)
                .execute()
            )
            for ev in (sev_r.data or []):
                meta = ev.get("metadata") or {}
                if meta.get("shop_id") in shop_ids:
                    et = ev.get("event_type", "")
                    if et == "whatsapp_clicked":
                        total_whatsapp_clicks += 1
                    elif et == "messaged":
                        total_messages += 1
        except Exception as exc:
            logger.warning("shop-level listing_events lookup failed: %s", exc)

    return {
        "total_shops": len(shops),
        "active_shops": sum(1 for s in shops if s.get("is_active")),
        "total_shop_views": total_views,
        "total_followers": total_follows,
        "total_shop_likes": total_likes,
        "total_products": product_count,
        "total_product_views": product_views,
        "total_product_likes": product_likes_count,
        "total_whatsapp_clicks": total_whatsapp_clicks,
        "total_messages": total_messages,
    }


@router.get("/me/analytics")
async def my_shops_analytics(
    user_id: str = Depends(get_current_user_id),
    days: int = Query(30, ge=1, le=180),
) -> dict:
    """Rich merchant analytics: impressions, daily series, per-shop breakdown,
    conversion funnels, and top listings. Powers the expanded merchant dashboard."""
    from datetime import datetime, timedelta, timezone
    from collections import defaultdict

    admin = get_supabase_admin()
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(days=days)).isoformat()

    # Shops owned
    shops_r = (
        admin.table("shops")
        .select("id,name,slug,is_active,view_count,follower_count,like_count,created_at,shop_type")
        .eq("owner_id", user_id)
        .execute()
    )
    shops = shops_r.data or []
    shop_ids = [str(s["id"]) for s in shops if s.get("id")]

    if not shop_ids:
        return {
            "generated_at": now.isoformat(),
            "window_days": days,
            "summary": {},
            "per_shop": [],
            "top_products": [],
            "trends": {"impressions": [], "views": [], "whatsapp": [], "messages": []},
            "funnel": {},
            "pool_mix": [],
        }

    # Products
    prods_r = (
        admin.table("products")
        .select("id,shop_id,title,view_count,like_count,category,price_ugx,is_published,created_at")
        .in_("shop_id", shop_ids)
        .execute()
    )
    products = prods_r.data or []
    product_ids = [str(p["id"]) for p in products if p.get("id")]
    prod_shop = {str(p["id"]): str(p.get("shop_id") or "") for p in products}

    # ── Impressions (chunked IN) ───────────────────────────────────────────
    impressions_rows: list[dict[str, Any]] = []
    chunk = 400
    for i in range(0, len(product_ids), chunk):
        subset = product_ids[i : i + chunk]
        try:
            r = (
                admin.table("listing_impressions")
                .select("listing_id,pool,created_at")
                .in_("listing_id", subset)
                .gte("created_at", since_iso)
                .limit(50000)
                .execute()
            )
            impressions_rows.extend(r.data or [])
        except Exception as exc:
            logger.warning("me/analytics impressions chunk failed: %s", exc)

    total_impressions = len(impressions_rows)
    impressions_by_product: dict[str, int] = defaultdict(int)
    pool_counts: dict[str, int] = defaultdict(int)
    impr_series: dict[str, int] = defaultdict(int)
    for r in impressions_rows:
        pid = str(r.get("listing_id") or "")
        pool_counts[str(r.get("pool") or "organic")] += 1
        impressions_by_product[pid] += 1
        day = str(r.get("created_at") or "")[:10]
        if day:
            impr_series[day] += 1

    # ── Listing events (views, whatsapp, messages) ─────────────────────────
    events_rows: list[dict[str, Any]] = []
    for i in range(0, len(product_ids), chunk):
        subset = product_ids[i : i + chunk]
        try:
            r = (
                admin.table("listing_events")
                .select("listing_id,event_type,created_at")
                .in_("listing_id", subset)
                .gte("created_at", since_iso)
                .limit(50000)
                .execute()
            )
            events_rows.extend(r.data or [])
        except Exception as exc:
            logger.warning("me/analytics events chunk failed: %s", exc)

    events_by_type: dict[str, int] = defaultdict(int)
    views_by_product: dict[str, int] = defaultdict(int)
    wa_by_product: dict[str, int] = defaultdict(int)
    msg_by_product: dict[str, int] = defaultdict(int)
    view_series: dict[str, int] = defaultdict(int)
    wa_series: dict[str, int] = defaultdict(int)
    msg_series: dict[str, int] = defaultdict(int)
    for e in events_rows:
        et = str(e.get("event_type") or "")
        events_by_type[et] += 1
        pid = str(e.get("listing_id") or "")
        day = str(e.get("created_at") or "")[:10]
        if et == "viewed":
            views_by_product[pid] += 1
            if day:
                view_series[day] += 1
        elif et == "whatsapp_clicked":
            wa_by_product[pid] += 1
            if day:
                wa_series[day] += 1
        elif et == "messaged":
            msg_by_product[pid] += 1
            if day:
                msg_series[day] += 1

    # ── Per-shop rollup ────────────────────────────────────────────────────
    impressions_by_shop: dict[str, int] = defaultdict(int)
    for pid, n in impressions_by_product.items():
        sid = prod_shop.get(pid, "")
        if sid:
            impressions_by_shop[sid] += n

    per_shop = []
    for s in shops:
        sid = str(s["id"])
        per_shop.append({
            "id": sid,
            "name": s.get("name"),
            "slug": s.get("slug"),
            "is_active": bool(s.get("is_active", False)),
            "shop_type": s.get("shop_type"),
            "view_count": int(s.get("view_count") or 0),
            "follower_count": int(s.get("follower_count") or 0),
            "like_count": int(s.get("like_count") or 0),
            "impressions": impressions_by_shop.get(sid, 0),
        })
    per_shop.sort(key=lambda r: r["impressions"], reverse=True)

    # ── Top products (by impressions) ──────────────────────────────────────
    top_products = []
    for p in products:
        pid = str(p["id"])
        impr = impressions_by_product.get(pid, 0)
        vw = int(p.get("view_count") or 0) + views_by_product.get(pid, 0)
        top_products.append({
            "id": pid,
            "title": p.get("title"),
            "category": p.get("category"),
            "shop_id": prod_shop.get(pid, ""),
            "impressions": impr,
            "views": vw,
            "whatsapp_clicks": wa_by_product.get(pid, 0),
            "messages": msg_by_product.get(pid, 0),
            "likes": int(p.get("like_count") or 0),
            "ctr": (vw / impr) if impr > 0 else 0.0,
            "price_ugx": float(p.get("price_ugx") or 0),
        })
    top_products.sort(key=lambda r: r["impressions"], reverse=True)
    top_products = top_products[:15]

    # ── Daily series (zero-filled) ────────────────────────────────────────
    def _series(bucket: dict[str, int]) -> list[dict[str, Any]]:
        out = []
        for i in range(days):
            d = (now - timedelta(days=days - 1 - i)).date().isoformat()
            out.append({"day": d, "count": bucket.get(d, 0)})
        return out

    trends = {
        "impressions": _series(impr_series),
        "views": _series(view_series),
        "whatsapp": _series(wa_series),
        "messages": _series(msg_series),
    }

    total_views = sum(views_by_product.values()) + sum(int(p.get("view_count") or 0) for p in products)
    total_wa = sum(wa_by_product.values())
    total_msg = sum(msg_by_product.values())

    funnel = {
        "impressions": total_impressions,
        "views": total_views,
        "whatsapp_clicks": total_wa,
        "messages": total_msg,
        "view_rate": (total_views / total_impressions) if total_impressions else 0.0,
        "wa_rate": (total_wa / total_views) if total_views else 0.0,
        "msg_rate": (total_msg / total_views) if total_views else 0.0,
    }

    pool_mix = [{"label": k, "value": v} for k, v in sorted(pool_counts.items(), key=lambda x: -x[1])]

    summary = {
        "total_shops": len(shops),
        "active_shops": sum(1 for s in shops if s.get("is_active")),
        "total_products": len(products),
        "published_products": sum(1 for p in products if p.get("is_published")),
        "total_impressions": total_impressions,
        "total_product_views": total_views,
        "total_whatsapp_clicks": total_wa,
        "total_messages": total_msg,
        "total_shop_views": sum(int(s.get("view_count") or 0) for s in shops),
        "total_followers": sum(int(s.get("follower_count") or 0) for s in shops),
        "total_shop_likes": sum(int(s.get("like_count") or 0) for s in shops),
    }

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "summary": summary,
        "per_shop": per_shop,
        "top_products": top_products,
        "trends": trends,
        "funnel": funnel,
        "pool_mix": pool_mix,
    }


