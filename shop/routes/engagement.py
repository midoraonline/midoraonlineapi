from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_cache.decorator import cache

from db.supabase import get_supabase_admin, get_supabase_client
from core.security import get_current_user_id, get_optional_user_id
from shop import engagement_service
from shop.schemas import ShopEngagementState, ViewCountResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{shop_id}/engagement", response_model=ShopEngagementState)
async def get_shop_engagement(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    if not engagement_service.shop_exists(client, shop_id):
        raise HTTPException(status_code=404, detail="Shop not found")
    return engagement_service.get_shop_engagement(client, shop_id, viewer_id)


@router.post("/{shop_id}/follow", response_model=ShopEngagementState)
async def follow_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.follow_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{shop_id}/follow", response_model=ShopEngagementState)
async def unfollow_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unfollow_shop(client, user_id, shop_id)


@router.post("/{shop_id}/like", response_model=ShopEngagementState)
async def like_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.like_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{shop_id}/views", response_model=ViewCountResponse)
async def record_shop_view(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
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
        return {"error": f"Invalid event_type. Must be one of: {', '.join(sorted(valid_types))}"}

    admin = get_supabase_admin()

    shop_r = admin.table("shops").select("id,owner_id").eq("id", shop_id).limit(1).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    seller_id = str(shop_r.data[0].get("owner_id", ""))

    try:
        # Find a product from this shop to use as listing_id reference
        prod_r = admin.table("products").select("id").eq("shop_id", shop_id).limit(1).execute()
        listing_id = str(prod_r.data[0]["id"]) if prod_r.data else None

        payload = {
            "listing_id": listing_id,
            "seller_id": seller_id,
            "buyer_id": current_user_id,
            "event_type": event_type,
            "metadata": {"source": "shop_page"},
        }
        admin.table("listing_events").insert(payload).execute()
    except Exception as exc:
        logger.warning("record_shop_event(%s, %s) failed: %s", shop_id, event_type, exc)
        return {"error": "Failed to record event"}

    return {"status": "recorded"}


@router.delete("/{shop_id}/like", response_model=ShopEngagementState)
async def unlike_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
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
@cache(expire=120)
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
    except Exception:
        pass

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
            except Exception:
                pass
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
        except Exception:
            pass

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

