from typing import Any

from postgrest.exceptions import APIError

from core.postgrest_compat import is_undefined_column_error
from shop import engagement_service
from core.categories import normalize_category
from shop.schemas import (
    OrderCreate,
    OrderListItem,
    OrderResponse,
    ProductCreate,
    ProductDetailResponse,
    ProductListItem,
    ProductResponse,
    ProductUpdate,
    ShopSummary,
)


_PRODUCT_LIST_COLS_WITH_VIEWS = (
    "id,shop_id,title,description,price_ugx,image_urls,category,item_type,status,"
    "listing_score,location_name,is_published,created_at,view_count"
)
_PRODUCT_LIST_COLS_BASE = (
    "id,shop_id,title,description,price_ugx,image_urls,category,item_type,"
    "is_published,created_at"
)


def list_products(
    client: Any,
    shop_id: str,
    page: int = 1,
    limit: int = 20,
    category: str | None = None,
    search: str | None = None,
    is_owner: bool = False,
) -> dict:
    """List products for a shop. If not owner, only is_published=True."""
    limit = min(limit, 100)
    offset = (page - 1) * limit

    def _run_list(select_cols: str):
        q = client.table("products").select(select_cols, count="exact").eq("shop_id", shop_id)
        if not is_owner:
            q = q.eq("is_published", True).eq("status", "active")
        if category:
            try:
                cat = normalize_category(category)
            except ValueError:
                cat = category.strip()
            if cat:
                q = q.eq("category", cat)
        if search:
            q = q.or_(f"title.ilike.%{search}%,description.ilike.%{search}%")
        return q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()

    try:
        r = _run_list(_PRODUCT_LIST_COLS_WITH_VIEWS)
    except APIError as exc:
        if is_undefined_column_error(exc):
            r = _run_list(_PRODUCT_LIST_COLS_BASE)
        else:
            raise
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = []
    for row in (r.data or []):
        image_urls = row.get("image_urls")
        if isinstance(image_urls, str):
            image_urls = [image_urls] if image_urls else []
        items.append(
            ProductListItem(
                id=str(row["id"]),
                shop_id=str(row["shop_id"]),
                title=row.get("title", ""),
                description=row.get("description"),
                price_ugx=float(row.get("price_ugx", 0)),
                image_urls=image_urls[:1] if image_urls else None,
                category=row.get("category"),
                is_published=row.get("is_published", True),
                item_type=row.get("item_type"),
                status=row.get("status"),
                listing_score=int(row.get("listing_score") or 0),
                location_name=row.get("location_name"),
                created_at=str(row["created_at"]) if row.get("created_at") else None,
                view_count=int(row.get("view_count") or 0),
            )
        )
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def _image_urls_for_db(value: list[str] | None) -> list[str] | None:
    """Postgres column is TEXT[]; PostgREST expects a JSON array, not a comma-separated string."""
    if value is None:
        return None
    return list(value)


def create_product(client: Any, shop_id: str, data: ProductCreate) -> dict:
    payload = {
        "shop_id": shop_id,
        "title": data.title,
        "description": data.description,
        "price_ugx": data.price_ugx,
        "stock_quantity": data.stock_quantity,
        "category": data.category,
        "is_published": data.is_published,
        "item_type": data.item_type or "product",
        "location_name": data.location_name,
        "status": "pending_review",
    }
    imgs = _image_urls_for_db(data.image_urls)
    if imgs is not None:
        payload["image_urls"] = imgs
    r = client.table("products").insert(payload).execute()
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create product")
    result = _row_to_product_response(r.data[0])
    from ranking.service import calculate_listing_score
    calculate_listing_score(str(r.data[0]["id"]))
    from feed.embeddings import refresh_product_embedding
    refresh_product_embedding(str(r.data[0]["id"]))
    return result


def get_similar_products(client: Any, product_id: str, limit: int = 8) -> list[dict]:
    """Fetch products in the same category, excluding the current product."""
    product = get_product(client, product_id)
    if not product or not product.get("category"):
        return []
    try:
        r = (
            client.table("products")
            .select("id,shop_id,title,price_ugx,image_urls,category,item_type,"
                    "listing_score,location_name,is_published,created_at,view_count")
            .eq("category", product["category"])
            .eq("is_published", True)
            .eq("status", "active")
            .neq("id", product_id)
            .order("listing_score", desc=True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        r = (
            client.table("products")
            .select("id,shop_id,title,price_ugx,image_urls,category,item_type,"
                    "is_published,created_at")
            .eq("category", product["category"])
            .eq("is_published", True)
            .eq("status", "active")
            .neq("id", product_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    out = []
    shop_ids = list({str(row["shop_id"]) for row in (r.data or []) if row.get("shop_id")})
    shops_map: dict[str, dict] = {}
    if shop_ids:
        try:
            sr = client.table("shops").select("id, name, slug").in_("id", shop_ids).execute()
            for s in sr.data or []:
                shops_map[str(s["id"])] = s
        except Exception:
            pass
    for row in (r.data or []):
        imgs = row.get("image_urls")
        if isinstance(imgs, str):
            imgs = [imgs] if imgs else []
        sid = str(row.get("shop_id", ""))
        s = shops_map.get(sid, {})
        out.append({
            "id": str(row["id"]),
            "shop_id": sid,
            "title": row.get("title", ""),
            "price_ugx": float(row.get("price_ugx", 0)),
            "image_urls": imgs[:1] if imgs else None,
            "category": row.get("category"),
            "item_type": row.get("item_type"),
            "listing_score": int(row.get("listing_score") or 0),
            "location_name": row.get("location_name"),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "view_count": int(row.get("view_count") or 0),
            "shop_name": s.get("name"),
            "shop_slug": s.get("slug"),
            "owner_id": str(s.get("owner_id")) if s.get("owner_id") else None,
        })
    return out


def get_product(client: Any, product_id: str, viewer_id: str | None = None) -> dict | None:
    r = client.table("products").select("*").eq("id", product_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    row = r.data[0]
    shop_id = row.get("shop_id", "")
    is_owner = bool(viewer_id and shop_id and bool(
        client.table("shops").select("user_id").eq("id", shop_id).eq("user_id", viewer_id).limit(1).execute().data
    ))
    if not is_owner and (row.get("status") != "active" or not row.get("is_published")):
        return None
    out = _row_to_product_response(row)
    out.update(engagement_service.get_product_engagement(client, product_id, viewer_id))
    return out


def get_product_detail(
    client: Any,
    product_id: str,
    viewer_id: str | None = None,
) -> ProductDetailResponse | None:
    """Batched product detail fetch — 3 queries instead of 7.

    Query plan:
      Q1  products      — full product row (includes view_count, status, etc.)
      Q2  shops         — shop snapshot + owner_id for visibility check
      Q3  product_likes — like count (count=exact) + viewer_liked in one pass
      Q4  listing_events — whatsapp_clicks + messages in a single grouped query
      Q5  listing_boosts — active boost check (lightweight, single row expected)

    The shop snapshot is embedded in the response, eliminating the separate
    frontend shop fetch that previously caused an extra round-trip.
    """
    from datetime import datetime, timezone

    # -------------------------------------------------------------------
    # Q1: Product row
    # -------------------------------------------------------------------
    prod_r = (
        client.table("products")
        .select(
            "id,shop_id,title,description,price_ugx,stock_quantity,image_urls,"
            "category,item_type,status,is_published,listing_score,location_name,"
            "ai_seo_tags,ai_generated_desc,created_at,view_count"
        )
        .eq("id", product_id)
        .limit(1)
        .execute()
    )
    if not prod_r.data:
        return None
    row = prod_r.data[0]

    # -------------------------------------------------------------------
    # Q2: Shop row — owner check + embedded snapshot
    # -------------------------------------------------------------------
    shop_id = str(row.get("shop_id", ""))
    shop_snapshot: ShopSummary | None = None
    is_owner = False

    if shop_id:
        try:
            shop_r = (
                client.table("shops")
                .select(
                    "id,name,slug,logo_url,owner_id,whatsapp_number,"
                    "is_active,trust_score,available_now,location"
                )
                .eq("id", shop_id)
                .limit(1)
                .execute()
            )
            if shop_r.data:
                s = shop_r.data[0]
                loc = s.get("location")
                location_str = loc.get("display") if isinstance(loc, dict) else loc
                is_owner = bool(viewer_id and str(s.get("owner_id", "")) == viewer_id)
                shop_snapshot = ShopSummary(
                    id=str(s["id"]),
                    name=s.get("name", ""),
                    slug=s.get("slug"),
                    logo_url=s.get("logo_url"),
                    whatsapp_number=s.get("whatsapp_number"),
                    is_active=bool(s.get("is_active", True)),
                    trust_score=int(s.get("trust_score") or 0),
                    available_now=bool(s.get("available_now", False)),
                    location=location_str,
                )
        except Exception:
            pass

    # Visibility gate — non-owners can't see inactive/unpublished listings
    if not is_owner and (row.get("status") != "active" or not row.get("is_published")):
        return None

    # -------------------------------------------------------------------
    # Q3: Like count + viewer_liked (single query using count=exact)
    # -------------------------------------------------------------------
    like_count = 0
    viewer_liked: bool | None = None
    try:
        likes_q = (
            client.table("product_likes")
            .select("user_id", count="exact")
            .eq("product_id", product_id)
        )
        likes_r = likes_q.execute()
        like_count = int(likes_r.count or 0)
        if viewer_id:
            viewer_liked = any(
                str(row_.get("user_id", "")) == viewer_id
                for row_ in (likes_r.data or [])
            )
    except Exception:
        pass

    # -------------------------------------------------------------------
    # Q4: Listing events — both whatsapp_clicks and messages in one query
    # -------------------------------------------------------------------
    whatsapp_clicks = 0
    messages = 0
    try:
        events_r = (
            client.table("listing_events")
            .select("event_type")
            .eq("listing_id", product_id)
            .in_("event_type", ["whatsapp_clicked", "messaged"])
            .execute()
        )
        for evt in (events_r.data or []):
            t = evt.get("event_type")
            if t == "whatsapp_clicked":
                whatsapp_clicks += 1
            elif t == "messaged":
                messages += 1
    except Exception:
        pass

    # -------------------------------------------------------------------
    # Q5: Active boost check
    # -------------------------------------------------------------------
    boosted = False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        boost_r = (
            client.table("listing_boosts")
            .select("id")
            .eq("listing_id", product_id)
            .eq("active", True)
            .gte("ends_at", now_iso)
            .limit(1)
            .execute()
        )
        boosted = bool(boost_r.data)
    except Exception:
        pass

    # -------------------------------------------------------------------
    # Assemble response
    # -------------------------------------------------------------------
    image_urls = row.get("image_urls") or []
    if isinstance(image_urls, str):
        image_urls = [s.strip() for s in image_urls.split(",") if s.strip()]
    else:
        image_urls = [str(x).strip() for x in image_urls if x is not None and str(x).strip()]

    return ProductDetailResponse(
        id=str(row["id"]),
        shop_id=shop_id,
        title=row.get("title", ""),
        description=row.get("description"),
        price_ugx=float(row.get("price_ugx", 0)),
        stock_quantity=int(row.get("stock_quantity") or 0),
        image_urls=image_urls,
        category=row.get("category"),
        item_type=row.get("item_type"),
        status=row.get("status"),
        is_published=bool(row.get("is_published", True)),
        listing_score=int(row.get("listing_score") or 0),
        location_name=row.get("location_name"),
        ai_seo_tags=row.get("ai_seo_tags"),
        ai_generated_desc=bool(row.get("ai_generated_desc", False)),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        like_count=like_count,
        view_count=int(row.get("view_count") or 0),
        viewer_liked=viewer_liked,
        whatsapp_clicks=whatsapp_clicks,
        messages=messages,
        boosted=boosted,
        shop=shop_snapshot,
    )




def update_product(client: Any, product_id: str, data: ProductUpdate) -> dict | None:
    payload = data.model_dump(exclude_unset=True)
    if data.image_urls is not None:
        payload["image_urls"] = _image_urls_for_db(data.image_urls) or []
    if not payload:
        return get_product(client, product_id, viewer_id=None)
    r = client.table("products").update(payload).eq("id", product_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    from feed.embeddings import refresh_product_embedding
    refresh_product_embedding(product_id)
    return _row_to_product_response(r.data[0])


def delete_product(client: Any, product_id: str) -> bool:
    r = client.table("products").delete().eq("id", product_id).execute()
    return bool(r.data)


def repost_product(client: Any, product_id: str) -> dict | None:
    from datetime import datetime, timezone, timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    
    logs = client.table("product_reposts_log").select("id").eq("product_id", product_id).gte("created_at", yesterday).execute()
    
    if logs.data and len(logs.data) >= 2:
        raise ValueError("Repost limit reached (2x per 24 hours)")
        
    # Insert log
    client.table("product_reposts_log").insert({"product_id": product_id}).execute()
    
    # Update product created_at
    r = client.table("products").update({"created_at": datetime.now(timezone.utc).isoformat()}).eq("id", product_id).execute()
    
    if not r.data or len(r.data) == 0:
        return None
    return _row_to_product_response(r.data[0])


def _row_to_product_response(row: dict) -> dict:
    image_urls = row.get("image_urls")
    if isinstance(image_urls, list):
        image_urls = [str(x).strip() for x in image_urls if x is not None and str(x).strip()]
    elif isinstance(image_urls, str) and image_urls:
        image_urls = [s.strip() for s in image_urls.split(",")]
    elif not image_urls:
        image_urls = []
    return {
        "id": str(row["id"]),
        "shop_id": str(row["shop_id"]),
        "title": row.get("title", ""),
        "description": row.get("description"),
        "price_ugx": float(row.get("price_ugx", 0)),
        "stock_quantity": row.get("stock_quantity") or 0,
        "image_urls": image_urls,
        "category": row.get("category"),
        "item_type": row.get("item_type", "product"),
        "status": row.get("status", "active"),
        "listing_score": int(row.get("listing_score") or 0),
        "location_name": row.get("location_name"),
        "ai_seo_tags": row.get("ai_seo_tags"),
        "ai_generated_desc": row.get("ai_generated_desc", False),
        "is_published": row.get("is_published", True),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "like_count": 0,
        "view_count": int(row.get("view_count") or 0),
        "viewer_liked": None,
    }


def list_orders(
    client: Any,
    page: int = 1,
    limit: int = 20,
    shop_id: str | None = None,
    customer_id: str | None = None,
) -> dict:
    limit = min(limit, 100)
    offset = (page - 1) * limit
    q = client.table("orders").select("id,customer_id,shop_id,total_amount,order_status,created_at", count="exact")
    if shop_id:
        q = q.eq("shop_id", shop_id)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    q = q.range(offset, offset + limit - 1).order("created_at", desc=True)
    r = q.execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = [
        OrderListItem(
            id=str(row["id"]),
            shop_id=str(row["shop_id"]),
            total_amount=float(row.get("total_amount", 0)),
            order_status=row.get("order_status", "pending"),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
        )
        for row in (r.data or [])
    ]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def create_order(client: Any, customer_id: str, data: OrderCreate) -> dict:
    r = client.table("orders").insert(
        {"customer_id": customer_id, "shop_id": data.shop_id, "total_amount": data.total_amount}
    ).execute()
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create order")
    row = r.data[0]
    return {
        "id": str(row["id"]),
        "customer_id": str(row["customer_id"]),
        "shop_id": str(row["shop_id"]),
        "total_amount": float(row.get("total_amount", 0)),
        "order_status": row.get("order_status", "pending"),
        "pesapal_tracking_id": row.get("pesapal_tracking_id"),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }


def update_order_status(client: Any, order_id: str, order_status: str) -> dict | None:
    r = client.table("orders").update({"order_status": order_status}).eq("id", order_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    row = r.data[0]
    return {
        "id": str(row["id"]),
        "customer_id": str(row["customer_id"]),
        "shop_id": str(row["shop_id"]),
        "total_amount": float(row.get("total_amount", 0)),
        "order_status": row.get("order_status", "pending"),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }
