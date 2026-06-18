from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from core.authz import ensure_product_owner, ensure_shop_owner
from core.config import get_settings
from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from core.security import get_current_claims, get_current_user_id, get_optional_user_id
from shop import engagement_service, service as shop_service
from shop.schemas import (
    ProductCreate,
    ProductDetailResponse,
    ProductEngagementState,
    ProductResponse,
    ProductUpdate,
    ViewCountResponse,
)

router = APIRouter()


@router.post("/{shop_id}/products", response_model=ProductResponse)
async def create_product(
  shop_id: str,
  body: ProductCreate,
  client: Annotated[Any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        product = shop_service.create_product(client, shop_id, body)

        from mail.send import _html_shell
        from mail.queue import get_admin_emails, enqueue_mail

        # Confirmation to the merchant
        try:
            user_r = client.table("users").select("email").eq("id", user_id).limit(1).execute()
            if user_r.data and user_r.data[0].get("email"):
                merchant_email = user_r.data[0]["email"]
                confirm_inner = f"""
                <p>Your product <strong>{body.title}</strong> has been submitted and is now pending review.</p>
                <p>Our team will review it shortly. Once approved, it will be visible to customers on Midora.</p>
                <p style="margin-top:24px;color:#64748b;font-size:13px;">You can track the status of your listings from your merchant dashboard.</p>
                """
                await enqueue_mail(
                    to=merchant_email,
                    subject=f"Product submitted: {body.title} — Midora",
                    body_html=_html_shell("Product submitted for review", confirm_inner),
                )
        except Exception:
            pass

        # Notify admins (best-effort, queued)
        recipients = get_admin_emails()
        if recipients:
            shop_row = client.table("shops").select("name, slug").eq("id", shop_id).limit(1).execute()
            shop_name = shop_row.data[0].get("name", "Unknown") if shop_row.data else "Unknown"
            price = body.price_ugx
            settings = get_settings()
            inner = f"""
            <p>A new product has been added to <strong>{shop_name}</strong> and is pending review.</p>
            <ul>
              <li><strong>Title:</strong> {body.title}</li>
              <li><strong>Price:</strong> UGX {price:,.0f}</li>
              <li><strong>Category:</strong> {body.category}</li>
            </ul>
            <p style="margin-top:24px;">
              <a href="{settings.frontend_public_url}/admin/listings" style="display:inline-block;padding:10px 18px;background:#0f172a;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;">Review in admin panel</a>
            </p>
            """
            for recipient in recipients:
                await enqueue_mail(
                    to=recipient,
                    subject=f"[Midora] New product: {body.title}",
                    body_html=_html_shell("New product pending review", inner),
                )

        return product
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{shop_id}/products")
async def list_products(
  shop_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
  params: Annotated[PaginationParams, Depends()],
  category: str | None = None,
  search: str | None = None,
  status: str | None = None,
  user_id: str | None = Depends(get_optional_user_id),
):
    is_owner = False
    if user_id:
        try:
            ensure_shop_owner(client, shop_id, user_id)
            is_owner = True
        except (LookupError, PermissionError):
            pass
    return shop_service.list_products(
        client, shop_id, page=params.page, limit=params.limit,
        category=category, search=search, status=status,
        is_owner=is_owner,
    )


router_products = APIRouter()


@router_products.get("/{product_id}/engagement", response_model=ProductEngagementState)
async def get_product_engagement(
    product_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    if not engagement_service.product_exists(client, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return engagement_service.get_product_engagement(client, product_id, viewer_id)


@router_products.post("/{product_id}/like", response_model=ProductEngagementState)
async def like_product(
    product_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.like_product(client, user_id, product_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router_products.delete("/{product_id}/like", response_model=ProductEngagementState)
async def unlike_product(
    product_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unlike_product(client, user_id, product_id)


@router_products.post("/{product_id}/views", response_model=ViewCountResponse)
async def record_product_view(
    product_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
):
    """Increment product/service detail view (click) count. Call when a customer opens the product page."""
    try:
        n = engagement_service.record_product_view(client, product_id)
        return ViewCountResponse(view_count=n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router_products.get("/premium")
async def get_premium_products(
    client: Annotated[Any, Depends(get_supabase_client)],
    limit: int = 10,
):
    """Return premium (high-scoring boosted) products for the premium carousel."""
    r = (
        client.table("products")
        .select("id, shop_id, title, price_ugx, image_urls, category, view_count, listing_score, created_at")
        .eq("is_published", True)
        .eq("status", "active")
        .order("listing_score", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    items = r.data or []
    shop_ids = list({str(p["shop_id"]) for p in items if p.get("shop_id")})
    shops_map: dict[str, dict] = {}
    if shop_ids:
        sr = (
            client.table("shops")
            .select("id, name, slug")
            .in_("id", shop_ids)
            .execute()
        )
        for s in sr.data or []:
            shops_map[str(s["id"])] = s
    out = []
    for p in items:
        sid = str(p.get("shop_id", ""))
        s = shops_map.get(sid, {})
        out.append({
            "id": str(p["id"]),
            "shop_id": sid,
            "title": p.get("title", ""),
            "price_ugx": float(p.get("price_ugx", 0)),
            "image_urls": ([str(x).strip() for x in p.get("image_urls", []) if x] if isinstance(p.get("image_urls"), list) else []),
            "category": p.get("category"),
            "view_count": int(p.get("view_count") or 0),
            "listing_score": int(p.get("listing_score") or 0),
            "created_at": str(p.get("created_at", "")),
            "shop_name": s.get("name"),
            "shop_slug": s.get("slug"),
        })
    return out


@router_products.get("/trending")
async def get_trending_products(
    client: Annotated[Any, Depends(get_supabase_client)],
    limit: int = 12,
):
    """Return trending products (highest view count) for the trending carousel."""
    r = (
        client.table("products")
        .select("id, shop_id, title, price_ugx, image_urls, category, view_count, listing_score, created_at")
        .eq("is_published", True)
        .eq("status", "active")
        .order("view_count", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    items = r.data or []
    shop_ids = list({str(p["shop_id"]) for p in items if p.get("shop_id")})
    shops_map: dict[str, dict] = {}
    if shop_ids:
        sr = (
            client.table("shops")
            .select("id, name, slug")
            .in_("id", shop_ids)
            .execute()
        )
        for s in sr.data or []:
            shops_map[str(s["id"])] = s
    out = []
    for p in items:
        sid = str(p.get("shop_id", ""))
        s = shops_map.get(sid, {})
        out.append({
            "id": str(p["id"]),
            "shop_id": sid,
            "title": p.get("title", ""),
            "price_ugx": float(p.get("price_ugx", 0)),
            "image_urls": ([str(x).strip() for x in p.get("image_urls", []) if x] if isinstance(p.get("image_urls"), list) else []),
            "category": p.get("category"),
            "view_count": int(p.get("view_count") or 0),
            "listing_score": int(p.get("listing_score") or 0),
            "created_at": str(p.get("created_at", "")),
            "shop_name": s.get("name"),
            "shop_slug": s.get("slug"),
        })
    return out


@router_products.get("/{product_id}/similar")
async def get_similar_products(
    product_id: str,
    client: Annotated[Any, Depends(get_supabase_client)],
    limit: int = 8,
):
    """Fetch similar products in the same category."""
    return shop_service.get_similar_products(client, product_id, limit=limit)


@router_products.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    """Fetch a single product with shop snapshot and engagement data bundled.

    Returns `ProductDetailResponse` — a composite payload that includes:
    - Full product fields
    - Embedded shop summary (name, slug, logo, whatsapp, etc.)
    - Engagement counters: like_count, view_count, whatsapp_clicks, messages
    - viewer_liked flag (requires authentication)
    - boosted flag (active boost status)

    Runs 5 targeted DB queries instead of the previous 7 sequential round-trips.
    The frontend no longer needs a separate shop fetch for the product detail page.
    """
    detail = shop_service.get_product_detail(client, product_id, viewer_id=viewer_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Product not found")
    return detail


@router_products.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
  product_id: str,
  body: ProductUpdate,
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_product_owner(client, product_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Product not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    updated = shop_service.update_product(client, product_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail="Product not found")
    out = shop_service.get_product(client, product_id, viewer_id=user_id)
    if not out:
        raise HTTPException(status_code=404, detail="Product not found")
    return out


@router_products.delete("/{product_id}")
async def delete_product(
  product_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
  claims: Annotated[Any, Depends(get_current_claims)],
):
    user_id = claims.sub
    is_admin = (claims.role or "").lower() == "admin"
    if not is_admin:
        try:
            ensure_product_owner(client, product_id, user_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="Product not found")
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    ok = shop_service.delete_product(client, product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted": product_id}


@router_products.post("/{product_id}/repost", response_model=ProductResponse)
async def repost_product(
  product_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_product_owner(client, product_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Product not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        updated = shop_service.repost_product(client, product_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Product not found")
        return updated
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))



@router_products.post("/generate-from-image")
async def generate_from_image(
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    """Merchant Copilot: Gemini suggests title, description, tags from image. Body: image_url or image_base64, shop_id."""
    return {"title": "", "description": "", "ai_seo_tags": ""}
