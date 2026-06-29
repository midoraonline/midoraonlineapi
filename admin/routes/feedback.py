from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from core.security import get_current_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/feedback")
async def list_feedback(
    limit: int = 100,
    current_admin_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """List platform feedback (Admin only)."""
    admin = get_supabase_admin()
    
    # Simple role check (if the dependencies don't do it)
    ur = admin.table("users").select("user_role").eq("id", current_admin_id).limit(1).execute()
    if not ur.data or ur.data[0].get("user_role") != "admin":
        return {"error": "Unauthorized"}
    
    try:
        r = (
            admin.table("platform_feedback")
            .select("id, feedback_text, created_at, user_id, users(full_name, email)")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"items": r.data or []}
    except Exception as exc:
        logger.warning("list_feedback failed: %s", exc)
        return {"items": []}
