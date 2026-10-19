"""Admin: user management (list/search, details, role, suspend, delete)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.account_deletion import DeletionError, delete_account
from core.security import TokenPayload, require_admin_role
from db.supabase import get_supabase_admin

router = APIRouter()
logger = logging.getLogger(__name__)

_USER_COLS = (
    "id, email, full_name, phone_number, phone_verified, email_verified, "
    "user_role, status, created_at, last_seen_at"
)
_ROLES = ("customer", "merchant", "admin", "staff")


def _actor(payload: TokenPayload | None) -> str | None:
    return str(payload.sub) if payload and payload.sub else None


def _clean_search(q: str) -> str:
    # PostgREST or() syntax uses , ( ) as separators; drop them and wildcards.
    return re.sub(r"[,()%*\\]", " ", q).strip()[:80]


def _get_user(client, user_id: str) -> dict[str, Any]:
    rows = client.table("users").select(_USER_COLS).eq("id", user_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    return rows[0]


@router.get("/users")
def admin_list_users(
    q: str | None = Query(None, max_length=120),
    role: str | None = Query(None),
    verified: Literal["email", "phone", "unverified"] | None = Query(None),
    status: Literal["active", "suspended", "blocked", "deleted"] | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    client = get_supabase_admin()
    offset = (page - 1) * limit
    query = client.table("users").select(_USER_COLS, count="exact")
    if q and (term := _clean_search(q)):
        query = query.or_(
            f"email.ilike.%{term}%,full_name.ilike.%{term}%,phone_number.ilike.%{term}%"
        )
    if role in _ROLES:
        query = query.eq("user_role", role)
    if verified == "email":
        query = query.eq("email_verified", True)
    elif verified == "phone":
        query = query.eq("phone_verified", True)
    elif verified == "unverified":
        query = query.eq("email_verified", False).eq("phone_verified", False)
    if status:
        query = query.eq("status", status)
    else:
        query = query.neq("status", "deleted")
    result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    total = result.count if getattr(result, "count", None) is not None else len(result.data or [])
    return {
        "items": result.data or [],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit if limit else 0,
    }


@router.get("/users/{user_id}")
def admin_get_user(user_id: str) -> dict[str, Any]:
    client = get_supabase_admin()
    user = _get_user(client, user_id)
    shops: list[dict] = []
    listings = 0
    try:
        shops = (
            client.table("shops")
            .select("id, name, slug, is_active, logo_url, trust_badges, created_at")
            .eq("owner_id", user_id)
            .execute()
            .data
            or []
        )
        shop_ids = [s["id"] for s in shops]
        if shop_ids:
            res = client.table("products").select("id", count="exact").in_("shop_id", shop_ids).limit(1).execute()
            listings = res.count or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin user details: shops/listings lookup failed: %s", exc)
    profile: dict[str, Any] = {}
    try:
        rows = client.table("profiles").select("avatar_url").eq("id", user_id).limit(1).execute().data or []
        profile = rows[0] if rows else {}
    except Exception:  # noqa: BLE001
        pass
    badges = {b for s in shops for b in (s.get("trust_badges") or [])}
    return {
        "user": {**user, "avatar_url": profile.get("avatar_url")},
        "shops": shops,
        "listings_count": listings,
        "verification": {
            "email": bool(user.get("email_verified")),
            "phone": bool(user.get("phone_verified")),
            "identity": "identity_verified" in badges,
            "business": "business_verified" in badges,
        },
    }


class RoleBody(BaseModel):
    role: Literal["customer", "merchant", "admin", "staff"]


@router.patch("/users/{user_id}/role")
def admin_change_role(
    user_id: str,
    body: RoleBody,
    payload: TokenPayload | None = Depends(require_admin_role),
) -> dict[str, Any]:
    if user_id == _actor(payload):
        raise HTTPException(status_code=400, detail={"detail": "You can't change your own role.", "code": "self_action"})
    client = get_supabase_admin()
    user = _get_user(client, user_id)
    if user.get("status") == "deleted":
        raise HTTPException(status_code=409, detail="User is deleted")
    client.table("users").update({"user_role": body.role}).eq("id", user_id).execute()
    try:
        client.table("profiles").update({"user_role": body.role}).eq("id", user_id).execute()
    except Exception:  # noqa: BLE001
        pass
    logger.info("admin %s set role of %s to %s", _actor(payload), user_id, body.role)
    return _get_user(client, user_id)


class StatusBody(BaseModel):
    status: Literal["active", "suspended"]


@router.post("/users/{user_id}/status")
def admin_set_status(
    user_id: str,
    body: StatusBody,
    payload: TokenPayload | None = Depends(require_admin_role),
) -> dict[str, Any]:
    if user_id == _actor(payload):
        raise HTTPException(status_code=400, detail={"detail": "You can't suspend yourself.", "code": "self_action"})
    client = get_supabase_admin()
    user = _get_user(client, user_id)
    if user.get("status") == "deleted":
        raise HTTPException(status_code=409, detail="User is deleted")
    now = datetime.now(timezone.utc).isoformat()
    update: dict[str, Any] = {"status": body.status}
    try:
        client.table("users").update(
            {**update, "suspended_at": now if body.status == "suspended" else None}
        ).eq("id", user_id).execute()
    except Exception:  # suspended_at missing until migration 056
        client.table("users").update(update).eq("id", user_id).execute()
    if body.status == "suspended":
        client.table("refresh_tokens").update({"revoked_at": now}).eq("user_id", user_id).is_(
            "revoked_at", "null"
        ).execute()
    logger.info("admin %s set status of %s to %s", _actor(payload), user_id, body.status)
    return _get_user(client, user_id)


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    payload: TokenPayload | None = Depends(require_admin_role),
) -> dict[str, Any]:
    if user_id == _actor(payload):
        raise HTTPException(
            status_code=400,
            detail={"detail": "Delete your own account from Settings.", "code": "self_action"},
        )
    try:
        summary = await delete_account(user_id, actor_id=_actor(payload))
    except DeletionError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "not_found" else 409,
            detail={"detail": str(exc), "code": exc.code},
        ) from exc
    return {"message": "User deleted", "summary": summary}
