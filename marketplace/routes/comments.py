from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.security import get_current_user_id, get_optional_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


class CommentBody(BaseModel):
    comment: str


@router.get("/products/{product_id}/comments")
async def get_product_comments(product_id: str) -> list[dict[str, Any]]:
    """Get all comments for a product."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("product_comments")
            .select("*, users!product_comments_user_id_fkey(full_name)")
            .eq("product_id", product_id)
            .eq("is_flagged", False)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("get_product_comments(%s) failed: %s", product_id, exc)
        return []


@router.post("/products/{product_id}/comments")
async def create_product_comment(
    product_id: str,
    body: CommentBody,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Add a comment to a product."""
    comment = body.comment
    if not comment or not comment.strip():
        return {"error": "Comment cannot be empty"}
    if len(comment) > 500:
        return {"error": "Comment too long (max 500 characters)"}

    admin = get_supabase_admin()
    try:
        r = admin.table("product_comments").insert({
            "product_id": product_id,
            "user_id": current_user_id,
            "comment": comment.strip(),
        }).execute()
        return r.data[0] if r.data else {"status": "created"}
    except Exception as exc:
        logger.warning("create_product_comment failed: %s", exc)
        return {"error": "Failed to create comment"}


@router.get("/shops/{shop_id}/comments")
async def get_shop_comments(shop_id: str) -> list[dict[str, Any]]:
    """Get all comments for a shop."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("shop_comments")
            .select("*, users!shop_comments_user_id_fkey(full_name)")
            .eq("shop_id", shop_id)
            .eq("is_flagged", False)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("get_shop_comments(%s) failed: %s", shop_id, exc)
        return []


@router.post("/shops/{shop_id}/comments")
async def create_shop_comment(
    shop_id: str,
    body: CommentBody,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Add a comment to a shop."""
    comment = body.comment
    if not comment or not comment.strip():
        return {"error": "Comment cannot be empty"}
    if len(comment) > 500:
        return {"error": "Comment too long (max 500 characters)"}

    admin = get_supabase_admin()
    try:
        r = admin.table("shop_comments").insert({
            "shop_id": shop_id,
            "user_id": current_user_id,
            "comment": comment.strip(),
        }).execute()
        return r.data[0] if r.data else {"status": "created"}
    except Exception as exc:
        logger.warning("create_shop_comment failed: %s", exc)
        return {"error": "Failed to create comment"}
