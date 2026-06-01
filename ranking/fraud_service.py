from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def flag_suspicious_activity(
    seller_id: str | None = None,
    listing_id: str | None = None,
    event_id: str | None = None,
    flag_type: str = "suspicious_traffic",
    severity: str = "low",
    notes: str | None = None,
) -> dict | None:
    """Create a fraud flag record."""
    admin = get_supabase_admin()
    payload = {
        "seller_id": seller_id,
        "listing_id": listing_id,
        "event_id": event_id,
        "flag_type": flag_type,
        "severity": severity,
        "notes": notes,
    }
    r = admin.table("fraud_flags").insert(payload).execute()
    if not r.data:
        return None
    flag = r.data[0]

    if seller_id:
        _adjust_fraud_score(seller_id)

    return flag


def resolve_fraud_flag(flag_id: str, notes: str | None = None) -> dict | None:
    """Mark a fraud flag as resolved."""
    admin = get_supabase_admin()
    update = {"resolved": True}
    if notes:
        update["notes"] = notes
    r = admin.table("fraud_flags").update(update).eq("id", flag_id).execute()
    return r.data[0] if r.data else None


def list_fraud_flags(
    page: int = 1,
    limit: int = 20,
    resolved: bool | None = None,
    severity: str | None = None,
) -> dict:
    """Paginated list of fraud flags for admin panel."""
    admin = get_supabase_admin()
    limit = min(limit, 100)
    offset = (page - 1) * limit

    q = admin.table("fraud_flags").select("*, users(full_name, email), products(title)", count="exact")
    if resolved is not None:
        q = q.eq("resolved", resolved)
    if severity:
        q = q.eq("severity", severity)

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0

    return {
        "items": r.data or [],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_seller_fraud_history(seller_id: str) -> list[dict]:
    """Get all fraud flags for a seller."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("fraud_flags")
            .select("*")
            .eq("seller_id", seller_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("get_seller_fraud_history(%s) failed: %s", seller_id, exc)
        return []


def _adjust_fraud_score(seller_id: str) -> None:
    """Recalculate fraud_score for all shops owned by a seller."""
    admin = get_supabase_admin()
    try:
        flags_r = (
            admin.table("fraud_flags")
            .select("severity")
            .eq("seller_id", seller_id)
            .eq("resolved", False)
            .execute()
        )
        severity_weights = {"low": 0.1, "medium": 0.3, "high": 0.6, "critical": 1.0}
        total = sum(
            severity_weights.get(f.get("severity", "low"), 0.1)
            for f in (flags_r.data or [])
        )
        new_fraud_score = round(min(total, 5.0), 2)

        shops_r = (
            admin.table("shops")
            .select("id")
            .eq("owner_id", seller_id)
            .execute()
        )
        for shop in (shops_r.data or []):
            sid = shop.get("id")
            if sid:
                admin.table("shops").update({"fraud_score": new_fraud_score}).eq("id", sid).execute()
    except Exception as exc:
        logger.warning("_adjust_fraud_score(%s) failed: %s", seller_id, exc)
