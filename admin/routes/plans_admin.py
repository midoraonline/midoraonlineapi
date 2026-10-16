"""Admin: edit plans (public.plans) and boost packages (public.boost_plans)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db.supabase import get_supabase_admin
from payments.plans import EDITABLE_FIELDS, PLAN_ORDER, PLANS, invalidate_plans_cache, list_plans

router = APIRouter()

_INT_BOUNDS = {
    "price_ugx": (0, 100_000_000),
    "billing_period_days": (1, 3650),
    "max_shops": (0, 1000),
    "max_products_per_shop": (0, 100_000),
    "frequency_per_day": (0, 1000),
    "frequency_per_day_boosted": (0, 1000),
    "max_photos": (1, 20),
    "max_videos": (0, 5),
    "free_boosts_per_month": (0, 1000),
    "reposts_per_day": (0, 100),
    "repost_cooldown_hours": (0, 24 * 30),
}


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"detail": msg, "code": "invalid_plan"})


def clean_plan_patch(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, value in body.items():
        kind = EDITABLE_FIELDS.get(field)
        if kind is None:
            raise _bad(f"Unknown plan field: {field}.")
        if kind is int:
            if isinstance(value, bool):
                raise _bad(f"{field} must be a whole number.")
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise _bad(f"{field} must be a whole number.")
            lo, hi = _INT_BOUNDS.get(field, (0, 10**9))
            if n < lo or n > hi:
                raise _bad(f"{field} must be between {lo} and {hi}.")
            out[field] = n
        elif kind is bool:
            if not isinstance(value, bool):
                raise _bad(f"{field} must be true or false.")
            out[field] = value
        elif kind is str:
            text = str(value or "").strip()
            if not text or len(text) > 60:
                raise _bad(f"{field} must be 1-60 characters.")
            out[field] = text
        elif kind is list:
            if not isinstance(value, list):
                raise _bad("features must be a list of short lines.")
            lines = [str(v).strip()[:120] for v in value if str(v).strip()]
            out[field] = lines[:20]
    return out


@router.get("/plans")
def admin_list_plans() -> dict[str, Any]:
    invalidate_plans_cache()
    return {"items": list_plans(), "fields": list(EDITABLE_FIELDS)}


@router.patch("/plans/{key}")
def admin_update_plan(key: str, body: dict[str, Any]) -> dict[str, Any]:
    key = key.strip().lower()
    if key not in PLANS:
        raise HTTPException(status_code=404, detail={"detail": "Unknown plan.", "code": "unknown_plan"})
    patch = clean_plan_patch(body)
    if not patch:
        raise _bad("Nothing to update.")
    admin = get_supabase_admin()
    current = next(p for p in list_plans() if p["key"] == key)
    row = {k: current.get(k) for k in EDITABLE_FIELDS}
    row.update(patch)
    row.update({"key": key, "sort_order": PLAN_ORDER.index(key), "updated_at": datetime.now(timezone.utc).isoformat()})
    try:
        admin.table("plans").upsert(row, on_conflict="key").execute()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"detail": "Plans can't be saved until migration 058 is applied.", "code": "migration_required"},
        ) from exc
    invalidate_plans_cache()
    try:
        from platform_settings.flags import invalidate_cache

        invalidate_cache()
    except Exception:  # noqa: BLE001
        pass
    return next(p for p in list_plans() if p["key"] == key)


class BoostPlanBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    duration_hours: int | None = Field(None, ge=1, le=24 * 90)
    price_amount: float | None = Field(None, ge=0, le=100_000_000)
    score_bonus: int | None = Field(None, ge=0, le=500)
    is_active: bool | None = None


@router.get("/boost-plans")
def admin_list_boost_plans() -> dict[str, Any]:
    rows = get_supabase_admin().table("boost_plans").select("*").order("price_amount").execute().data or []
    return {"items": rows}


@router.post("/boost-plans")
def admin_create_boost_plan(body: BoostPlanBody) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    for req in ("name", "duration_hours", "price_amount"):
        if req not in data:
            raise _bad(f"{req} is required.")
    data.setdefault("score_bonus", 50)
    r = get_supabase_admin().table("boost_plans").insert(data).execute()
    return (r.data or [data])[0]


@router.patch("/boost-plans/{plan_id}")
def admin_update_boost_plan(plan_id: str, body: BoostPlanBody) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    if not data:
        raise _bad("Nothing to update.")
    r = get_supabase_admin().table("boost_plans").update(data).eq("id", plan_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail={"detail": "Boost package not found.", "code": "not_found"})
    return r.data[0]
