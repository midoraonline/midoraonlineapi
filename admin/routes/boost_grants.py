"""Admin: grant/end free boosts on any listing or business (testing). Admin-only via router dependency."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin import boost_grants as svc
from core.security import TokenPayload, require_admin
from db.supabase import get_supabase_admin

router = APIRouter()


def _guard(fn):
    """Missing 059 columns -> clear 503 instead of a raw 500."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if "granted_by" in str(exc) or "shop_id" in str(exc) or "ended_at" in str(exc) or "boost_plan_id" in str(exc):
            raise HTTPException(
                status_code=503,
                detail={"detail": "Admin boosts need migration 059.", "code": "migration_required"},
            ) from exc
        raise


class GrantBody(BaseModel):
    package_id: str | None = None
    hours: int | None = Field(None, ge=1, le=svc.MAX_HOURS)
    score_bonus: int | None = Field(None, ge=0, le=svc.MAX_SCORE)


def _admin_id(p: TokenPayload | None) -> str | None:
    return p.sub if p is not None and p.sub else None  # None when using X-Admin-Key


AdminDep = Annotated[TokenPayload | None, Depends(require_admin)]


@router.get("/boosts/active")
def active_boosts() -> dict[str, Any]:
    return {"items": _guard(lambda: svc.list_active(get_supabase_admin()))}


@router.post("/listings/{listing_id}/boost")
def grant_listing(listing_id: str, body: GrantBody, who: AdminDep) -> dict[str, Any]:
    admin = get_supabase_admin()
    terms = svc.resolve_terms(admin, body.package_id, body.hours, body.score_bonus)
    return _guard(lambda: svc.boost_listing(admin, listing_id, terms, _admin_id(who)))


@router.delete("/listings/{listing_id}/boost")
def end_listing(listing_id: str) -> dict[str, Any]:
    return _guard(lambda: svc.end_listing_boosts(get_supabase_admin(), listing_id))


@router.post("/shops/{shop_id}/boost")
def grant_shop(shop_id: str, body: GrantBody, who: AdminDep) -> dict[str, Any]:
    admin = get_supabase_admin()
    terms = svc.resolve_terms(admin, body.package_id, body.hours, body.score_bonus)
    return _guard(lambda: svc.boost_shop(admin, shop_id, terms, _admin_id(who)))


@router.delete("/shops/{shop_id}/boost")
def end_shop(shop_id: str) -> dict[str, Any]:
    return _guard(lambda: svc.end_shop_boosts(get_supabase_admin(), shop_id))


@router.post("/boosts/{boost_id}/end")
def end_one(boost_id: str) -> dict[str, Any]:
    return _guard(lambda: svc.end_boost(get_supabase_admin(), boost_id))
