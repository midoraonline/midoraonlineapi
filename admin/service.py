from __future__ import annotations

import logging
from typing import Any

from auth import service as auth_service
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def list_all_shops(page: int = 1, limit: int = 20) -> dict:
    """Paginated admin shop list enriched with verification status, owner
    contact, product count, and engagement — everything the admin UI needs to
    triage a shop without a second round-trip.
    """
    limit = min(limit, 100)
    offset = (page - 1) * limit
    admin = get_supabase_admin()
    r = (
        admin.table("shops")
        .select(
            "id,owner_id,name,slug,shop_type,description,logo_url,"
            "is_active,subscription_end_date,view_count,created_at,updated_at",
            count="exact",
        )
        .range(offset, offset + limit - 1)
        .order("created_at", desc=True)
        .execute()
    )
    rows = r.data or []
    total = r.count if hasattr(r, "count") and r.count is not None else len(rows)
    total_pages = (total + limit - 1) // limit if limit else 0

    shop_ids = [row["id"] for row in rows if row.get("id")]
    owner_ids = list({row["owner_id"] for row in rows if row.get("owner_id")})

    # Verification status for this page (single query, no N+1).
    verifications_by_shop: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            vr = (
                admin.table("shop_verifications")
                .select("shop_id, status, requested_at, reviewed_at, notes")
                .in_("shop_id", shop_ids)
                .execute()
            )
            for v in vr.data or []:
                sid = v.get("shop_id")
                if sid:
                    verifications_by_shop[str(sid)] = v
        except Exception as exc:  # noqa: BLE001
            logger.warning("verification fetch failed: %s", exc)

    owner_by_id: dict[str, dict[str, Any]] = {}
    if owner_ids:
        try:
            ur = (
                admin.table("users")
                .select("id, email, full_name")
                .in_("id", owner_ids)
                .execute()
            )
            for u in ur.data or []:
                owner_by_id[str(u["id"])] = u
        except Exception as exc:  # noqa: BLE001
            logger.warning("owner fetch failed: %s", exc)

    product_count_by_shop: dict[str, int] = {}
    if shop_ids:
        try:
            pr = (
                admin.table("products")
                .select("shop_id")
                .in_("shop_id", shop_ids)
                .execute()
            )
            for p in pr.data or []:
                sid = str(p.get("shop_id"))
                product_count_by_shop[sid] = product_count_by_shop.get(sid, 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("product count fetch failed: %s", exc)

    items: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("id"))
        owner = owner_by_id.get(str(row.get("owner_id") or ""), {})
        v = verifications_by_shop.get(sid)
        items.append(
            {
                **row,
                "owner_email": owner.get("email"),
                "owner_full_name": owner.get("full_name"),
                "product_count": product_count_by_shop.get(sid, 0),
                "verification_status": (v or {}).get("status") or "unverified",
                "verification_requested_at": (v or {}).get("requested_at"),
                "verification_reviewed_at": (v or {}).get("reviewed_at"),
                "verification_notes": (v or {}).get("notes"),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def set_shop_active(shop_id: str, is_active: bool) -> dict | None:
    """Toggle a shop's `is_active` flag. When activating, also ensure the
    owner's `user_role` is at least `merchant` so they can access the
    merchant dashboard immediately.
    """
    admin = get_supabase_admin()
    r = admin.table("shops").update({"is_active": is_active}).eq("id", shop_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    row = r.data[0]
    if is_active and row.get("owner_id"):
        auth_service.promote_to_merchant(str(row["owner_id"]))
    return row


def list_all_subscriptions() -> list:
    admin = get_supabase_admin()
    r = admin.table("subscriptions").select("*").order("created_at", desc=True).execute()
    return r.data or []
