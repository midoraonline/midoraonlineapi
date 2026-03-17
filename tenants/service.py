from typing import Any

from core.schemas import PaginatedResponse
from tenants.schemas import ShopCreate, ShopListItem, ShopResponse, ShopUpdate


def list_shops(
    client: Any,
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    shop_type: str | None = None,
) -> dict:
    """List shops (public). Paginated. Optional filter by shop_type (product, service, both)."""
    limit = min(limit, 100)
    offset = (page - 1) * limit
    q = client.table("shops").select(
        "id,name,slug,description,logo_url,shop_type,is_active,created_at", count="exact"
    )
    if search:
        q = q.or_(f"name.ilike.%{search}%,slug.ilike.%{search}%")
    if shop_type and shop_type in ("product", "service", "both"):
        q = q.eq("shop_type", shop_type)
    q = q.range(offset, offset + limit - 1).order("created_at", desc=True)
    r = q.execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = [
        ShopListItem(
            id=str(row["id"]),
            name=row.get("name", ""),
            slug=row.get("slug", ""),
            description=row.get("description"),
            logo_url=row.get("logo_url"),
            shop_type=row.get("shop_type") or "product",
            is_active=row.get("is_active", False),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
        )
        for row in (r.data or [])
    ]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def list_my_shops(client: Any, page: int = 1, limit: int = 20) -> dict:
    """List shops owned by current user (RLS filters by owner_id)."""
    limit = min(limit, 100)
    offset = (page - 1) * limit
    r = (
        client.table("shops")
        .select("id,name,slug,description,logo_url,shop_type,is_active,created_at", count="exact")
        .range(offset, offset + limit - 1)
        .order("created_at", desc=True)
        .execute()
    )
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = [
        ShopListItem(
            id=str(row["id"]),
            name=row.get("name", ""),
            slug=row.get("slug", ""),
            description=row.get("description"),
            logo_url=row.get("logo_url"),
            shop_type=row.get("shop_type") or "product",
            is_active=row.get("is_active", False),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
        )
        for row in (r.data or [])
    ]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def create_shop(client: Any, owner_id: str, data: ShopCreate) -> dict:
    """Create shop. Validate slug uniqueness. shop_type: product, service, or both."""
    r = client.table("shops").insert(
        {
            "owner_id": owner_id,
            "name": data.name,
            "slug": data.slug,
            "description": data.description,
            "about": data.about,
            "logo_url": data.logo_url,
            "shop_email": data.shop_email,
            "whatsapp_number": data.whatsapp_number,
            "contacts": data.contacts,
            "social_links": data.social_links,
            "location": data.location,
            "availability": data.availability,
            "theme_config": data.theme_config,
            "shop_type": data.shop_type,
        }
    ).execute()
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create shop")
    row = r.data[0]
    return _row_to_shop_response(row)


def get_shop(client: Any, shop_id: str) -> dict | None:
    """Get one shop by id."""
    r = client.table("shops").select("*").eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    return _row_to_shop_response(r.data[0])


def get_shop_by_slug(client: Any, slug: str) -> dict | None:
    """Get shop by slug."""
    r = client.table("shops").select("*").eq("slug", slug).execute()
    if not r.data or len(r.data) == 0:
        return None
    return _row_to_shop_response(r.data[0])


def update_shop(client: Any, shop_id: str, data: ShopUpdate) -> dict | None:
    """Update shop (partial). RLS: owner only."""
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return get_shop(client, shop_id)
    r = client.table("shops").update(payload).eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    return _row_to_shop_response(r.data[0])


def _row_to_shop_response(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "name": row.get("name", ""),
        "slug": row.get("slug", ""),
        "description": row.get("description"),
        "about": row.get("about"),
        "logo_url": row.get("logo_url"),
        "shop_email": row.get("shop_email"),
        "whatsapp_number": row.get("whatsapp_number"),
        "contacts": row.get("contacts"),
        "social_links": row.get("social_links"),
        "location": row.get("location"),
        "availability": row.get("availability"),
        "theme_config": row.get("theme_config"),
        "shop_type": row.get("shop_type") or "product",
        "is_active": row.get("is_active", False),
        "subscription_end_date": str(row["subscription_end_date"]) if row.get("subscription_end_date") else None,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }
