from db.supabase import get_supabase_admin


def list_all_shops(page: int = 1, limit: int = 20) -> dict:
    limit = min(limit, 100)
    offset = (page - 1) * limit
    admin = get_supabase_admin()
    r = (
        admin.table("shops")
        .select("id,owner_id,name,slug,shop_type,is_active,subscription_end_date,created_at", count="exact")
        .range(offset, offset + limit - 1)
        .order("created_at", desc=True)
        .execute()
    )
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    return {"items": r.data or [], "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def set_shop_active(shop_id: str, is_active: bool) -> dict | None:
    admin = get_supabase_admin()
    r = admin.table("shops").update({"is_active": is_active}).eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    return r.data[0]


def list_all_subscriptions() -> list:
    admin = get_supabase_admin()
    r = admin.table("subscriptions").select("*").order("created_at", desc=True).execute()
    return r.data or []
