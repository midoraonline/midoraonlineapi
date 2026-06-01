from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/reports")
def list_reports(
    resolved: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        count_q = (
            admin.table("product_reports")
            .select("id", count="exact")
        )
        if resolved is not None:
            count_q = count_q.eq("resolved", resolved)
        count_r = count_q.limit(1).execute()
        total = count_r.count if count_r.count is not None else 0

        query = (
            admin.table("product_reports")
            .select("*, product:product_id(title, shop_id), reporter:reporter_id(full_name)")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if resolved is not None:
            query = query.eq("resolved", resolved)
        offset = (page - 1) * limit
        if offset:
            query = query.offset(offset)
        r = query.execute()
        return {"items": r.data or [], "total": total}
    except Exception as exc:
        logger.warning("list_reports failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}


@router.patch("/reports/{report_id}/resolve")
def resolve_report(report_id: str) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        admin.table("product_reports").update({"resolved": True}).eq("id", report_id).execute()
        return {"status": "resolved"}
    except Exception as exc:
        logger.warning("resolve_report failed: %s", exc)
        return {"error": "Failed to resolve report"}
