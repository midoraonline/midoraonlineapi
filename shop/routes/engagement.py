from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from db.supabase import get_supabase_admin, get_supabase_client
from core.security import get_current_user_id, get_optional_user_id
from shop import engagement_service
from shop.schemas import ShopEngagementState, ViewCountResponse

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

    return {
        "total_shops": len(shops),
        "active_shops": sum(1 for s in shops if s.get("is_active")),
        "total_shop_views": total_views,
        "total_followers": total_follows,
        "total_shop_likes": total_likes,
        "total_products": product_count,
        "total_product_views": product_views,
        "total_product_likes": product_likes_count,
    }

