from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from postgrest.exceptions import APIError as PostgrestAPIError

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/chat/conversations")
async def admin_list_conversations(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("conversations")
            .select("*, buyer:buyer_id(full_name, email), seller:seller_id(full_name, email)")
            .order("last_message_at", desc=True, nullsfirst=False)
            .limit(limit)
            .execute()
        )
        return {"items": r.data or [], "total": len(r.data or [])}
    except PostgrestAPIError:
        return {"items": [], "total": 0}
    except Exception as exc:
        logger.warning("admin_list_conversations failed: %s", exc)
        return {"items": [], "total": 0}


@router.get("/chat/messages/count")
async def admin_message_count() -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = admin.table("messages").select("id", count="exact").execute()
        return {"count": r.count or 0}
    except PostgrestAPIError:
        return {"count": 0}
    except Exception as exc:
        logger.warning("admin_message_count failed: %s", exc)
        return {"count": 0}
