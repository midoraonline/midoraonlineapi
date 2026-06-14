from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/comments")
async def list_all_comments(
    flagged: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        product = (
            admin.table("product_comments")
            .select("*, product:product_id(title), user:user_id(full_name)")
            .order("created_at", desc=True)
            .limit(limit)
        )
        shop = (
            admin.table("shop_comments")
            .select("*, shop:shop_id(name), user:user_id(full_name)")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if flagged is not None:
            product = product.eq("is_flagged", flagged)
            shop = shop.eq("is_flagged", flagged)
        pr = product.execute()
        sr = shop.execute()
        return {
            "product_comments": pr.data or [],
            "shop_comments": sr.data or [],
        }
    except Exception as exc:
        logger.warning("list_all_comments failed: %s", exc)
        return {"product_comments": [], "shop_comments": []}


@router.patch("/comments/{comment_id}/flag")
async def toggle_comment_flag(comment_id: str, table: str = Query(...)) -> dict[str, Any]:
    if table not in ("product_comments", "shop_comments"):
        return {"error": "Invalid table"}
    admin = get_supabase_admin()
    try:
        current = admin.table(table).select("is_flagged").eq("id", comment_id).execute()
        if not current.data:
            return {"error": "Comment not found"}
        new_flag = not current.data[0]["is_flagged"]
        admin.table(table).update({"is_flagged": new_flag}).eq("id", comment_id).execute()
        return {"status": "updated", "is_flagged": new_flag}
    except Exception as exc:
        logger.warning("toggle_comment_flag failed: %s", exc)
        return {"error": "Failed to update comment"}
