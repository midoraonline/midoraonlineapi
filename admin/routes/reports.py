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


@router.get("/seller-reports")
def list_seller_reports(
    resolved: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        q = admin.table("seller_reports").select("*", count="exact").order("created_at", desc=True).limit(limit)
        if resolved is not None:
            q = q.eq("resolved", resolved)
        offset = (page - 1) * limit
        if offset:
            q = q.offset(offset)
        r = q.execute()
        return {"items": r.data or [], "total": r.count if r.count is not None else len(r.data or [])}
    except Exception as exc:
        logger.warning("list_seller_reports failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}


@router.patch("/seller-reports/{report_id}/resolve")
def resolve_seller_report(report_id: str) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        admin.table("seller_reports").update({"resolved": True}).eq("id", report_id).execute()
        return {"status": "resolved"}
    except Exception as exc:
        logger.warning("resolve_seller_report failed: %s", exc)
        return {"error": "Failed to resolve seller report"}


@router.get("/trust-queue")
def list_trust_queue(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Thin admin queue: reports, near-dupes, and auto-moderation manual review."""
    admin = get_supabase_admin()
    product_reports: list[dict[str, Any]] = []
    seller_reports: list[dict[str, Any]] = []
    near_dupes: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    try:
        pr = (
            admin.table("product_reports")
            .select("id, product_id, reason, description, created_at, resolved, product:product_id(title)")
            .eq("resolved", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        product_reports = pr.data or []
    except Exception as exc:
        logger.warning("trust-queue product_reports failed: %s", exc)
    try:
        sr = (
            admin.table("seller_reports")
            .select("id, seller_id, reason, description, created_at, resolved")
            .eq("resolved", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        seller_reports = sr.data or []
    except Exception as exc:
        logger.warning("trust-queue seller_reports failed: %s", exc)
    try:
        mr = (
            admin.table("listing_moderation_queue")
            .select("id, product_id, seller_id, title, reason, status, created_at, scores")
            .eq("status", "needs_review")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        manual_review = mr.data or []
        near_dupes = [
            row for row in manual_review
            if "near_duplicate" in str(row.get("reason") or "").lower()
        ]
    except Exception as exc:
        logger.warning("trust-queue manual_review failed: %s", exc)
    return {
        "product_reports": product_reports,
        "seller_reports": seller_reports,
        "near_dupes": near_dupes,
        "manual_review": manual_review,
        "counts": {
            "product_reports": len(product_reports),
            "seller_reports": len(seller_reports),
            "near_dupes": len(near_dupes),
            "manual_review": len(manual_review),
        },
    }


@router.get("/near-duplicates")
def list_near_duplicates(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("listing_moderation_queue")
            .select("id, product_id, seller_id, title, reason, status, created_at, scores")
            .eq("status", "needs_review")
            .ilike("reason", "%near_duplicate%")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"items": r.data or [], "total": len(r.data or [])}
    except Exception as exc:
        logger.warning("list_near_duplicates failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}
