from typing import Annotated
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from core.authz import ensure_product_owner, ensure_shop_owner
from core.config import get_settings
from core.schemas import PaginationParams
from db.supabase import get_supabase_admin, get_supabase_client
from core.security import TokenPayload, get_current_claims, get_current_user_id, get_optional_user_id
from mail.queue import enqueue_mail, filter_recipients, get_admin_emails
from mail.templates import (
    build_new_product_admin_notification,
    build_product_submitted_confirmation,
)
from ranking.service import calculate_listing_score
from shop import engagement_service, service as shop_service
from shop.schemas import (
    DiscountSet,
    PaginatedProductCards,
    ProductCard,
    ProductCreate,
    ProductDetailResponse,
    ProductEngagementState,
    ProductResponse,
    ProductUpdate,
    ViewCountResponse,
)
from shop.serializers import serialize_product_card

logger = logging.getLogger(__name__)

router = APIRouter()


async def _notify_product_submission(
    *,
    client: Client,
    user_id: str,
    shop_id: str,
    body: ProductCreate,
) -> None:
    """Best-effort merchant confirmation + admin notification for a new product.

    All email work is queued (writes go through `mail_queue`) so route latency
    stays flat. Exceptions from DB reads or enqueue are logged, never raised.
    """
    merchant_email: str | None = None
    try:
        user_r = client.table("users").select("email").eq("id", user_id).limit(1).execute()
        if user_r.data and user_r.data[0].get("email"):
            merchant_email = user_r.data[0]["email"]
    except Exception as exc:
        logger.warning("lookup merchant email failed for user %s: %s", user_id, exc)

    if merchant_email:
        subject, body_html = build_product_submitted_confirmation(product_title=body.title)
        try:
            await enqueue_mail(to=merchant_email, subject=subject, body_html=body_html)
        except Exception as exc:
            logger.warning("enqueue merchant confirmation failed: %s", exc)

    recipients = filter_recipients(get_admin_emails(), merchant_email)
    if not recipients:
        return

    shop_name = "Unknown"
    try:
        shop_row = client.table("shops").select("name").eq("id", shop_id).limit(1).execute()
        if shop_row.data:
            shop_name = shop_row.data[0].get("name", "Unknown")
    except Exception as exc:
        logger.warning("lookup shop name failed for %s: %s", shop_id, exc)

    settings = get_settings()
    admin_url = f"{settings.frontend_public_url}/admin/listings"
    subject, body_html = build_new_product_admin_notification(
        shop_name=shop_name,
        product_title=body.title,
        price_ugx=body.price_ugx,
        category=body.category,
        admin_listings_url=admin_url,
    )
    for recipient in recipients:
        try:
            await enqueue_mail(to=recipient, subject=subject, body_html=body_html)
        except Exception as exc:
            logger.warning("enqueue admin notification failed for %s: %s", recipient, exc)


@router.post("/{shop_id}/products", response_model=ProductResponse)
async def create_product(
  shop_id: str,
  body: ProductCreate,
  client: Annotated[Client, Depends(get_supabase_client)],
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _notify_product_submission(
        client=client, user_id=user_id, shop_id=shop_id, body=body,
    )
    return product


@router.get("/{shop_id}/products")
async def list_products(
  shop_id: str,
  client: Annotated[Client, Depends(get_supabase_client)],
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


@router_products.get("/me/liked", response_model=PaginatedProductCards)
async def my_liked_products(
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Return all products the authenticated user has liked."""
    admin = get_supabase_admin()
    offset = (page - 1) * limit

    lr = (
        admin.table("product_likes")
        .select("product_id, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    product_ids = [str(r["product_id"]) for r in (lr.data or []) if r.get("product_id")]
    total = len(product_ids)
    page_ids = product_ids[offset:offset + limit]

    if not page_ids:
        return PaginatedProductCards(items=[], total=0, page=page, limit=limit)

    pr = (
        admin.table("products")
        .select(
            "id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,"
            "status,listing_score,location_name,is_published,is_negotiable,view_count,created_at"
        )
        .in_("id", page_ids)
        .execute()
    )
    by_id = {str(r["id"]): r for r in (pr.data or []) if r.get("id")}

    shop_ids = list({str(r["shop_id"]) for r in (pr.data or []) if r.get("shop_id")})
    shops_map: dict[str, dict] = {}
    if shop_ids:
        try:
            sr = (
                admin.table("shops")
                .select("id, name, slug, whatsapp_number, owner_id, is_active, trust_badges, available_now")
                .in_("id", shop_ids)
                .execute()
            )
            for s in sr.data or []:
                shops_map[str(s["id"])] = s
        except Exception as exc:
            logger.warning("my_liked_products: shop lookup failed: %s", exc)

    avg_ratings: dict[str, float] = {}
    review_counts: dict[str, int] = {}
    try:
        rev_r = (
            admin.table("product_reviews")
            .select("product_id,rating")
            .in_("product_id", page_ids)
            .execute()
        )
        sums: dict[str, float] = {}
        for rev in rev_r.data or []:
            pid = str(rev.get("product_id"))
            rating = rev.get("rating")
            if pid and rating:
                sums[pid] = sums.get(pid, 0) + float(rating)
                review_counts[pid] = review_counts.get(pid, 0) + 1
        for pid in page_ids:
            if review_counts.get(pid, 0) > 0:
                avg_ratings[pid] = round(sums[pid] / review_counts[pid], 2)
    except Exception as exc:
        logger.warning("my_liked_products: reviews lookup failed: %s", exc)

    items: list[ProductCard] = []
    for pid in page_ids:
        row = by_id.get(pid)
        if not row:
            continue
        items.append(serialize_product_card(
            row,
            shop_row=shops_map.get(str(row["shop_id"])),
            average_rating=avg_ratings.get(pid, 0.0),
            review_count=review_counts.get(pid, 0),
            first_image_only=True,
        ))
    return PaginatedProductCards(items=items, total=total, page=page, limit=limit)


@router_products.get("/{product_id}/engagement", response_model=ProductEngagementState)
async def get_product_engagement(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    if not engagement_service.product_exists(client, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return engagement_service.get_product_engagement(client, product_id, viewer_id)


@router_products.post("/{product_id}/like", response_model=ProductEngagementState)
async def like_product(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        result = engagement_service.like_product(client, user_id, product_id)
        calculate_listing_score(product_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router_products.delete("/{product_id}/like", response_model=ProductEngagementState)
async def unlike_product(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    result = engagement_service.unlike_product(client, user_id, product_id)
    calculate_listing_score(product_id)
    return result


@router_products.post("/{product_id}/views", response_model=ViewCountResponse)
async def record_product_view(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str | None = Depends(get_optional_user_id),
):
    """Increment product view count and record a per-user viewed event when authenticated."""
    try:
        n = engagement_service.record_product_view(client, product_id, buyer_id=user_id)
        calculate_listing_score(product_id)
        return ViewCountResponse(view_count=n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _fetch_carousel_products(
    client: Client,
    *,
    order_by: str,
    limit: int,
) -> list[ProductCard]:
    """Shared query for the trending & premium carousels.

    Both endpoints share the exact same shape and joins — they only differ in
    the ordering column, so we thread that in and let a single serializer own
    the projection.
    """
    r = (
        client.table("products")
        .select(
            "id, shop_id, title, price_ugx, discount_price, discount_expires_at, "
            "image_urls, category, view_count, listing_score, created_at"
        )
        .eq("is_published", True)
        .eq("status", "active")
        .order(order_by, desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    products = r.data or []
    shop_ids = list({str(p["shop_id"]) for p in products if p.get("shop_id")})

    shops_map: dict[str, dict] = {}
    if shop_ids:
        try:
            sr = (
                client.table("shops")
                .select("id, name, slug")
                .in_("id", shop_ids)
                .execute()
            )
            for s in sr.data or []:
                shops_map[str(s["id"])] = s
        except Exception as exc:
            logger.warning("carousel shop lookup failed (order=%s): %s", order_by, exc)

    return [
        serialize_product_card(p, shop_row=shops_map.get(str(p.get("shop_id", ""))))
        for p in products
    ]


@router_products.get("/premium", response_model=list[ProductCard])
async def get_premium_products(
    client: Annotated[Client, Depends(get_supabase_client)],
    limit: int = Query(10, ge=1, le=50),
) -> list[ProductCard]:
    """Return premium (high-scoring boosted) products for the premium carousel."""
    return _fetch_carousel_products(client, order_by="listing_score", limit=limit)


@router_products.get("/trending", response_model=list[ProductCard])
async def get_trending_products(
    client: Annotated[Client, Depends(get_supabase_client)],
    limit: int = Query(12, ge=1, le=50),
) -> list[ProductCard]:
    """Return trending products (highest view count) for the trending carousel."""
    return _fetch_carousel_products(client, order_by="view_count", limit=limit)


@router_products.get("/{product_id}/similar")
async def get_similar_products(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    limit: int = 8,
):
    """Fetch similar products in the same category."""
    return shop_service.get_similar_products(client, product_id, limit=limit)


@router_products.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
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
  client: Annotated[Client, Depends(get_supabase_client)],
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
  client: Annotated[Client, Depends(get_supabase_client)],
  claims: Annotated[TokenPayload, Depends(get_current_claims)],
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
  client: Annotated[Client, Depends(get_supabase_client)],
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


@router_products.post("/{product_id}/discount", response_model=ProductResponse)
async def set_product_discount(
    product_id: str,
    body: DiscountSet,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Set or remove a discount on a product. Pass discount_price=null to remove discount."""
    try:
        ensure_product_owner(client, product_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Product not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    payload: dict[str, object] = {"discount_price": body.discount_price}
    if body.discount_expires_at is not None:
        payload["discount_expires_at"] = body.discount_expires_at
    elif body.discount_price is not None:
        payload["discount_expires_at"] = None

    r = client.table("products").update(payload).eq("id", product_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Product not found")

    from ranking.service import calculate_listing_score
    calculate_listing_score(product_id)

    out = shop_service.get_product(client, product_id, viewer_id=user_id)
    if not out:
        raise HTTPException(status_code=404, detail="Product not found")
    return out


@router_products.post("/{product_id}/toggle-availability", response_model=ProductResponse)
async def toggle_product_availability(
    product_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Toggle whether a product is published/available on the storefront."""
    try:
        ensure_product_owner(client, product_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Product not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    current = client.table("products").select("is_published").eq("id", product_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Product not found")

    new_val = not bool(current.data[0].get("is_published", True))
    r = client.table("products").update({"is_published": new_val}).eq("id", product_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Product not found")

    from ranking.service import calculate_listing_score
    calculate_listing_score(product_id)

    out = shop_service.get_product(client, product_id, viewer_id=user_id)
    if not out:
        raise HTTPException(status_code=404, detail="Product not found")
    return out



@router_products.post("/generate-from-image")
async def generate_from_image(
  client: Annotated[Client, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    """Merchant Copilot: Gemini suggests title, description, tags from image. Body: image_url or image_base64, shop_id."""
    return {"title": "", "description": "", "ai_seo_tags": ""}
