"""Admin: read the audit history."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query

from admin.audit import list_admin_actions
from db.supabase import get_supabase_admin

router = APIRouter()


@router.get("/audit")
def admin_audit_history(
    target_type: Literal["plan", "user", "listing"] | None = Query(None),
    target_id: str | None = Query(None, max_length=80),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    return list_admin_actions(
        get_supabase_admin(), target_type=target_type, target_id=target_id, page=page, limit=limit
    )
