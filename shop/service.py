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
    ProductListItem,
    ProductResponse,
    ProductUpdate,
)

_PRODUCT_LIST_COLS_WITH_VIEWS = (
    "id,shop_id,title,description,price_ugx,image_urls,category,is_published,created_at,view_count"
)
_PRODUCT_LIST_COLS_BASE = (
    "id,shop_id,title,description,price_ugx,image_urls,category,is_published,created_at"
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
            q = q.eq("is_published", True)
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
    }
    imgs = _image_urls_for_db(data.image_urls)
    if imgs is not None:
        payload["image_urls"] = imgs
    r = client.table("products").insert(payload).execute()
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create product")
    return _row_to_product_response(r.data[0])


def get_product(client: Any, product_id: str, viewer_id: str | None = None) -> dict | None:
    r = client.table("products").select("*").eq("id", product_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    out = _row_to_product_response(r.data[0])
    out.update(engagement_service.get_product_engagement(client, product_id, viewer_id))
    return out


def update_product(client: Any, product_id: str, data: ProductUpdate) -> dict | None:
    payload = data.model_dump(exclude_unset=True)
    if data.image_urls is not None:
        payload["image_urls"] = _image_urls_for_db(data.image_urls) or []
    if not payload:
        return get_product(client, product_id, viewer_id=None)
    r = client.table("products").update(payload).eq("id", product_id).execute()
    if not r.data or len(r.data) == 0:
        return None
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
        "stock_quantity": row.get("stock_quantity", 0),
        "image_urls": image_urls,
        "category": row.get("category"),
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
