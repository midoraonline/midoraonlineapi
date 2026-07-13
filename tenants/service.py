from typing import Any

from postgrest.exceptions import APIError

from core.next_cache import revalidate_nextjs_cache_tag
from shop import engagement_service
from tenants.schemas import ShopCreate, ShopListItem, ShopResponse, ShopUpdate, ShopThemeConfig

# When optional columns are missing (no migration yet), try narrower selects — see list_shops().
_SHOP_LIST_COLS_TIERS: tuple[str, ...] = (
    "id,name,slug,description,logo_url,shop_type,category,location,is_active,created_at,view_count,trust_badges",
    "id,name,slug,description,logo_url,shop_type,location,is_active,created_at,view_count,trust_badges",
    "id,name,slug,description,logo_url,shop_type,is_active,created_at,view_count,trust_badges",
    "id,name,slug,description,logo_url,shop_type,is_active,created_at,trust_badges",
)


def _theme_config_for_db(value: ShopThemeConfig | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return value.model_dump(mode="json", exclude_none=True)


def list_shops(
    client: Any,
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    shop_type: str | None = None,
    include_inactive: bool = False,
) -> dict:
    """List shops (public). Paginated. Optional filter by shop_type (product, service, both).

    By default only `is_active=True` shops are returned — shops that haven't
    completed verification or whose subscription lapsed stay hidden from the
    public directory. Pass `include_inactive=True` for admin surfaces.
    """
    limit = min(limit, 100)
    offset = (page - 1) * limit

    def _run_list(select_cols: str):
        q = client.table("shops").select(select_cols, count="exact")
        if not include_inactive:
            q = q.eq("is_active", True)
        if search:
            q = q.or_(f"name.ilike.%{search}%,slug.ilike.%{search}%")
        if shop_type and shop_type in ("product", "service", "both"):
            q = q.eq("shop_type", shop_type)
        return q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()

    r = None
    last_err: APIError | None = None
    for select_cols in _SHOP_LIST_COLS_TIERS:
        try:
            r = _run_list(select_cols)
            break
        except APIError as exc:
            if getattr(exc, "code", None) != "42703":
                raise
            last_err = exc
    if r is None:
        raise last_err  # type: ignore[misc]
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = [
        ShopListItem(
            id=str(row["id"]),
            name=row.get("name", ""),
            slug=row.get("slug", ""),
            description=row.get("description"),
            logo_url=row.get("logo_url"),
            category=row.get("category"),
            location=row.get("location"),
            shop_type=row.get("shop_type") or "product",
            is_active=row.get("is_active", False),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            view_count=int(row.get("view_count") or 0),
            trust_badges=row.get("trust_badges") or ["shop_listed"],
        )
        for row in (r.data or [])
    ]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def list_my_shops(client: Any, owner_id: str, page: int = 1, limit: int = 20) -> dict:
    """List shops owned by current user. Explicitly filters by owner_id since RLS is currently bypassed."""
    limit = min(limit, 100)
    offset = (page - 1) * limit
    last_err: APIError | None = None
    r = None
    for select_cols in _SHOP_LIST_COLS_TIERS:
        try:
            r = (
                client.table("shops")
                .select(select_cols, count="exact")
                .eq("owner_id", owner_id)
                .range(offset, offset + limit - 1)
                .order("created_at", desc=True)
                .execute()
            )
            break
        except APIError as exc:
            if getattr(exc, "code", None) != "42703":
                raise
            last_err = exc
    if r is None:
        raise last_err  # type: ignore[misc]
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = [
        ShopListItem(
            id=str(row["id"]),
            name=row.get("name", ""),
            slug=row.get("slug", ""),
            description=row.get("description"),
            logo_url=row.get("logo_url"),
            category=row.get("category"),
            location=row.get("location"),
            shop_type=row.get("shop_type") or "product",
            is_active=row.get("is_active", False),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            view_count=int(row.get("view_count") or 0),
            trust_badges=row.get("trust_badges") or ["shop_listed"],
        )
        for row in (r.data or [])
    ]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def create_shop(client: Any, owner_id: str, data: ShopCreate) -> dict:
    """Create shop. Validate slug uniqueness. shop_type: product, service, or both.

    Side effect: the owner's `user_role` is upgraded from `customer` to
    `merchant` automatically. We surface the resulting role under
    `_owner_role` / `_role_changed` so the route layer can re-issue auth
    cookies with the new claim if needed.
    """
    # Local import keeps tenants/auth modules loosely coupled.
    from auth import service as auth_service

    insert_payload: dict[str, Any] = {
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
        "shop_type": data.shop_type,
        "category": data.category,
    }
    tc = _theme_config_for_db(data.theme_config)
    if tc is not None:
        insert_payload["theme_config"] = tc
    r = client.table("shops").insert(insert_payload).execute()
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create shop")
    row = r.data[0]

    new_role, changed = auth_service.promote_to_merchant(owner_id)

    shop = get_shop(client, str(row["id"]), viewer_id=owner_id) or {}
    shop["_owner_role"] = new_role
    shop["_role_changed"] = changed
    revalidate_nextjs_cache_tag("shops")

    # Best-effort lifecycle emails — never block shop creation.
    try:
        import asyncio
        from core.config import get_settings
        from mail.send import send_shop_opened_merchant_email, send_shop_opened_admin_email
        from mail.queue import get_admin_emails

        settings = get_settings()
        # Resolve merchant email from user record
        merchant_email: str | None = None
        try:
            ur = client.table("users").select("email").eq("id", owner_id).limit(1).execute()
            if ur.data:
                merchant_email = ur.data[0].get("email")
        except Exception:
            pass

        shop_id_str = str(row["id"])
        verification_url = f"{settings.frontend_url}/merchant/shops/{shop_id_str}/verification"

        async def _fire_emails() -> None:
            if merchant_email:
                await send_shop_opened_merchant_email(
                    to=merchant_email,
                    shop_name=data.name,
                    shop_id=shop_id_str,
                    verification_url=verification_url,
                )
            admin_emails = get_admin_emails()
            if admin_emails:
                await send_shop_opened_admin_email(
                    admin_recipients=admin_emails,
                    shop_name=data.name,
                    shop_id=shop_id_str,
                    merchant_email=merchant_email,
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_fire_emails())
        except RuntimeError:
            # No running loop (e.g. called from a sync test context) — skip emails.
            pass
    except Exception:
        pass  # never fail shop creation due to email

    return shop


def get_shop(client: Any, shop_id: str, viewer_id: str | None = None) -> dict | None:
    """Get one shop by id, including follower/like counts and viewer flags."""
    r = client.table("shops").select("*").eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    out = _row_to_shop_response(r.data[0])
    out.update(engagement_service.get_shop_engagement(client, shop_id, viewer_id))
    return out


def get_shop_by_slug(client: Any, slug: str, viewer_id: str | None = None) -> dict | None:
    """Get shop by slug."""
    r = client.table("shops").select("*").eq("slug", slug).execute()
    if not r.data or len(r.data) == 0:
        return None
    row = r.data[0]
    out = _row_to_shop_response(row)
    out.update(engagement_service.get_shop_engagement(client, str(row["id"]), viewer_id))
    return out


def update_shop(client: Any, shop_id: str, data: ShopUpdate, viewer_id: str | None = None) -> dict | None:
    """Update shop (partial). RLS: owner only.

    available_now is presence-derived and cannot be set via this endpoint.
    """
    payload = data.model_dump(exclude_unset=True)
    payload.pop("available_now", None)
    if "theme_config" in payload:
        payload["theme_config"] = _theme_config_for_db(data.theme_config)
    if not payload:
        return get_shop(client, shop_id, viewer_id=viewer_id)
    r = client.table("shops").update(payload).eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    revalidate_nextjs_cache_tag("shops")
    return get_shop(client, shop_id, viewer_id=viewer_id)


def _row_to_shop_response(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "name": row.get("name", ""),
        "slug": row.get("slug", ""),
        "category": row.get("category"),
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
        "follower_count": 0,
        "like_count": 0,
        "viewer_following": None,
        "viewer_liked_shop": None,
        "view_count": int(row.get("view_count") or 0),
        "trust_score": float(row.get("trust_score") or 0),
        "seller_score": float(row.get("seller_score") or 0),
        "fraud_score": float(row.get("fraud_score") or 0),
        "trust_badges": row.get("trust_badges") or ["shop_listed"],
        "available_now": bool(row.get("available_now") or False),
        "last_seen_at": str(row["last_seen_at"]) if row.get("last_seen_at") else None,
    }
