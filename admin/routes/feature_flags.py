from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.security import TokenPayload, require_admin_role
from platform_settings.flags import FLAGS, admin_items, invalidate_cache, settings_persisted

router = APIRouter()


class SettingPatch(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)
    enabled: bool | None = None
    value: dict[str, Any] | None = None


def _write(body: SettingPatch, updated_by: str | None) -> dict[str, Any]:
    from db.supabase import get_supabase_admin

    if body.key not in FLAGS:
        raise HTTPException(
            status_code=404,
            detail={"detail": "Unknown setting.", "code": "unknown_setting"},
        )
    if body.enabled is None and body.value is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "Send enabled or value.", "code": "setting_empty"},
        )

    admin = get_supabase_admin()
    try:
        current = (
            admin.table("app_settings")
            .select("key,enabled,value")
            .eq("key", body.key)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Platform settings are not available until migration 050 is applied.",
                "code": "migration_required",
            },
        ) from exc

    previous = current.data[0] if current.data else None
    enabled = body.enabled if body.enabled is not None else bool((previous or {}).get("enabled"))
    value = body.value if body.value is not None else (previous or {}).get("value") or {}
    if not isinstance(value, dict):
        value = {}

    row = {
        "key": body.key,
        "enabled": enabled,
        "value": value,
        "updated_by": updated_by,
        "updated_at": _now(),
    }
    audit = {
        "key": body.key,
        "enabled": enabled,
        "value": value,
        "previous_enabled": None if previous is None else bool(previous.get("enabled")),
        "previous_value": (previous or {}).get("value") or {},
        "updated_by": updated_by,
    }
    try:
        admin.table("app_settings_audit").insert(audit).execute()
        if previous:
            admin.table("app_settings").update(row).eq("key", body.key).execute()
        else:
            admin.table("app_settings").insert(row).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Platform settings are not available until migration 050 is applied.",
                "code": "migration_required",
            },
        ) from exc
    invalidate_cache()
    saved = next(item for item in admin_items() if item["key"] == body.key)
    return saved


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@router.get("/settings")
async def list_settings() -> dict[str, Any]:
    return {"items": admin_items(), "persisted": settings_persisted()}


@router.patch("/settings")
async def patch_setting(
    body: SettingPatch,
    claims: TokenPayload | None = Depends(require_admin_role),
) -> dict[str, Any]:
    updated_by = claims.sub if claims is not None else None
    return _write(body, updated_by)
